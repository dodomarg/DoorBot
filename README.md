# DoorBot

A DIY retrofit smart lock: a **Seeed XIAO ESP32S3** driving a **Feetech ST3215 or
ST3235** serial bus servo through a **Seeed Bus Servo Driver Board (XIAO Bus Servo
Adapter)**, controlled from **Home Assistant**, with an add-on for calibration
and PIN management — and optional PIN entry from a **SwitchBot Keypad**.

```
              encrypted BLE                Wi-Fi / ESPHome API
SwitchBot Keypad ───────────► XIAO ESP32S3 ────────────────────► Home Assistant
  (PIN / NFC /                + Feetech servo                        │
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
| `esphome/components/feetech_servo/` | ESPHome external component implementing the Feetech SMS/STS bus protocol |
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

It starts in **mock** mode with a simulated servo, so you can run the whole
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

The servo bus runs at **1,000,000 baud** (the factory default for both models) and the
driver board handles the half-duplex conversion, so a plain UART is all that is
needed.

Power the servo from the driver board's DC jack, never from USB — both models
draw 2.7 A locked-rotor and will brown out the XIAO.

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

**Leave it off unless your lock genuinely needs it.** The goal register accepts
±30719, but the servo still reports its position wrapped to 0–4095 — the two
are not symmetrical. DoorBot's firmware unwraps the reading and tracks the
revolution count itself, which is what makes multi-turn work at all; without
that, a goal beyond one revolution can never be *observed* as reached and the
servo chases it forever instead of reporting a failure.

The revolution count still starts at zero on every boot, so absolute multi-turn
position does not survive a reset — that is a hardware limitation, not a bug. A
multi-turn lock must be re-homed after a reboot. A lock whose travel fits
inside one revolution has no such problem, which is the other reason to leave
multi-turn off when you can.

Because a multi-turn step count is not an angle, the interface reports the
angle *within the current revolution* alongside a separate turn count, rather
than showing you something like "527°".

#### The servo is never left holding indefinitely

DoorBot energises the servo only while a move is actually running or a
**time-bounded hold** is running, and releases it the moment either ends. This
is deliberate and non-negotiable: a servo holding position is a servo you cannot
turn by hand, and on a door that means a firmware fault can trap someone on the
wrong side. There is no command anywhere in the system that energises the servo
without an end time — the add-on refuses one outright.

A watchdog in the firmware enforces it every loop. A move that overruns its
energised budget is aborted and released; torque found on at rest is released
within a second; and if the servo answers but refuses to let go, the node
restarts, because `setup()` forces the release before it does anything else.
Torque state lives in the servo's own register and survives an ESP32 reset, so
the reboot alone would not clear it.

That decision is a pure function in `esphome/components/feetech_servo/safety_policy.h`
— no hardware, no globals, no clock of its own — so it can be compiled and
exercised on a PC. `tests/safety_policy_test.cpp` drives it through the 49.7-day
`millis()` rollover, a stopped clock, a clock running backwards and a corrupted
deadline, and asserts that torque is always released and a hold never exceeds
its ceiling:

```bash
g++ -std=c++17 -Wall -Wextra -o /tmp/safety_test tests/safety_policy_test.cpp && /tmp/safety_test
```

**Hold-open** works within this rule rather than around it. It drives past the
unlocked point and holds there so a passive outside handle can push the door,
but the hold is requested *as part of the move* (torque is dropped the instant a
move ends, so a hold asked for afterwards would leave a gap for a spring latch),
it is granted only if the move actually arrives, and the firmware clamps the
duration to a **60 second** ceiling. Three independent mechanisms end it: the
granted deadline, an elapsed-time ceiling that does not trust the deadline, and
a loop counter that does not trust the clock.

There is one honest limit. If the serial bus itself is dead, the firmware
cannot transmit a release at all, and no amount of rebooting changes that — it
would only cost you the logs, OTA and Home Assistant connection you need to
diagnose the fault. In that case DoorBot reports a component error and keeps
retrying. **Cutting power to the servo is the only complete mitigation**, so
put it on a switched supply if the door is the only way out of a room.

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
