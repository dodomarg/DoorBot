# SwitchBot Keypad — how DoorBot talks to it

DoorBot's ESP32 **impersonates a SwitchBot Lock**. The keypad pairs to it and
talks the real, encrypted lock protocol — so DoorBot learns not just *that*
someone unlocked the door, but *who*.

## The short version

| Question | Answer |
|---|---|
| Does the keypad validate the PIN itself? | **Yes** — the passcode table lives on the keypad |
| How does it tell the lock? | An **AES-CTR encrypted** BLE GATT frame |
| Do we learn which credential was used? | **Yes** — method + credential slot |
| Do we need a SwitchBot **Lock**? | **No** |
| Do we need a SwitchBot **account**? | **Yes**, once, during setup |
| Cloud needed day to day? | **No** — fully local after pairing |

## Why not just sniff the advertisement?

The keypad also broadcasts an unencrypted status advertisement containing an
`attempt_state` counter that goes up by 1 on a rejected PIN and 2 on an accepted
one. DoorBot originally used exactly that, and it worked — but it has two fatal
flaws for a door lock:

- **It's replayable.** No nonce, no timestamp, no signature. Record an
  "accepted" advert, rebroadcast it later, door opens.
- **It's anonymous.** It never says which PIN was entered, so per-person
  schedules and a real audit trail are impossible.

The advertisement is a *status beacon*, not the command channel. The real
command channel is encrypted, so DoorBot uses that instead and the sniffing path
was removed entirely.

## The real exchange

```
User enters PIN
        │
        ▼
┌──────────────────┐   validates the PIN against its own table
│  SwitchBot       │
│  Keypad          │   then opens a GATT connection to its paired "lock"
└────────┬─────────┘
         │  AES-CTR encrypted frame
         │  [ header(4) | method | 0x80 | slot | … ]
         ▼
┌──────────────────┐   decrypts, decodes, actuates the servo
│  DoorBot ESP32   │
│  (pretends to be │   replies with an encrypted lock-style ACK
│   a SwitchBot    │
│   Lock)          │   fires an ESPHome event → Home Assistant
└──────────────────┘
```

For the keypad to accept a device as its lock, that device must advertise like a
SwitchBot Lock, answer the lock GATT protocol, and carry a MAC in SwitchBot's
`B0:E9:FE` OUI. The bridge component spoofs all three.

### Frame layout

From [`lock_protocol.cpp`](https://github.com/pierluigizagaria/switchbot-keypad-bridge/blob/main/components/switchbot_keypad_bridge/lock_protocol.cpp):

```cpp
constexpr uint8_t FRAME_LOCK[8]              = {0x0F, 0x4E, 0x01, 0x03, 0x00, 0x00, 0x00, 0x00};
constexpr uint8_t FRAME_ACTION[4]            = {0x0F, 0x4E, 0x01, 0x03};
constexpr uint8_t FRAME_STATE_POLL_PREFIX[3] = {0x0F, 0x4F, 0x81};
constexpr uint8_t FRAME_DOORBELL[2]          = {0x01, 0x03};

// Unlock frame: [hdr(4) | method | marker(0x80) | index | ...]
constexpr size_t  UNLOCK_METHOD_OFFSET = 4;
constexpr size_t  UNLOCK_MARKER_OFFSET = 5;
constexpr size_t  UNLOCK_INDEX_OFFSET  = 6;
constexpr uint8_t UNLOCK_MARKER        = 0x80;
constexpr uint8_t UNLOCK_INDEX_BASE    = 0x0A;
```

### Method byte

| Byte | Method |
|---|---|
| `0x04` | PIN |
| `0x08` | NFC tag |
| `0x0C` | Fingerprint |
| `0x18` | Face |

DoorBot's `app/keypad.py` mirrors this table in `METHOD_BYTES`, and
`normalise_method()` accepts either the byte or the name.

### Credential slot

The index byte is the slot the SwitchBot app assigned when the credential was
added — first credential is 0, second is 1, and so on. The original Keypad
biases it by `0x0A`; Keypad Vision appears to send it raw, so the decoder treats
a byte `>= 0x0A` as biased and anything lower as a raw index.

**Method and slot together form the identity.** `fingerprint:0` and `pin:0` are
different credentials, which is why DoorBot keys its credential records on
`method:slot`.

## Setup, and why it touches the cloud once

The keypad's pairing handshake is encrypted with a **communication key issued
and stored by SwitchBot's servers**. It isn't in the app and can't be read off
the keypad. So the one-time wizard:

1. Signs in to your SwitchBot account from the ESP32 over HTTPS.
2. Fetches that communication key.
3. Uses it once to complete the handshake, injecting a **fresh AES-128 session
   key generated on the ESP32**.
4. Stores the session key in NVS — never in your YAML or git.

After that, nothing contacts the cloud. Unlocks, doorbell presses and battery
readings are pure local BLE.

## What this means for DoorBot

Because the frame identifies the credential, DoorBot can do real access control
on top of what the keypad already enforces:

- **Name each slot** — the log reads "Maya, fingerprint" instead of "slot 2".
- **Schedule per credential** — days of the week plus a time window, so the
  cleaner's PIN only works Tuesday mornings.
- **Disable one credential** without touching the others or reprogramming the
  keypad.
- **Flag a slot as duress** — it opens the door but raises an alert.
- **Lock down to known slots** — `known_credentials_only` refuses any slot you
  haven't named in DoorBot.

The keypad still authenticates; DoorBot authorises. Those are two different
questions and it's useful to have both.

## Home Assistant's native support

HA's `switchbot` integration supports Keypad Vision and Keypad Vision Pro, but
not the plain `WoKeypad` — it isn't in `SUPPORTED_MODEL_TYPES` in
`homeassistant/components/switchbot/const.py`. Either way, HA only ever reads
the *advertisement*, so it can't identify a credential. The bridge is a strictly
better source of truth.

## Credits and sources

- [pierluigizagaria/switchbot-keypad-bridge](https://github.com/pierluigizagaria/switchbot-keypad-bridge)
  — the ESPHome component doing the lock impersonation. DoorBot depends on it.
- [sblibs/pySwitchbot](https://github.com/sblibs/pySwitchbot) — the advertisement
  parsers, and the library Home Assistant uses.

## Appendix: the advertisement, for reference

Still useful for spotting a keypad on the air, even though DoorBot no longer
acts on it.

| Field | Where | Meaning |
|---|---|---|
| Service UUID | `0000fd3d-0000-1000-8000-00805f9b34fb` | SwitchBot service data |
| `service_data[0]` | `'y'` / `'Y'` | model character = Keypad |
| `service_data[2] & 0x7F` | 0–100 | battery percentage |
| Manufacturer ID | `2409` (`0x0969`) | SwitchBot company ID |
| `manufacturer_data[6]` | 0–255 | `attempt_state`, a wrapping counter |

```bash
# find your keypad on the air
sudo hcitool lescan --duplicates | grep -i wokeypad
sudo btmon | grep -A5 "0969"
```
