# SwitchBot Keypad (WoKeypad) — protocol notes

Everything below was verified against source code, not guessed. Sources are
linked inline.

## The short version

| Question | Answer |
|---|---|
| Does the keypad validate the PIN itself? | **Yes** — it stores the passcode table locally |
| Does it broadcast when a code is entered? | **Yes** — a BLE advertisement |
| Is that advertisement encrypted? | **No** (`isEncrypted: False`) |
| Can we tell *which* PIN was used? | **No** — only accepted vs rejected |
| Do we need a SwitchBot Lock / hub / cloud to *read* it? | **No** |
| Do we need one to *program PINs into it*? | **Yes** — the SwitchBot app pairs it to a Lock |

## Advertisement format

The keypad is model character **`y`** (also `Y`) in SwitchBot's model table.

| Field | Where | Meaning |
|---|---|---|
| Service UUID | `0000fd3d-0000-1000-8000-00805f9b34fb` | SwitchBot service data (legacy: `00000d00-…`) |
| `service_data[0]` | `'y'` / `'Y'` | model character = Keypad |
| `service_data[2] & 0x7F` | 0–100 | battery percentage |
| Manufacturer ID | `2409` (`0x0969`) | SwitchBot company ID |
| `manufacturer_data[6]` | 0–255 | **`attempt_state`** — a wrapping counter |

Source: [`switchbot/adv_parsers/keypad.py`](https://github.com/sblibs/pySwitchbot/blob/master/switchbot/adv_parsers/keypad.py)

```python
def process_wokeypad(data, mfr_data):
    if data is None or mfr_data is None or len(data) < 3 or len(mfr_data) < 7:
        return {"battery": None, "attempt_state": None}
    return {"battery": data[2] & 0b01111111, "attempt_state": mfr_data[6]}
```

A real captured advertisement, from pySwitchbot's own test suite
([`tests/test_adv_parser.py::test_parse_advertisement_data_keypad`](https://github.com/sblibs/pySwitchbot/blob/master/tests/test_adv_parser.py)):

```python
manufacturer_data={2409: b"\xeb\x13\x02\xe6#\x0f\x8fd\x00\x00\x00\x00"}
service_data={"0000fd3d-0000-1000-8000-00805f9b34fb": b"y\x00d"}
# -> {"attempt_state": 143, "battery": 100}, "isEncrypted": False
```

`0x8f` = 143 at index 6 ✓, `0x64` = 100 % battery ✓.

## Accepted vs rejected

`attempt_state` is a counter that **advances by 1 when a PIN is rejected and by
2 when it is accepted**. This was the original implementation in pySwitchbot
PR [#252](https://github.com/sblibs/pySwitchbot/pull/252), commit `536f7c5`:

```python
success = lastStatus != -1 and (
    (mfr_data[6] > lastStatus and mfr_data[6] - lastStatus >= 2)
    or (mfr_data[6] < lastStatus and mfr_data[6] - lastStatus >= -254)
)
```

The second clause is the wraparound case. Written modulo 256 it collapses to:

```
delta = (current - previous) mod 256
delta >= 2  ->  accepted
delta == 1  ->  rejected
delta == 0  ->  no new attempt (just a repeat advertisement)
```

Check the wrap: `255 -> 1` is delta 2 → accepted; `255 -> 0` is delta 1 →
rejected. Both are covered by the modulo form, and both are unit tested in
this repo's add-on test suite.

The `success` flag was later replaced by exposing the raw `attempt_state`, so
consumers do the comparison themselves — which is exactly what the DoorBot
ESPHome config and the add-on both do.

## What Home Assistant does natively

Home Assistant's `switchbot` integration supports **Keypad Vision** and **Keypad
Vision Pro**, but *not* the plain `WoKeypad` — it isn't in
`SUPPORTED_MODEL_TYPES` in `homeassistant/components/switchbot/const.py`. So the
plain keypad produces no HA entities on its own, which is why DoorBot's ESP32
listens for the advertisement directly with `esp32_ble_tracker`.

## Security assessment

The maintainer of pySwitchbot said it plainly during review of PR #252:

> "I'd probably add a note in the Home Assistant documentation that there is no
> encryption, so you shouldn't rely on the sensor for secure operation"

Concretely:

- **Replayable.** The advert carries no nonce, timestamp or signature. Anyone
  who records an "accepted" advertisement can rebroadcast it. DoorBot mitigates
  this only weakly, by requiring a strictly advancing counter and rate limiting.
- **No code identity.** You cannot tell a cleaner's code from your own, so
  per-person logging and per-person schedules are impossible via the keypad path.
- **Programming PINs still needs SwitchBot.** The passcode table lives on the
  keypad and is provisioned by the SwitchBot app, which expects the keypad to be
  paired to a real SwitchBot Lock.

**Recommendation.** If you want proper per-user codes, schedules and an audit
trail, drive PIN entry through DoorBot itself (dashboard keypad card, a wired
keypad, or NFC) and use `POST /api/verify`. Use the SwitchBot keypad as a
secondary convenience, ideally with **"Require a DoorBot PIN as well"** enabled.

## Finding your keypad's BLE address

```bash
# On a Linux box with Bluetooth
sudo hcitool lescan --duplicates | grep -i wokeypad
# or watch raw adverts
sudo btmon | grep -A5 "0969"
```

Then put the address into `keypad_mac` in `esphome/doorbot.yaml`.
