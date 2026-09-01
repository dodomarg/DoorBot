"""Lock control layer.

Two interchangeable backends:

* ``MockBackend``    - a physics-lite simulation of a Feetech SMS/STS bus servo on a
  deadbolt. It models travel time, load rising near the end stops and jamming,
  so the whole add-on (calibration wizard included) can be exercised with no
  hardware attached.
* ``EsphomeBackend`` - drives a real XIAO ESP32S3 node through the Home
  Assistant Core API.

Both expose the same small surface used by the web API.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from .db import Database
from .hass import HassClient, HassError

# Servo raw units. SMS/STS servos use a 12-bit encoder: 0..4095 over 360 degrees.
RESOLUTION = 4096
# With both angle limits set to 0 the servo accepts multi-turn goals over this
# range instead of a single revolution (official ST3215 register map, reg 0x2A).
MULTITURN_MIN = -30719
MULTITURN_MAX = 30719
STATE_LOCKED = "locked"
STATE_UNLOCKED = "unlocked"
STATE_LOCKING = "locking"
STATE_UNLOCKING = "unlocking"
STATE_JAMMED = "jammed"
STATE_UNKNOWN = "unknown"

# The locked and unlocked points must be at least this far apart (raw steps,
# ~5 degrees) before the calibration is considered usable.
MIN_TRAVEL = 60
# How far off the commanded position the servo may settle and still count as
# having arrived. Matches the firmware's `tolerance` option.
MOVE_TOLERANCE = 25


def raw_to_degrees(raw: int) -> float:
    """Angle within the current revolution, 0..359.9.

    In multi-turn mode a position can be several revolutions away from zero, so
    the naive ``raw * 360 / 4096`` produces angles like 527deg that are not
    angles at all. The shaft angle is the remainder; the revolution count is
    reported separately by ``raw_to_turns``.
    """
    return round((raw % RESOLUTION) * 360.0 / RESOLUTION, 1)


def raw_to_turns(raw: int) -> float:
    """Signed revolutions from zero. 0.0 in single-turn mode's usual range."""
    return round(raw / RESOLUTION, 2)


def travel_limits(multi_turn: bool) -> tuple[int, int]:
    """Position bounds for the current travel mode."""
    if multi_turn:
        return MULTITURN_MIN, MULTITURN_MAX
    return 0, RESOLUTION - 1


def direction_sign(cal: dict[str, Any]) -> int:
    """+1 when locking increases the step count, -1 when it decreases.

    The two captured end points are the single source of truth for which way
    the lock turns. Everything directional -- the overshoot push, the hold-open
    point, the jog arrows -- is derived from this rather than configured
    separately, so the two can never disagree.
    """
    return 1 if int(cal.get("locked_position", 0)) >= int(cal.get("unlocked_position", 0)) else -1


def direction_label(cal: dict[str, Any]) -> str:
    """How the locking turn reads to a person watching the output shaft.

    A Feetech servo counts up clockwise as seen from the horn, but a mirrored
    mount or an extra gear reverses what the user actually sees, which is what
    the `invert` flag records.
    """
    clockwise = direction_sign(cal) > 0
    if cal.get("invert"):
        clockwise = not clockwise
    return "clockwise" if clockwise else "counter-clockwise"


def hold_position_is_valid(cal: dict[str, Any]) -> bool:
    """True when the hold-open point lies beyond unlocked, away from locked.

    Holding the latch means turning further in the unlocking direction. A value
    on the locked side would drive the bolt back out while reporting that the
    door is being held open for you.
    """
    if int(cal.get("hold_seconds") or 0) <= 0:
        return True
    unlocked = int(cal.get("unlocked_position", 0))
    hold = int(cal.get("hold_position", unlocked))
    return (hold - unlocked) * direction_sign(cal) <= 0


class BackendError(RuntimeError):
    pass


