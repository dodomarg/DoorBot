# DoorBot

A DIY retrofit smart lock: a **Seeed XIAO ESP32S3** driving a **Feetech STS3215**
serial bus servo through a **Seeed Bus Servo Driver Board (XIAO Bus Servo
Adapter)**, controlled from **Home Assistant**, with an add-on for calibration
and PIN management — and optional PIN entry from a **SwitchBot Keypad**.

```
                 BLE advert                 Wi-Fi / ESPHome API
SwitchBot Keypad ───────────► XIAO ESP32S3 ────────────────────► Home Assistant
                              + STS3215                              │
                                 │                                   │
                            deadbolt turn                     DoorBot add-on
                                                        (calibration + PIN codes)
```

## What's in here

| Path | What it is |
|---|---|
| `doorbot/` | The Home Assistant add-on (web UI: calibration wizard, PIN codes, event log) |
| `esphome/doorbot.yaml` | Firmware for the XIAO ESP32S3 |
| `esphome/components/sts3215/` | ESPHome external component implementing the Feetech STS/SMS bus protocol |
| `hardware/` | FreeCAD model of the enclosure / bracket |
| `docs/` | Protocol notes, wiring, and setup guides |
| `repository.yaml` | Makes this repo installable as a Home Assistant add-on repository |

## Quick start

### 1. Try the add-on with no hardware

```bash
cd doorbot/rootfs/opt/doorbot
DOORBOT_DATA=/tmp/doorbot-data DOORBOT_PORT=8099 python3 -m app
# then open http://localhost:8099
```

It starts in **mock** mode with a simulated STS3215, so you can run the whole
calibration wizard, add PIN codes, test the virtual keypad and even simulate a
jam before any hardware exists. No third-party Python packages required.

### 2. Install the add-on in Home Assistant

**Settings → Add-ons → Add-on Store → ⋮ → Repositories**, add this repo's URL,
then install **DoorBot**. Open the web UI from the sidebar.

Leave `backend: mock` until the ESP32 is flashed, then switch to `esphome`.

### 3. Flash the ESP32

```bash
cd esphome
esphome run doorbot.yaml
```

Wiring (the XIAO plugs straight into the driver board headers):

| XIAO ESP32S3 | Driver board |
|---|---|
| D6 / GPIO43 | servo bus **TX** |
| D7 / GPIO44 | servo bus **RX** |
| — | DC IN 5–12 V matching the servo |

The servo bus runs at **1,000,000 baud** (STS3215 factory default) and the
driver board handles the half-duplex conversion, so a plain UART is all that is
needed.

### 4. Calibrate

Open the add-on → **Calibration**:

1. **Release the servo** so the thumbturn turns freely.
2. Move the bolt fully thrown → **Set as LOCKED**.
3. Move the bolt fully retracted → **Set as UNLOCKED**.
4. **Test lock / unlock**.

Then tune speed, overshoot (an extra push past the target for stiff cylinders)
and the jam threshold.

The calibration is also stored on the ESP32 itself in `restore_value` number
entities, so the lock keeps working if the add-on or Home Assistant is down.

## PIN codes

Codes are stored **hashed** (PBKDF2-HMAC-SHA256, 120k rounds, per-code salt) —
DoorBot can check a PIN but can never show it back to you. Supported types:

- **Permanent**
- **Time limited** — valid between two dates
- **One time** — burns itself after a single use
- **Recurring** — days of the week plus a daily time window
- **Duress** — opens the door but flags the event so an automation can react

Failed attempts are rate limited per source (`max_failed_attempts` /
`lockout_seconds`).

Anything can present a code to `POST /api/verify` — a dashboard keypad card, an
NFC tag automation, a wired keypad, or the built-in virtual keypad.

## SwitchBot Keypad

See [`docs/switchbot-keypad.md`](docs/switchbot-keypad.md) for the full
protocol write-up. Short version:

- The keypad checks the PIN **itself** and broadcasts an **unencrypted** BLE
  advertisement containing a counter (`attempt_state`).
- The counter goes up by **1** on a rejected PIN and by **2** on an accepted one.
- The ESP32 watches for that and can open the lock — no SwitchBot Lock, hub or
  cloud needed for the *reading* side.

⚠️ **Important limitations:** the advert is unencrypted and replayable, and it
does not tell you *which* PIN was used. Also, programming PINs into the keypad
is done in the SwitchBot app, which normally requires pairing it to a real
SwitchBot Lock. Treat the keypad as a convenience trigger and keep DoorBot's own
PIN validation as the real access control.

## Security notes

- PINs are hashed, never stored or logged in the clear.
- The add-on binds to `0.0.0.0:8099` inside its container and is only reachable
  through Home Assistant **ingress** (so it inherits HA authentication).
- Keypad BLE events are advisory. Enable *"Require a DoorBot PIN as well"* if
  you don't want a replayed advertisement to be able to open the door.
- Don't expose `/api/verify` to the internet.

## Licence

MIT — see [LICENSE](LICENSE).
