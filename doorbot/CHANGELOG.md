# Changelog

## 0.2.5

- **Hold-open is back, but the firmware owns the deadline.** A hold is now
  requested as part of a move (`hold_open`), not as a separate "energise now"
  command, because torque is dropped the instant a move ends and a spring latch
  would snap back in that gap. The hold is granted *only* if the move actually
  arrives -- a jam, stall, timeout or abort releases instead of holding.
- **The firmware refuses to hold longer than 60 s.** Any request above the
  ceiling is clamped in the firmware, not in the UI or the add-on. The add-on
  and the wizard also validate it, but they are a convenience, not the control.
- **Removed the wizard's "Hold position" button.** It energised the servo with
  no end time, which is exactly the state that can trap someone. The add-on now
  refuses `set_torque(true)` outright (HTTP 409) so there is no path to an
  indefinite hold from any client.
- **The safety decision is now a pure function with its own test suite.** The
  watchdog logic moved into `safety_policy.h` -- no hardware, no globals, no
  clock of its own -- so it can be compiled and driven on a PC.
  `tests/safety_policy_test.cpp` (27 checks) proves torque is always released
  across the 49.7-day `millis()` rollover, with a stopped clock, with a clock
  running backwards, and with a corrupted deadline.
- **Three independent reasons a hold can end**, because trusting one value is
  how a door stays clamped: the granted deadline (wrap-safe signed compare), an
  elapsed-time ceiling measured from the hold's start that does not trust the
  deadline, and a loop counter that does not trust the clock at all.
- **Fixed three simulator bugs that were hiding the safety policy.** The mock
  never released torque after a move, so every mock-based test was modelling a
  servo that holds forever; a new move did not cancel a running hold, so the old
  deadline cut torque mid-move; and torque dropping mid-move froze the mock at
  `moving` instead of reporting `aborted`.
- **Fixed: "Open" was not actually holding anything.** The `open_door` script
  still used the pre-0.2.4 approach -- an ordinary move followed by a YAML
  `delay`. Since every move now releases torque on arrival, that "hold" dropped
  the latch immediately and then waited out the delay doing nothing. It now
  calls the firmware's bounded hold, so the deadline survives the script being
  stopped, the add-on dying or Home Assistant going away.
- **Fixed: two warnings that fired on every single operation.** The post-move
  check asked "is the servo holding?", which is false by design after a normal
  move, so every lock and unlock logged *"Servo is not holding"*; and the
  hold check fired *"The latch slipped during the hold"* plus a
  `doorbot_hold_slipped` event every time. Constant false alarms are how a real
  one gets ignored. The post-move check now verifies the move *arrived*, and
  slip detection runs during the hold, when it can actually mean something.
- **Fixed: "Holding position" turned on for unsanctioned torque.** It was
  derived from raw torque, so a servo left energised by a fault -- exactly what
  the watchdog exists to clear -- reported as a legitimate hold. It now requires
  an active bounded hold, so an automation can tell the two apart.
- **A hold ending normally no longer logs a warning.** It shared the watchdog's
  anomaly wording, which would have taught you to ignore the message that
  matters.

## 0.2.4

- **The servo is never left holding torque.** A servo under torque cannot be
  turned by hand, so on a door it can trap someone on the wrong side. Torque is
  now energised only while a move is actually running and released the moment
  it ends, with the release read back from the servo rather than assumed.
- **Added a safety watchdog in the firmware.** Every loop it checks that the
  servo is either moving or released. A move that overruns its energised budget
  (15 s, above the 12 s move timeout) is aborted and released. Torque found on
  at rest is released within a second. If the servo answers but refuses to drop
  torque, the node restarts, and `setup()` forces the release before it does
  anything else -- torque state lives in the servo and survives an ESP32 reset,
  so the reboot alone would not have cleared it.
- **The watchdog will not reboot into a dead bus.** If the servo is
  unreachable, a restart cannot transmit the release either, and rebooting on a
  one-second cadence would cost the logs, OTA and the Home Assistant connection
  needed to diagnose it. It now reports a component error and keeps retrying
  instead. If the bus is dead, only cutting servo power restores manual
  operation -- no firmware can do it for you.
- **Fixed: aborting a move re-energised the servo.** `abort_move()` commanded a
  hold at the current position before finishing, which clamped the lock exactly
  where the abort was meant to free it.
- **Removed hold-open.** It worked by driving past the unlocked point and
  keeping the servo energised there so a passive outside handle could push the
  door. That is sustained torque with nobody watching, which the safety policy
  forbids. *Open* now simply unlocks, and says so in the log rather than
  silently doing nothing.

### Multi-turn

- **Fixed the runaway.** `multi_turn` let the goal register accept
  -30719..30719, but the servo still reports `Present_Position` wrapped to
  0..4095. A goal beyond one revolution could therefore never be observed as
  reached, so the move engine chased it forever -- the position climbed at the
  configured speed and wrapped, 3438 to 148 to 3371 to 81, while the UI
  reported `arrived`. The firmware now unwraps the reading across the 4096
  boundary and tracks the revolution count itself.
- **`multi_turn` now defaults to off.** The revolution count still starts at
  zero on every boot, so absolute multi-turn position does not survive a reset.
  It is only worth enabling for a cylinder that genuinely needs more than one
  turn, and such a setup needs re-homing after a reboot.
- **Fixed: positions were displayed as 12-bit angles.** A multi-turn step count
  is not an angle -- position 6000 was shown as 527 degrees. Degrees are now the
  angle within the current revolution, with the revolution count reported
  separately.
- **Fixed: the Turns readout was always blank on real hardware.** The ESPHome
  backend never emitted a `turns` value at all, so the metric only ever worked
  in the simulator.

