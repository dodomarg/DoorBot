# Changelog

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
