# tools

Host-side helpers. Nothing here runs on the ESP32.

## `improv_provision.py`

Sets the device's Wi-Fi credentials over USB using the [Improv Serial][improv]
protocol, without reflashing. The credentials are stored in the ESP32's NVS
partition, so they survive OTA updates but not a factory flash over USB.

Requires `improv_serial:` in `doorbot.yaml` (it is enabled by default) and
`pyserial` on the host.

```bash
# credentials on the command line
tools/improv_provision.py --port /dev/ttyACM0 --ssid 'MyNetwork' --password 'secret'

# or from a file, to keep them out of your shell history
printf 'SSID=MyNetwork\nPASS=secret\n' > ~/.config/doorbot/wifi.env
chmod 600 ~/.config/doorbot/wifi.env
tools/improv_provision.py --env ~/.config/doorbot/wifi.env
```

Add `--verbose` to echo the device's own log output while provisioning, which
is useful when the association fails and you want the Wi-Fi stack's reason code.

Exit codes: `0` provisioned, `1` the device reported an Improv error (most
often `UNABLE_TO_CONNECT`, meaning wrong password or no matching SSID), `2`
timed out with no response.

### Why not just use web.esphome.io

The browser flasher does the same thing, and is the right answer for end users.
This exists so provisioning can be scripted, and so a failure prints a specific
Improv error code rather than a spinner.

### Notes

The device streams ordinary log text on the same port, so the reader has to
locate frames within that noise. It searches for the `IMPROV` magic, checks the
version byte, then validates the checksum before accepting a frame — the string
`IMPROV` appearing inside a log line is rejected rather than parsed as a header.

Frame layout, from `Improv/src/improv.cpp`:

```
'I' 'M' 'P' 'R' 'O' 'V' | version | type | data_len | data... | checksum | '\n'
```

`checksum` is the low byte of the sum of every byte from `'I'` through the last
data byte. For an RPC the data is `[command, payload_len, *args]`, and
`WIFI_SETTINGS` args are `[ssid_len, *ssid, password_len, *password]`.

[improv]: https://www.improv-wifi.com/serial/
