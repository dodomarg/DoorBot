"""Lock control layer.

Two interchangeable backends:

* ``MockBackend``    - a physics-lite simulation of a Feetech STS3215 on a
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

# Servo raw units. The STS3215 is a 12-bit encoder: 0..4095 over 360 degrees.
RESOLUTION = 4096
STATE_LOCKED = "locked"
STATE_UNLOCKED = "unlocked"
STATE_LOCKING = "locking"
STATE_UNLOCKING = "unlocking"
STATE_JAMMED = "jammed"
STATE_UNKNOWN = "unknown"

# The locked and unlocked points must be at least this far apart (raw steps,
# ~5 degrees) before the calibration is considered usable.
MIN_TRAVEL = 60


def raw_to_degrees(raw: int) -> float:
    return round(raw * 360.0 / RESOLUTION, 1)


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


class MockBackend(BaseBackend):
    """Simulated STS3215 so the add-on is fully testable without hardware."""

    name = "mock"

    def __init__(self, calibration: dict[str, Any]) -> None:
        self._lock = threading.RLock()
        self._position = float(calibration.get("locked_position", 2048))
        self._goal = self._position
        self._speed = float(calibration.get("speed", 800) or 800)
        self._torque = True
        self._last = time.monotonic()
        self._load = 0.0
        self._voltage = 12.1
        self._temperature = 31.0
        # A simulated mechanical hard stop just past each calibrated end point,
        # so "drive until it stalls" behaves like a real deadbolt.
        self._stop_low = 0.0
        self._stop_high = float(RESOLUTION - 1)
        self.jam_next_move = False
        self._jammed = False

    def set_hard_stops(self, low: float, high: float) -> None:
        with self._lock:
            self._stop_low, self._stop_high = min(low, high), max(low, high)

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

        # STS3215 goal velocity is in steps/second-ish; scale for realism.
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
                self._goal = self._position
        else:
            self._load *= 0.6

        self._temperature = min(60.0, 30.0 + self._load / 60.0)
        self._voltage = 12.2 - self._load / 4000.0

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._tick()
            return {
                "online": True,
                "position": int(round(self._position)),
                "goal": int(round(self._goal)),
                "degrees": raw_to_degrees(int(round(self._position))),
                "load": int(round(self._load)),
                "voltage": round(self._voltage, 1),
                "temperature": round(self._temperature, 1),
                "torque": self._torque,
                "moving": abs(self._goal - self._position) > 1.0,
                "jammed": self._jammed,
            }

    def move_to(self, position: int, speed: int | None = None) -> None:
        with self._lock:
            self._tick()
            if speed:
                self._speed = float(speed)
            self._torque = True
            self._goal = float(max(0, min(RESOLUTION - 1, int(position))))
            self._jammed = False

    def set_torque(self, enabled: bool) -> None:
        with self._lock:
            self._tick()
            self._torque = bool(enabled)
            if not enabled:
                self._goal = self._position

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
        online = position is not None
        return {
            "online": online,
            "position": int(position) if position is not None else 0,
            "goal": int(self._num(self.options.get("position_number_entity", "")) or 0),
            "degrees": raw_to_degrees(int(position)) if position is not None else 0.0,
            "load": int(load) if load is not None else 0,
            "voltage": self._num("sensor.doorbot_voltage") or 0.0,
            "temperature": self._num("sensor.doorbot_temperature") or 0.0,
            "torque": bool(torque_state and torque_state.get("state") == "on"),
            "moving": False,
            "jammed": False,
        }

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


class LockController:
    """High-level lock behaviour built on top of a backend."""

    def __init__(self, db: Database, backend: BaseBackend, options: dict[str, Any]) -> None:
        self.db = db
        self.backend = backend
        self.options = options
        self._lock = threading.RLock()
        self._state = STATE_UNKNOWN
        self._auto_lock_timer: threading.Timer | None = None
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._sync_hard_stops()
        self._state = self._infer_state()

    # ------------------------------------------------------------- plumbing
    def _sync_hard_stops(self) -> None:
        if isinstance(self.backend, MockBackend):
            cal = self.db.get_calibration()
            if not cal.get("calibrated"):
                # Nothing learned yet - let the simulated bolt travel freely so
                # the calibration wizard can reach any position.
                self.backend.set_hard_stops(0, RESOLUTION - 1)
                return
            lo = min(cal["locked_position"], cal["unlocked_position"])
            hi = max(cal["locked_position"], cal["unlocked_position"])
            # Generous margin: a real deadbolt has a little slack past each end,
            # and this must never box in a later re-calibration.
            margin = max(150, int(abs(hi - lo) * 0.25))
            self.backend.set_hard_stops(lo - margin, hi + margin)

    def _settle(self, timeout: float = 12.0) -> dict[str, Any]:
        """Block until the servo stops moving (or the timeout expires)."""
        deadline = time.monotonic() + timeout
        servo = self.backend.status()
        while servo.get("moving") and time.monotonic() < deadline:
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
            if servo.get("jammed"):
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
                "auto_lock_pending": self._auto_lock_timer is not None,
            }

    # ------------------------------------------------------------- movement
    def _drive(self, position: int, speed: int | None, actor: str) -> dict[str, Any]:
        """Move to a position, wait for it to settle, and raise if it jams."""
        self.backend.move_to(position, speed)
        servo = self._settle()
        if servo.get("jammed"):
            self._state = STATE_JAMMED
            self.db.log("jammed", "The lock jammed while moving", actor=actor)
            self._emit("jammed", {"actor": actor})
            raise BackendError("The lock jammed - check the mechanism.")
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
            push = max(0, min(RESOLUTION - 1, target + direction * overshoot))
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
            self._cancel_auto_lock()
            return self._travel("locked_position", STATE_LOCKED, actor)

    def unlock(self, actor: str = "ui") -> dict[str, Any]:
        with self._lock:
            return self._travel("unlocked_position", STATE_UNLOCKED, actor)

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
            servo = self.backend.status()
            if not servo["torque"] and isinstance(self.backend, MockBackend):
                self.backend.nudge_by_hand(delta)
            else:
                self.backend.move_to(servo["position"] + delta, 400)
                self._settle(5.0)
            return self.status()

    def goto(self, position: int) -> dict[str, Any]:
        with self._lock:
            self.backend.move_to(int(position), self.db.get_calibration().get("speed"))
            self._settle(8.0)
            return self.status()

    def set_torque(self, enabled: bool) -> dict[str, Any]:
        with self._lock:
            self.backend.set_torque(enabled)
            return self.status()

    def capture(self, which: str) -> dict[str, Any]:
        """Store the current physical position as the locked/unlocked point."""
        if which not in ("locked", "unlocked"):
            raise BackendError("Capture either 'locked' or 'unlocked'.")
        with self._lock:
            position = self._settle(8.0)["position"]
            cal = self.db.save_calibration({f"{which}_position": position})
            travel = abs(cal["locked_position"] - cal["unlocked_position"])
            cal = self.db.save_calibration({"calibrated": travel >= MIN_TRAVEL})
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
            allowed = {
                k: v
                for k, v in values.items()
                if k
                in {
                    "locked_position",
                    "unlocked_position",
                    "overshoot",
                    "hold_ms",
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
            if "invert" in allowed:
                allowed["invert"] = bool(allowed["invert"])
            cal = self.db.save_calibration(allowed)
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


def build_backend(db: Database, options: dict[str, Any], hass: HassClient) -> BaseBackend:
    if options.get("backend") == "esphome" and hass.configured:
        return EsphomeBackend(hass, options)
    return MockBackend(db.get_calibration())
