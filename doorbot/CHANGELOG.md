# Changelog

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
