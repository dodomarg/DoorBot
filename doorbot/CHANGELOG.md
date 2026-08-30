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
