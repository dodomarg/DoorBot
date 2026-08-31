# DoorBot

A DIY retrofit smart lock: a **Seeed XIAO ESP32S3** driving a **Feetech STS3215**
serial bus servo through a **Seeed Bus Servo Driver Board (XIAO Bus Servo
Adapter)**, controlled from **Home Assistant**, with an add-on for calibration
and PIN management — and optional PIN entry from a **SwitchBot Keypad**.

```
              encrypted BLE                Wi-Fi / ESPHome API
SwitchBot Keypad ───────────► XIAO ESP32S3 ────────────────────► Home Assistant
  (PIN / NFC /                + STS3215                              │
   fingerprint / face)           │                                   │
                            deadbolt turn                     DoorBot add-on
                                                        (calibration + PIN codes)
```

The ESP32 impersonates a SwitchBot Lock, so the keypad pairs to it and sends
every unlock **encrypted** — including *which* credential was used.

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

Power the servo from the driver board's DC jack, never from USB — the STS3215
stalls at well over an amp and will brown out the XIAO.

#### Changing Wi-Fi later

The firmware has no fallback access point, because one would be useless here
(see the comment above `improv_serial:` in `doorbot.yaml`). It ships
`improv_serial` instead, so new credentials go over the same USB cable used for
flashing: plug the board in, open <https://web.esphome.io>, **Connect**, then
**Configure Wi-Fi**. No reflash, and it works from a browser with Web Serial —
including Firefox 154+, as long as the page is a secure context.

### 4. Calibrate

Open the add-on → **Calibration**:

1. **Release the servo** so the thumbturn turns freely.
2. Move the bolt fully thrown → **Set as LOCKED**.
3. Move the bolt fully retracted → **Set as UNLOCKED**.
4. **Test lock / unlock**.

Then tune speed, overshoot (an extra push past the target for stiff cylinders)
and the jam threshold.

Every move is checked against the servo's own position feedback, so a turn that
falls short, jams or times out is reported as a failure rather than quietly
counted as success.

#### Locks needing more than one turn

Tick **Multi-turn** before capturing the positions. The travel range opens from
one revolution (0–4095) to ±30719 steps, so a euro cylinder that needs two or
three turns works without gearing.

The servo does not remember its revolution count across a power cut — that is a
hardware limitation, not a bug. DoorBot re-homes after a reset.

#### Doors with a passive outside handle

If the outside handle does not retract the latch, unlocking is not enough: the
latch stays out and the door will not push open. Use the **Hold open** card —
DoorBot turns past the unlocked point to a hold position, keeps the latch back
for the number of seconds you set, then returns.

Set **Hold for** to 0 if your handle retracts the latch itself.

If the latch slips while being held, it is almost always the servo's own
overload protection cutting output. Raise **Protective torque** on the ESP32
device page; `docs/sts3215.md` explains the mechanism.

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

DoorBot's ESP32 pretends to be a SwitchBot Lock. Your keypad pairs to it and
talks the genuine, **AES-CTR encrypted** lock protocol — so every unlock says
how it was presented (**PIN, NFC, fingerprint or face**) and **which credential
slot** it came from.

That means you get real per-person access control:

- Name each slot, so the log reads "Maya, fingerprint" instead of "slot 2"
- Give a slot its own schedule — the cleaner's PIN only works Tuesday mornings
- Disable one credential without reprogramming the keypad
- Flag a slot as **duress**: it opens the door but raises an alert
- Optionally refuse any slot you haven't named in DoorBot

**No SwitchBot Lock required.** You sign in to your SwitchBot account once, from
the ESP32's own setup page, so it can fetch the keypad's communication key.
After that it is entirely local.

Pairing is handled by the excellent
[switchbot-keypad-bridge](https://github.com/pierluigizagaria/switchbot-keypad-bridge)
component. Full protocol write-up in
[`docs/switchbot-keypad.md`](docs/switchbot-keypad.md).

## Security notes

- PINs are hashed, never stored or logged in the clear.
- The add-on binds to `0.0.0.0:8099` inside its container and is only reachable
  through Home Assistant **ingress** (so it inherits HA authentication).
- Keypad traffic is encrypted end to end and the session key is generated on the
  ESP32, so it never appears in your YAML or git.
- The keypad authenticates the credential; DoorBot authorises it. Turn on
  *"Only allow slots listed below"* to refuse any credential you haven't named.
- The lock only ever *listens* for Home Assistant on TCP 6053; it never opens a
  connection to your network, so it is safe to isolate on an IoT VLAN. Ports and
  firewall rules are in [`docs/network.md`](docs/network.md).
- Don't expose `/api/verify` to the internet.

## Licence

MIT — see [LICENSE](LICENSE).