class BaseBackend:
    name = "base"

    def status(self) -> dict[str, Any]:
        raise NotImplementedError

    def move_to(self, position: int, speed: int | None = None) -> None:
        raise NotImplementedError

    def set_torque(self, enabled: bool) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def set_multi_turn(self, enabled: bool) -> None:
        """Widen or narrow the travel range. Mock backends model it locally."""
        return None


class MockBackend(BaseBackend):
    """Simulated Feetech bus servo so the add-on is fully testable without hardware."""

    name = "mock"

    def __init__(self, calibration: dict[str, Any]) -> None:
        self._lock = threading.RLock()
        self._position = float(calibration.get("locked_position", 2048))
        self._goal = self._position
        self._speed = float(calibration.get("speed", 800) or 800)
        self._torque = True
        # Lets the test suite and the dev panel reproduce an unpowered or
        # miswired servo, which is otherwise impossible to exercise in mock mode.
        self.offline = False
        self._last = time.monotonic()
        self._load = 0.0
        self._voltage = 12.1
        self._temperature = 31.0
        # A simulated mechanical hard stop just past each calibrated end point,
        # so "drive until it stalls" behaves like a real deadbolt.
        self._stop_low = 0.0
        self._stop_high = float(RESOLUTION - 1)
        self._multi_turn = bool(calibration.get("multi_turn"))
        # Mirrors the firmware's MoveResult so the UI can show the same words
        # whether it is talking to a simulation or to a real servo.
        self._move_result = "idle"
        self.jam_next_move = False
        self._jammed = False
        # Simulates the servo's overload protection cutting output during a
        # hold, which is the realistic failure mode of hold-open.
        self._slip_armed_move = -1
        self._slipped = False
        self._on_goal_since = 0.0
        self._move_seq = 0

    @property
    def slip_next_hold(self) -> bool:
        return self._slip_armed_move >= 0

    @slip_next_hold.setter
    def slip_next_hold(self, enabled: bool) -> None:
        # Armed against the *next* move, so arming it while the servo is
        # already holding does not consume it on the hold in progress.
        self._slip_armed_move = self._move_seq if enabled else -1

    def set_hard_stops(self, low: float, high: float) -> None:
        with self._lock:
            self._stop_low, self._stop_high = min(low, high), max(low, high)

    def set_multi_turn(self, enabled: bool) -> None:
        with self._lock:
            self._multi_turn = bool(enabled)

    # ------------------------------------------------------------- internals
    def _tick(self) -> None:
        now = time.monotonic()
        dt = max(0.0, now - self._last)
        self._last = now
        if dt == 0:
            return

        if not self._torque:
            self._load *= 0.5
            return

        # Goal velocity is in steps/second-ish; scale for realism.
        step = self._speed * dt
        delta = self._goal - self._position
        moving = abs(delta) > 1.0

        if moving:
            self._position += max(-step, min(step, delta))
            # Clamp against the simulated mechanical hard stops.
            if self._position <= self._stop_low:
                self._position = self._stop_low
                self._load = 900.0
            elif self._position >= self._stop_high:
                self._position = self._stop_high
                self._load = 900.0
            else:
                self._load = 260.0 + 40.0 * (abs(delta) / 100.0)
            if self.jam_next_move:
                self._load = 980.0
                self._jammed = True
                self._move_result = "jammed"
                self._goal = self._position
        else:
            self._load *= 0.6
            if self._move_result == "moving":
                self._move_result = "arrived"

        self._temperature = min(60.0, 30.0 + self._load / 60.0)
        self._voltage = 12.2 - self._load / 4000.0

        # Overload protection only trips on a *sustained* hold, so a move that
        # merely passes through a position is unaffected - same as the real
        # servo, where Protection time has to elapse first.
        if self._torque and abs(self._goal - self._position) <= 25.0:
            if self._on_goal_since == 0.0:
                self._on_goal_since = now
            elif (
                self._slip_armed_move >= 0
                and self._move_seq > self._slip_armed_move
                and now - self._on_goal_since >= 0.5
            ):
                self._slip_armed_move = -1
                self._slipped = True
        else:
            self._on_goal_since = 0.0

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._tick()
            return {
                "online": not self.offline,
                "position": int(round(self._position)),
                "goal": int(round(self._goal)),
                "degrees": raw_to_degrees(int(round(self._position))),
                "load": int(round(self._load)),
                "voltage": round(self._voltage, 1),
                "temperature": round(self._temperature, 1),
                "torque": self._torque,
                "moving": abs(self._goal - self._position) > 1.0,
                "jammed": self._jammed,
                # Holding means torque is on AND we are actually sitting on the
                # goal - the same test the firmware performs over the bus.
                "holding": not self._slipped
                and self._torque
                and abs(self._goal - self._position) <= 25.0
                and not self._jammed,
                "move_result": self._move_result,
                "multi_turn": self._multi_turn,
                "turns": raw_to_turns(int(round(self._position))),
            }

    def move_to(self, position: int, speed: int | None = None) -> None:
        with self._lock:
            if self.offline:
                # A servo that is not on the bus swallows the command silently,
                # exactly as the real one does.
                return
            self._tick()
            if speed:
                self._speed = float(speed)
            self._torque = True
            low, high = travel_limits(self._multi_turn)
            self._goal = float(max(low, min(high, int(position))))
            self._jammed = False
            self._slipped = False
            self._on_goal_since = 0.0
            self._move_seq += 1
            self._move_result = "moving"

    def set_torque(self, enabled: bool) -> None:
        with self._lock:
            self._tick()
            self._torque = bool(enabled)
            if not enabled:
                self._goal = self._position
                self._move_result = "idle"

    def stop(self) -> None:
        with self._lock:
            self._tick()
            self._goal = self._position

    def nudge_by_hand(self, delta: int) -> None:
        """Simulate someone turning the thumbturn while torque is released."""
        with self._lock:
            self._tick()
            if self._torque:
                raise BackendError(
                    "Release the servo (torque off) before turning it by hand."
                )
            self._position = max(
                self._stop_low, min(self._stop_high, self._position + delta)
            )
            self._goal = self._position


