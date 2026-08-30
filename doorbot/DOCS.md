# DoorBot

Calibrate a DIY servo deadbolt and manage its PIN codes, from inside Home
Assistant.

DoorBot drives a **Feetech STS3215** serial bus servo through a **Seeed XIAO
ESP32S3** running ESPHome. This add-on gives you the bit that ESPHome doesn't:
a guided calibration wizard (much like a SwitchBot Lock's) and proper PIN code
management with schedules, one-time codes and an audit trail.

## Try it before you build anything

Leave `backend` on **`mock`**. The add-on then runs against a simulated STS3215
with realistic travel time, load feedback and end stops. You can walk the whole
calibration wizard, add PIN codes, use the virtual keypad and even trigger a
simulated jam — all without hardware. Switch to `esphome` once your ESP32 is
flashed.

## Configuration

```yaml
backend: mock
lock_entity: lock.doorbot_lock
position_number_entity: number.doorbot_target_position
position_sensor_entity: sensor.doorbot_position
load_sensor_entity: sensor.doorbot_load
torque_switch_entity: switch.doorbot_servo_torque
auto_lock_seconds: 0
max_failed_attempts: 5
lockout_seconds: 300
log_level: info
```

### `backend`

- **`mock`** — simulated servo. No hardware needed. Use this to try everything out.
- **`esphome`** — talks to the real ESP32 through the Home Assistant entities below.

### Entity options

Only used when `backend: esphome`. These must match the entity IDs your ESP32
created. With the stock `esphome/doorbot.yaml` from the repo and a device named
`doorbot`, the defaults above are already correct.

| Option | What DoorBot does with it |
|---|---|
| `lock_entity` | Reports the lock state |
| `position_number_entity` | Written to, to move the servo |
| `position_sensor_entity` | Read, for the live position |
| `load_sensor_entity` | Read, for jam detection |
| `torque_switch_entity` | Toggled, to release the servo during calibration |

### `auto_lock_seconds`

Re-lock automatically this many seconds after unlocking. `0` disables it.

### `max_failed_attempts` / `lockout_seconds`

After this many wrong PINs, that source is locked out for this long. The limit
is **per source**, so someone hammering the front keypad can't lock out your
dashboard.

## Calibration

Open the add-on → **Calibration**:

1. **Release servo** — torque off, so the thumbturn turns freely by hand.
2. Turn the bolt fully **thrown**, press **Set as LOCKED**.
3. Turn the bolt fully **retracted**, press **Set as UNLOCKED**.
4. **Test lock** and **Test unlock**.

The wizard warns you if the two points are closer than 60 steps, which almost
always means the coupler slipped or you captured the same position twice.

There are also fine controls:

| Setting | What it does |
|---|---|
| **Speed** | How fast the bolt turns |
| **Overshoot** | Push this far past the target, then settle back. Helps stiff cylinders throw fully. |
| **Jam threshold** | Servo load above this counts as a jam and aborts the move |

You can also nudge with **Jog −/+** or drive to an exact position, which is
useful when the coupler is slightly off.

## PIN codes

| Type | Behaviour |
|---|---|
| **Permanent** | Always valid |
| **Time limited** | Valid between two dates |
| **One time** | Works once, then disables itself |
| **Recurring** | Chosen weekdays plus a daily time window (windows may cross midnight) |
| **Duress** | Opens the door, but raises a flagged event so an automation can alert you |

Codes are stored **hashed** (PBKDF2-HMAC-SHA256, 120 000 rounds, unique salt per
code). DoorBot can verify a PIN but can never show it to you again — only a hint
like `2••••3`. Write it down when you create it, or use **Suggest** to generate
one, which also rejects weak sequences like `1234` or `0000`.

## Using a code from elsewhere

Anything can present a PIN:

```yaml
rest_command:
  doorbot_verify:
    url: "http://addon_local_doorbot:8099/api/verify"
    method: POST
    content_type: application/json
    payload: '{"code": "{{ code }}", "source": "{{ source }}"}'
```

Useful for a keypad card on a dashboard, an NFC tag automation, or a wired
keypad.

## Events

DoorBot fires `doorbot_event` on the Home Assistant event bus for every lock,
unlock, failed attempt, jam and duress code. Automate on it:

```yaml
automation:
  - alias: Alert on duress code
    triggers:
      - trigger: event
        event_type: doorbot_event
        event_data:
          kind: duress
    actions:
      - action: notify.mobile_app
        data:
          message: "A duress code was used on the front door."
```

## SwitchBot Keypad

If you have a SwitchBot Keypad (non-touch), the ESP32 can watch its BLE
advertisement and unlock when the keypad reports an accepted PIN. Configure the
throttle and the safety switch under the **Keypad** tab.

**Read this before relying on it.** The advertisement is *unencrypted*, so it is
replayable, and it does *not* say which PIN was entered — only accepted or
rejected. Enable **Require a DoorBot PIN as well** if that matters to you. Full
details in `docs/switchbot-keypad.md` in the repository.

## Troubleshooting

**Lock state shows "unknown"** — the servo position isn't near either calibrated
point. Recalibrate, or check the coupler hasn't slipped.

**"Jammed" after every move** — the jam threshold is too low for your door, or
the bolt really is binding. Raise the threshold, or lower Speed and add
Overshoot.

**`esphome` backend can't reach anything** — check the entity IDs in the options
match what your ESP32 actually created (Developer Tools → States).
