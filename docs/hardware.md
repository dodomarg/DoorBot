# Hardware & wiring

## Bill of materials

| Part | Notes |
|---|---|
| Seeed **XIAO ESP32S3** | The plain model is fine; Sense also works. Needs BLE for the keypad. |
| Seeed **Bus Servo Driver Board for XIAO** | a.k.a. *XIAO Bus Servo Adapter*. Does the half-duplex bus conversion. |
| **Feetech ST3215 or ST3235** serial bus servo | Both take 6–12.6 V; 12 V gives full torque. The ST3235 has steel gears and is the better choice for a lock |
| DC PSU, **5.5 × 2.1 mm barrel** | 5–12 V, matching the servo. ~2 A headroom for stall current. |
| USB-C supply for the XIAO | The XIAO is powered **separately** from the servo |
| Thumbturn adapter / bracket | See `hardware/freecad/DoorBot.FCStd` |
| SwitchBot Keypad *(optional)* | Non-touch model, `WoKeypad` |

## Wiring

The XIAO **plugs directly into the driver board's headers** — no dupont wires
are needed for the UART.

| XIAO ESP32S3 | Signal |
|---|---|
| **D6 / GPIO43** | UART **TX** → servo bus |
| **D7 / GPIO44** | UART **RX** ← servo bus |

These are the XIAO's default UART pins. The Seeed wiki's own example code is:

```cpp
#define S_RXD D7
#define S_TXD D6
COMSerial.begin(1000000, SERIAL_8N1, S_RXD, S_TXD);
```

Arduino-ESP32's signature is `begin(baud, config, rxPin, txPin)`, so **host RX =
D7, host TX = D6**.

> ⚠️ The prose further down the same wiki page states the opposite mapping. The
> code example was treated as authoritative here. If the servo never answers a
> ping, swap `tx_pin` and `rx_pin` in `esphome/doorbot.yaml` — that costs one
> reflash and is the single most likely wiring mistake.

### Jumper

There is a 2-pin header at the front of the board with a 2.54 mm jumper cap that
selects UART mode. It is **shorted by default** — leave it on.

### Power

- **Servo power** goes into the barrel jack: 5–12 V, matched to the servo.
  ST-series ≈ 9 V, SC-series ≈ 12 V. Check your specific servo variant.
- **The XIAO needs its own supply** over USB-C. Don't try to run it from the
  servo rail.
- Give the supply real current headroom. A stalled ST3215/ST3235 pulls far more than
  its idle draw, and a browning-out ESP32 mid-turn is exactly what you don't
  want in a lock.

Reference: [Seeed wiki — XIAO Bus Servo Adapter](https://wiki.seeedstudio.com/xiao_bus_servo_adapter/)
· [schematic PDF](https://files.seeedstudio.com/wiki/bus_servo_driver_board/202004237_Servo_Driver_Board_for_Seeed_Studio_XIAO_SCH_PDF_250225.pdf)

## Mechanical

`hardware/freecad/DoorBot.FCStd` holds the bracket/enclosure model. The key
constraints:

- The servo horn must couple to the thumbturn with **no backlash** — slop shows
  up as an unreliable "is it locked?" reading, because DoorBot infers state from
  the servo's position.
- Leave the door openable by hand from the inside. Either use a coupler that
  slips, or accept that turning the thumbturn by hand back-drives the servo
  (harmless when torque is disabled, which is what the **Release servo** button
  in the calibration wizard does).
- Mount rigidly. If the bracket flexes, the captured locked/unlocked positions
  drift and you'll be recalibrating every week.

## First power-on checklist

1. Servo connected to the **bus** port, PSU in the barrel jack, XIAO on USB-C.
2. Flash `esphome/doorbot.yaml`.
3. Check the **Servo online** binary sensor in Home Assistant. If it's off:
   - swap `tx_pin` / `rx_pin`
   - confirm the servo is at the factory ID **1** and **1,000,000 baud**
   - confirm the jumper cap is fitted
4. Watch the **Position** sensor while turning the thumbturn by hand with torque
   off. It should sweep smoothly through 0–4095.
5. Only then run the calibration wizard.