class EsphomeBackend(BaseBackend):
    """Drives the real device via Home Assistant entities exposed by ESPHome."""

    name = "esphome"

    def __init__(self, hass: HassClient, options: dict[str, Any]) -> None:
        self.hass = hass
        self.options = options

    def _num(self, entity: str) -> float | None:
        state = self.hass.state(entity)
        if not state:
            return None
        try:
            return float(state["state"])
        except (TypeError, ValueError):
            return None

    def status(self) -> dict[str, Any]:
        position = self._num(self.options.get("position_sensor_entity", ""))
        load = self._num(self.options.get("load_sensor_entity", ""))
        torque_state = self.hass.state(self.options.get("torque_switch_entity", ""))
        # Prefer the firmware's own ping result. Inferring reachability from
        # "did the position sensor parse as a number" conflates three different
        # failures -- servo unpowered, ESP32 offline, entity misnamed -- and
        # reports the first two as a position of 0, which reads as a real
        # measurement rather than as missing data.
        online_entity = self.options.get("online_binary_sensor_entity", "")
        online_state = self.hass.state(online_entity) if online_entity else None
        if online_state is not None:
            online = online_state.get("state") == "on"
        else:
            online = position is not None
        return {
            "online": online,
            "position": int(position) if position is not None else 0,
            "goal": int(self._num(self.options.get("position_number_entity", "")) or 0),
            "degrees": raw_to_degrees(int(position)) if position is not None else 0.0,
            "turns": raw_to_turns(int(position)) if position is not None else 0.0,
            "load": int(load) if load is not None else 0,
            "voltage": self._num(self.options.get("voltage_sensor_entity", "")) or 0.0,
            "temperature": self._num(self.options.get("temperature_sensor_entity", "")) or 0.0,
            "torque": bool(torque_state and torque_state.get("state") == "on"),
            "moving": self._is_on(self.options.get("moving_binary_sensor_entity", "")),
            # These come straight from the firmware, which verifies them against
            # the servo rather than assuming the last command worked.
            "holding": self._is_on(self.options.get("holding_binary_sensor_entity", "")),
            "move_result": self._text(self.options.get("move_result_entity", "")) or "idle",
            "jammed": self._text(self.options.get("move_result_entity", "")) == "jammed",
        }

    def _is_on(self, entity: str) -> bool:
        state = self.hass.state(entity) if entity else None
        return bool(state and state.get("state") == "on")

    def _text(self, entity: str) -> str:
        state = self.hass.state(entity) if entity else None
        return str(state.get("state")) if state else ""

    def move_to(self, position: int, speed: int | None = None) -> None:
        entity = self.options.get("position_number_entity")
        if not entity:
            raise BackendError("No target-position entity configured.")
        self.hass.call_service(
            "number", "set_value", entity_id=entity, value=int(position)
        )

    def set_torque(self, enabled: bool) -> None:
        entity = self.options.get("torque_switch_entity")
        if not entity:
            raise BackendError("No torque switch entity configured.")
        self.hass.call_service(
            "switch", "turn_on" if enabled else "turn_off", entity_id=entity
        )

    def stop(self) -> None:
        status = self.status()
        self.move_to(status["position"])

    def set_multi_turn(self, enabled: bool) -> None:
        # The firmware owns the servo's EEPROM; ask it to re-apply the range.
        self.hass.call_service(
            "esphome",
            "doorbot_configure_servo",
            multi_turn=bool(enabled),
            torque_limit=int(self.options.get("torque_limit", 700)),
            max_torque=int(self.options.get("max_torque", 1000)),
        )


