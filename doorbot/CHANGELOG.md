# Changelog

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
- SwitchBot Keypad (non-touch) support via BLE `attempt_state`, with an
  anti-replay throttle and an optional "require a DoorBot PIN as well" switch.
- Event log, and `doorbot_event` forwarded to the Home Assistant event bus.
- `mock` backend with a simulated STS3215, so the whole add-on can be used and
  tested before any hardware exists.
- No third-party Python dependencies.