## 0.2.3

- **The calibration wizard now shows the direction of rotation.** It is derived
  from the two captured end points, stated in plain terms ("locking turns
  clockwise"), and shown alongside the positions it came from. Previously the
  direction was implicit in two numbers and never surfaced anywhere, so there
  was no way to confirm it before running the lock against a real door.
- **Added a *Swap direction* control.** Capturing the end points in the wrong
  order gave the right travel in the wrong direction, and the only remedy was
  to run the whole wizard again. Swapping exchanges the two points and carries
  the hold-open point across with them.
- **The jog arrows now say which way they turn.** They were labelled only with
  raw step counts, so there was no way to tell which button moved toward
  locked before pressing it.
- **Fixed: *Invert direction* did nothing.** It was stored and validated but
  never read by any code path. It now flips the jog arrows so they match a
  mirrored mount, and it is in the wizard next to the direction it affects
  rather than buried in the settings form.
- **Fixed: the hold-open position was never checked against the direction of
  travel.** A value on the locked side would drive the bolt back out while
  reporting that the door was being held open. It is now rejected with an
  explanation, and a hold point stranded by a direction change is retired
  automatically rather than left pointing the wrong way.

## 0.2.2

- **A servo that is not responding is now an error, not a silent success.**
  Every path that commands motion — lock, unlock, open, jog, go-to, torque and
  capture — checks first that the servo is answering, and refuses with a plain
  explanation if it is not. Previously the command was sent into the void and
  the move was reported as successful.
- **The web UI says when nothing is connected.** A red banner appears when the
  servo is not answering and the controls that move it are disabled; an amber
  banner appears whenever the add-on is running the simulator, which reports
  every move as a success by design. Neither state was visible before, so a
  simulated lock looked identical to a real one.
- **Servo reachability is read from the firmware instead of guessed.** The
  add-on now reads `binary_sensor.doorbot_servo_online`, which the ESP32
  publishes from its own ping. It previously inferred reachability from whether
  the position sensor parsed as a number, which reports an unpowered servo as
  position 0 — indistinguishable from a real reading.
- **Fixed: bus voltage and temperature always read zero.** The add-on asked for
  `sensor.doorbot_voltage` and `sensor.doorbot_temperature`, but the firmware
  publishes `sensor.doorbot_servo_voltage` and `sensor.doorbot_servo_temperature`.
  All three entity ids are now configurable rather than hardcoded.
- **Fixed: the reported version was two releases out of date.** The code said
  0.1.0 while the manifest said 0.2.1. The test suite now fails if they diverge.
- The lock state reads *unknown* while the servo is unreachable, rather than
  reporting the last position it happened to see.
- The simulator panel gained a *Pretend the servo is unplugged* toggle, so this
  failure can be reproduced without unplugging anything.

## 0.2.1

- **Support the Feetech ST3235 alongside the ST3215.** They share the SMS/STS
  control table, a 4096-step encoder and protocol 0, so the driver needed no
  behavioural change. The ESPHome component is now named `feetech_servo`
  rather than `sts3215`, which was never accurate — it always spoke the family
  protocol, not one model's dialect.
- **The servo's model number is read and reported.** It appears in the ESP32
  logs, in `dump_config`, and as a *Servo model* diagnostic entity. An
  SCS-series servo is now rejected outright: it answers a ping, but it is
  protocol 1, big-endian and 1024 steps per revolution, so it would otherwise
  have reported positions that were quietly wrong.

## 0.2.0

Closed-loop servo control. Previously DoorBot sent a move and assumed it worked;
now it checks.

- **Every move is verified.** Writes parse the servo's acknowledgement instead of
  firing and forgetting, and a move only counts as finished once the servo
  actually reports itself on target. Falling short, timing out or jamming now
  raises an error instead of silently reporting success.
- **"Holding" is a real reading.** Torque state is read back from the servo, so
  the UI shows whether the motor is genuinely holding position rather than just
  whether the command was sent.
- **Multi-turn support.** Locks needing more than one full revolution now work
  without gearing or external programming tools. The travel range opens up to
  ±30719 steps. (The servo does not keep its revolution count across a power
  cut, so DoorBot re-homes after a reset.)
- **Hold open**, for doors whose outside handle does not retract the latch.
  DoorBot turns past the unlocked point, holds the latch back for a configurable
  number of seconds, then returns — and reports it if the latch slips.
- **All servo programming moved into Home Assistant**: operating mode, ID, angle
  limits, torque and overload protection, and re-centring. Nothing needs a
  separate Feetech tool any more.
- Moves are non-blocking, so Bluetooth and the API stay responsive mid-turn.

## 0.1.0

First release.

- Guided calibration wizard: release the servo, capture the locked and unlocked
  positions by hand, then test.
- Fine controls for speed, overshoot, torque and the jam threshold, plus jog and
  go-to-position.
- PIN code management: permanent, time limited, one time, recurring and duress
  codes. Stored hashed with PBKDF2-HMAC-SHA256.
- Per-source rate limiting on failed attempts.
- Virtual keypad for testing without hardware.
- SwitchBot Keypad support over the genuine encrypted lock protocol: the ESP32
  impersonates a SwitchBot Lock, so every unlock arrives AES-CTR encrypted and
  identifies the method (PIN / NFC / fingerprint / face) and credential slot.
  No SwitchBot Lock required.
- Per-credential naming, day and time windows, notify and duress flags, and an
  optional "known credentials only" lockdown.
- Event log, and `doorbot_event` forwarded to the Home Assistant event bus.
- `mock` backend with a simulated STS3215, so the whole add-on can be used and
  tested before any hardware exists.
- No third-party Python dependencies.