class LockController:
    """High-level lock behaviour built on top of a backend."""

    def __init__(self, db: Database, backend: BaseBackend, options: dict[str, Any]) -> None:
        self.db = db
        self.backend = backend
        self.options = options
        self._lock = threading.RLock()
        self._state = STATE_UNKNOWN
        self._auto_lock_timer: threading.Timer | None = None
        # Bumped by every command that supersedes an in-flight hold-open.
        self._hold_generation = 0
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._sync_hard_stops()
        self._state = self._infer_state()

    # ------------------------------------------------------------- plumbing
    def _sync_hard_stops(self) -> None:
        cal = self.db.get_calibration()
        # The firmware owns the servo's real travel range, so tell it about
        # multi-turn regardless of backend; the simulated stops below are only
        # meaningful for the mock.
        self.backend.set_multi_turn(bool(cal.get("multi_turn")))
        if isinstance(self.backend, MockBackend):
            low, high = travel_limits(bool(cal.get("multi_turn")))
            if not cal.get("calibrated"):
                # Nothing learned yet - let the simulated bolt travel freely so
                # the calibration wizard can reach any position.
                self.backend.set_hard_stops(low, high)
                return
            lo = min(cal["locked_position"], cal["unlocked_position"])
            hi = max(cal["locked_position"], cal["unlocked_position"])
            # Generous margin: a real deadbolt has a little slack past each end,
            # and this must never box in a later re-calibration.
            margin = max(150, int(abs(hi - lo) * 0.25))
            # A hold-open point sits past the unlocked end, so the simulated
            # stops have to leave room for it or the hold would look like a jam.
            hold = int(cal.get("hold_position") or 0)
            if int(cal.get("hold_seconds") or 0) > 0:
                lo, hi = min(lo, hold), max(hi, hold)
            self.backend.set_hard_stops(max(low, lo - margin), min(high, hi + margin))

    # move_result values that mean the firmware has finished with the move.
    TERMINAL_RESULTS = frozenset(
        {"arrived", "jammed", "timeout", "stalled", "aborted", "offline"}
    )

    def _settle(self, timeout: float = 12.0) -> dict[str, Any]:
        """Block until the servo finishes the move (or the timeout expires)."""
        deadline = time.monotonic() + timeout
        # A command that has just been sent has not reached the servo yet, so
        # "not moving" at this instant means "not started", not "finished".
        # Waiting for a terminal move_result avoids that race; the moving flag
        # is only the fallback for backends that do not report one.
        started = False
        servo = self.backend.status()
        while time.monotonic() < deadline:
            result = servo.get("move_result", "")
            if result:
                if result == "moving":
                    started = True
                elif started or result in self.TERMINAL_RESULTS:
                    break
            elif servo.get("moving"):
                started = True
            elif started:
                break
            elif time.monotonic() > deadline - timeout + 0.5:
                # No feedback at all after half a second: nothing is coming.
                break
            time.sleep(0.02)
            servo = self.backend.status()
        return servo

    def on_event(self, fn: Callable[[str, dict[str, Any]], None]) -> None:
        self._listeners.append(fn)

    def _emit(self, kind: str, data: dict[str, Any]) -> None:
        for fn in list(self._listeners):
            try:
                fn(kind, data)
            except Exception:  # noqa: BLE001 - listeners must never break control
                pass

    # ---------------------------------------------------------------- state
    def _infer_state(self) -> str:
        cal = self.db.get_calibration()
        if not cal.get("calibrated"):
            return STATE_UNKNOWN
        pos = self.backend.status()["position"]
        d_locked = abs(pos - cal["locked_position"])
        d_unlocked = abs(pos - cal["unlocked_position"])
        travel = max(1, abs(cal["locked_position"] - cal["unlocked_position"]))
        tolerance = max(25, int(travel * 0.15))
        if d_locked <= tolerance:
            return STATE_LOCKED
        if d_unlocked <= tolerance:
            return STATE_UNLOCKED
        return STATE_UNKNOWN

    def status(self) -> dict[str, Any]:
        with self._lock:
            servo = self.backend.status()
            cal = self.db.get_calibration()
            if not servo.get("online", True):
                # No servo on the bus means no trustworthy position, so the
                # lock state is genuinely unknown rather than "wherever it was".
                self._state = STATE_UNKNOWN
            elif servo.get("jammed"):
                self._state = STATE_JAMMED
            elif not servo.get("moving") and self._state != STATE_JAMMED:
                # A jam stays visible until a movement actually succeeds.
                self._state = self._infer_state()
            return {
                "state": self._state,
                "backend": self.backend.name,
                "calibrated": bool(cal.get("calibrated")),
                "servo": servo,
                "calibration": cal,
                "direction": {
                    "sign": direction_sign(cal),
                    "locking": direction_label(cal),
                    "unlocking": (
                        "counter-clockwise"
                        if direction_label(cal) == "clockwise"
                        else "clockwise"
                    ),
                    "hold_valid": hold_position_is_valid(cal),
                },
                "auto_lock_pending": self._auto_lock_timer is not None,
            }

    # ------------------------------------------------------------- movement
    def _require_online(self, actor: str = "system") -> dict[str, Any]:
        """Return the servo status, refusing to proceed if it is not answering.

        Every path that commands motion goes through here. Without it a missing
        servo looks identical to a successful move, because nothing downstream
        distinguishes "reported position 0" from "no reading at all".
        """
        servo = self.backend.status()
        if not servo.get("online", True):
            self._state = STATE_UNKNOWN
            self.db.log("offline", "Refused to move: the servo is not responding", actor=actor)
            self._emit("offline", {"actor": actor})
            raise BackendError(
                "The servo is not responding - check that it is powered, wired to "
                "the driver board, and set to the configured servo id."
            )
        return servo

    def _drive(self, position: int, speed: int | None, actor: str) -> dict[str, Any]:
        """Move to a position, wait for it to settle, and verify it got there."""
        # Refuse to command a servo that is not answering. Without this the move
        # is sent into the void, and the drift check below then reports it as
        # "stopped N steps short", which blames the mechanism for what is really
        # a wiring, power or configuration fault.
        self._require_online(actor)

        self.backend.move_to(position, speed)
        servo = self._settle()
        result = servo.get("move_result", "")
        if not servo.get("online", True):
            self._state = STATE_UNKNOWN
            self.db.log("offline", "The servo stopped responding mid-move", actor=actor)
            self._emit("offline", {"actor": actor})
            raise BackendError("The servo stopped responding while moving.")
        if servo.get("jammed") or result in ("jammed", "timeout"):
            self._state = STATE_JAMMED
            reason = "jammed" if result != "timeout" else "timed out"
            self.db.log("jammed", f"The lock {reason} while moving", actor=actor)
            self._emit("jammed", {"actor": actor, "result": result})
            raise BackendError(f"The lock {reason} - check the mechanism.")
        # The move claims to be done; confirm the servo is really sitting there
        # rather than trusting that the command was obeyed.
        drift = abs(int(servo.get("position", 0)) - int(position))
        if drift > MOVE_TOLERANCE:
            self.db.log(
                "jammed",
                f"Stopped {drift} steps short of {position}",
                actor=actor,
                position=servo.get("position"),
            )
            self._emit("jammed", {"actor": actor, "result": "short"})
            raise BackendError(
                f"The lock stopped {drift} steps short of where it was sent."
            )
        return servo

    def _travel(self, target_key: str, end_state: str, actor: str) -> dict[str, Any]:
        cal = self.db.get_calibration()
        if not cal.get("calibrated"):
            raise BackendError(
                "The lock is not calibrated yet - run the calibration wizard first."
            )

        target = int(cal[target_key])
        speed = cal.get("speed")
        self._state = STATE_LOCKING if end_state == STATE_LOCKED else STATE_UNLOCKING

        overshoot = int(cal.get("overshoot") or 0)
        if overshoot:
            other = (
                cal["unlocked_position"]
                if target_key == "locked_position"
                else cal["locked_position"]
            )
            direction = 1 if target >= other else -1
            low, high = travel_limits(bool(cal.get("multi_turn")))
            push = max(low, min(high, target + direction * overshoot))
            self._drive(push, speed, actor)
            time.sleep(min(2.0, (cal.get("hold_ms") or 0) / 1000.0))

        self._drive(target, speed, actor)

        self._state = end_state
        self.db.log(end_state, f"Lock {end_state}", actor=actor)
        self._emit(end_state, {"actor": actor})
        self._schedule_auto_lock(end_state)
        return self.status()

    def lock(self, actor: str = "ui") -> dict[str, Any]:
        with self._lock:
            self._hold_generation += 1
            self._cancel_auto_lock()
            return self._travel("locked_position", STATE_LOCKED, actor)

    def unlock(self, actor: str = "ui") -> dict[str, Any]:
        with self._lock:
            self._hold_generation += 1
            return self._travel("unlocked_position", STATE_UNLOCKED, actor)

    def open(self, actor: str = "ui") -> dict[str, Any]:
        """Unlock. The hold-open latch is retired -- see the note below.

        This used to drive past "unlocked" to a hold point and keep the servo
        energised there so a passive outside handle could push the door. That
        requires the one thing the safety policy forbids: sustained torque with
        nobody watching. A servo holding the latch is a servo that cannot be
        overridden by hand, and a fault during the hold strands the door.

        The firmware now releases torque at the end of every move, so a hold
        cannot be sustained anyway -- keeping this code would only produce a
        control that reports success while doing nothing.
        """
        with self._lock:
            cal = self.db.get_calibration()
            if int(cal.get("hold_seconds") or 0) > 0:
                self.db.log(
                    "hold_retired",
                    "Hold-open is retired: the servo is never left holding "
                    "torque. Unlocking normally instead.",
                    actor=actor,
                )
        return self.unlock(actor=actor)

    def toggle(self, actor: str = "ui") -> dict[str, Any]:
        return self.lock(actor) if self._state != STATE_LOCKED else self.unlock(actor)

    # --------------------------------------------------------- auto-locking
    def _cancel_auto_lock(self) -> None:
        if self._auto_lock_timer is not None:
            self._auto_lock_timer.cancel()
            self._auto_lock_timer = None

    def _schedule_auto_lock(self, state: str) -> None:
        self._cancel_auto_lock()
        seconds = int(self.db.get_calibration().get("auto_lock_seconds") or 0)
        if state != STATE_UNLOCKED or seconds <= 0:
            return

        def fire() -> None:
            self._auto_lock_timer = None
            try:
                self.lock(actor="auto-lock")
            except BackendError:
                pass

        timer = threading.Timer(seconds, fire)
        timer.daemon = True
        self._auto_lock_timer = timer
        timer.start()

    # --------------------------------------------------------- calibration
    def jog(self, delta: int) -> dict[str, Any]:
        with self._lock:
            servo = self._require_online("ui")
            cal = self.db.get_calibration()
            # The arrows are labelled by what the user sees at the thumbturn, so
            # a mirrored mount has to flip the raw step delta to match. Without
            # this, "invert" was a checkbox that changed nothing at all.
            if cal.get("invert"):
                delta = -delta
            if not servo["torque"] and isinstance(self.backend, MockBackend):
                self.backend.nudge_by_hand(delta)
            else:
                self.backend.move_to(servo["position"] + delta, 400)
                self._settle(5.0)
            return self.status()

    def goto(self, position: int) -> dict[str, Any]:
        with self._lock:
            self._require_online("ui")
            cal = self.db.get_calibration()
            low, high = travel_limits(bool(cal.get("multi_turn")))
            target = max(low, min(high, int(position)))
            self.backend.move_to(target, cal.get("speed"))
            self._settle(8.0)
            return self.status()

    def set_torque(self, enabled: bool) -> dict[str, Any]:
        with self._lock:
            self._require_online("ui")
            self.backend.set_torque(enabled)
            return self.status()

    def capture(self, which: str) -> dict[str, Any]:
        """Store the current physical position as the locked/unlocked point."""
        if which not in ("locked", "unlocked"):
            raise BackendError("Capture either 'locked' or 'unlocked'.")
        with self._lock:
            self._require_online("ui")
            position = self._settle(8.0)["position"]
            cal = self.db.save_calibration({f"{which}_position": position})
            travel = abs(cal["locked_position"] - cal["unlocked_position"])
            cal = self.db.save_calibration({"calibrated": travel >= MIN_TRAVEL})
            # Re-capturing an end point can reverse which way the lock turns,
            # which can strand a previously valid hold-open point on the locked
            # side. Retire it rather than leave it pointing the wrong way.
            if not hold_position_is_valid(cal):
                cal = self.db.save_calibration(
                    {"hold_position": int(cal["unlocked_position"])}
                )
                self.db.log(
                    "calibration",
                    "Reset the hold-open position: the direction of rotation changed",
                    actor="ui",
                )
            self._sync_hard_stops()
            self.db.log(
                "calibration",
                f"Captured {which} position at {position} ({raw_to_degrees(position)}deg)",
                actor="ui",
                position=position,
                travel=travel,
            )
            status = self.status()
            if travel < MIN_TRAVEL:
                status["warning"] = (
                    "The locked and unlocked positions are only "
                    f"{travel} steps apart. Move the servo further and capture again."
                )
            return status

    def save_calibration(self, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            before = self.db.get_calibration()
            allowed = {
                k: v
                for k, v in values.items()
                if k
                in {
                    "locked_position",
                    "unlocked_position",
                    "overshoot",
                    "hold_ms",
                    "hold_position",
                    "hold_seconds",
                    "multi_turn",
                    "speed",
                    "acceleration",
                    "torque_limit",
                    "stall_load",
                    "invert",
                    "servo_id",
                    "baud",
                    "auto_lock_seconds",
                    "calibrated",
                }
            }
            for key in (
                "locked_position",
                "unlocked_position",
                "overshoot",
                "hold_ms",
                "hold_position",
                "hold_seconds",
                "speed",
                "acceleration",
                "torque_limit",
                "stall_load",
                "servo_id",
                "baud",
                "auto_lock_seconds",
            ):
                if key in allowed:
                    allowed[key] = int(allowed[key])
            for key in ("invert", "multi_turn"):
                if key in allowed:
                    allowed[key] = bool(allowed[key])
            cal = self.db.save_calibration(allowed)
            # A hold-open point on the wrong side of the unlocked position would
            # drive the bolt back out while reporting the door as held open, so
            # it is rejected rather than clamped.
            if not hold_position_is_valid(cal):
                self.db.save_calibration(before)
                unlocking = (
                    "counter-clockwise"
                    if direction_label(cal) == "clockwise"
                    else "clockwise"
                )
                raise BackendError(
                    f"The hold-open position ({cal.get('hold_position')}) is on the "
                    f"locked side of the unlocked position "
                    f"({cal.get('unlocked_position')}). Holding the latch means "
                    f"turning further {unlocking}, the same way unlocking turns."
                )
            # Editing the end points by hand must re-check that they are usable.
            if "locked_position" in allowed or "unlocked_position" in allowed:
                travel = abs(cal["locked_position"] - cal["unlocked_position"])
                cal = self.db.save_calibration({"calibrated": travel >= MIN_TRAVEL})
            self._sync_hard_stops()
            self.db.log("calibration", "Calibration saved", actor="ui")
            return self.status()

    def reset_calibration(self) -> dict[str, Any]:
        with self._lock:
            self.db.set_setting("calibration", None)
            self.db.save_calibration({"calibrated": False})
            self._sync_hard_stops()
            self.db.log("calibration", "Calibration reset", actor="ui")
            return self.status()

    def swap_direction(self) -> dict[str, Any]:
        """Reverse which way the lock turns by swapping the two end points.

        The usual reason to need this is capturing the end points in the wrong
        order: the travel is right but every move goes the wrong way. Swapping
        is preferable to re-running the wizard, and mirroring the hold-open
        point about the new unlocked position keeps it the same distance past
        the end rather than silently discarding it.
        """
        with self._lock:
            cal = self.db.get_calibration()
            locked, unlocked = int(cal["locked_position"]), int(cal["unlocked_position"])
            values: dict[str, Any] = {
                "locked_position": unlocked,
                "unlocked_position": locked,
            }
            if int(cal.get("hold_seconds") or 0) > 0:
                past_end = int(cal.get("hold_position", unlocked)) - unlocked
                low, high = travel_limits(bool(cal.get("multi_turn")))
                values["hold_position"] = max(low, min(high, locked - past_end))
            cal = self.db.save_calibration(values)
            if not hold_position_is_valid(cal):
                cal = self.db.save_calibration(
                    {"hold_position": int(cal["unlocked_position"])}
                )
            self._sync_hard_stops()
            self.db.log(
                "calibration",
                f"Swapped the direction of rotation - locking now turns "
                f"{direction_label(cal)}",
                actor="ui",
            )
            return self.status()


def build_backend(db: Database, options: dict[str, Any], hass: HassClient) -> BaseBackend:
    if options.get("backend") == "esphome" and hass.configured:
        return EsphomeBackend(hass, options)
    return MockBackend(db.get_calibration())
