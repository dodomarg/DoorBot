# DoorBot

Calibrate a DIY servo deadbolt and manage its PIN codes, from inside Home
Assistant.

DoorBot drives a **Feetech ST3215 or ST3235** serial bus servo through a **Seeed XIAO
ESP32S3** running ESPHome. This add-on gives you the bit that ESPHome doesn't:
a guided calibration wizard (much like a SwitchBot Lock's) and proper PIN code
management with schedules, one-time codes and an audit trail.

## Try it before you build anything

Leave `backend` on **`mock`**. The add-on then runs against a simulated servo
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
moving_binary_sensor_entity: binary_sensor.doorbot_moving
holding_binary_sensor_entity: binary_sensor.doorbot_holding_position
move_result_entity: sensor.doorbot_last_move
online_binary_sensor_entity: binary_sensor.doorbot_servo_online
voltage_sensor_entity: sensor.doorbot_servo_voltage
temperature_sensor_entity: sensor.doorbot_servo_temperature
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
| `moving_binary_sensor_entity` | Read, to know when a turn has finished |
| `holding_binary_sensor_entity` | Read, to confirm the servo is really holding |
| `move_result_entity` | Read, for how the last move ended (arrived / jammed / timeout) |
| `online_binary_sensor_entity` | Read, to know whether a servo is answering on the bus at all |
| `voltage_sensor_entity` | Read, for the bus voltage |
| `temperature_sensor_entity` | Read, for the servo temperature |

The last three are how DoorBot verifies a move instead of assuming it worked.
If they are wrong, every lock and unlock will be reported as a failure.

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
4. **Check the direction of rotation** (see below).
5. **Test lock** and **Test unlock**.

The wizard warns you if the two points are closer than 60 steps, which almost
always means the coupler slipped or you captured the same position twice.

### Direction of rotation

The two end points are the only source of truth for which way the lock turns:
whichever way the servo has to count to get from unlocked to locked *is* the
locking direction. The wizard states it plainly — "locking turns clockwise" —
so you can check it against the real door before trusting it.

If it reads the wrong way round, you captured the two points in the wrong
order. Press **Swap direction** rather than starting again: it exchanges the
end points and carries the hold-open point across with them.

Clockwise here means clockwise *as the servo counts*, which is what you see at
the output shaft. A mirrored mount or an extra gear reverses that. Tick **the
thumbturn turns the opposite way to the arrows** and the jog arrows flip to
match what you actually see.

> **Hold-open has been removed.** It worked by keeping the servo energised past
> the unlocked point so a passive outside handle could push the door. A servo
> under torque cannot be turned by hand, so that is a servo capable of trapping
> someone on the wrong side of a door. **Open** now simply unlocks. The
> direction rules below still apply to the stored hold point, which is kept
> only so the setting can be restored if a bounded version is ever added.

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

DoorBot's ESP32 impersonates a SwitchBot Lock, so your keypad pairs to it and
sends every unlock over the genuine **AES-CTR encrypted** lock protocol. Each
message says how the credential was presented and which slot it came from —
so DoorBot knows who is at the door.

**No SwitchBot Lock is required.** Pair once from the ESP32's own setup page
(browse to its IP, sign in to your SwitchBot account, pick the keypad), and
everything after that is local.

### Credentials

Under **Keypad → Credentials**, name each slot. Slots are numbered in the order
you added them in the SwitchBot app, **starting at 0**, and each method has its
own numbering — `fingerprint` slot 0 and `pin` slot 0 are different credentials.

| Field | What it does |
|---|---|
| **Name** | Shown in the log and in HA events |
| **Presented as** | PIN, fingerprint, NFC tag or face |
| **Slot** | The credential index from the SwitchBot app |
| **Allowed days / hours** | Refuse this credential outside its window |
| **Notify** | Flag the event so an automation can alert you |
| **Duress** | Opens the door, but raises a duress alert |

The PIN digits and fingerprint templates never leave the keypad — DoorBot only
stores who a slot belongs to and when it's allowed.

Turn on **Only allow slots listed below** to refuse any credential you haven't
named here. Leave it off and unknown slots still work, they're just logged
anonymously.

Full protocol write-up in `docs/switchbot-keypad.md` in the repository.

## Troubleshooting

**Lock state shows "unknown"** — the servo position isn't near either calibrated
point. Recalibrate, or check the coupler hasn't slipped.

**"Jammed" after every move** — the jam threshold is too low for your door, or
the bolt really is binding. Raise the threshold, or lower Speed and add
Overshoot.

**`esphome` backend can't reach anything** — check the entity IDs in the options
match what your ESP32 actually created (Developer Tools → States).
