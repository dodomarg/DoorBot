# Network and firewall

DoorBot's ESP32 needs very little network access. The keypad link is Bluetooth
LE, so it never touches the network at all — no firewall rule can affect it, and
the lock keeps working over BLE even with the network completely down.

Nothing ever needs to reach the lock from the internet. There is no port
forward, and no inbound WAN rule.

## Permanent rules

| Rule | Protocol | Port | Direction | Required |
| --- | --- | --- | --- | --- |
| ESPHome native API | TCP | 6053 | Home Assistant → lock | Yes |
| mDNS discovery | UDP | 5353 | multicast, both ways | Only if using `doorbot.local` |
| ESPHome OTA | TCP | 3232 | ESPHome dashboard → lock | Only for wireless updates |
| Setup wizard UI | TCP | 80 | your PC or phone → lock | Only during pairing |

The API connection is **inbound to the lock**: Home Assistant is the client and
the lock only listens. The lock never opens a connection to Home Assistant, so
it can live in a locked-down IoT VLAN with no ability to initiate anything
towards your trusted network.

### Watch out for the API reboot timeout

`api.reboot_timeout` defaults to 15 minutes: if no client connects for that
long, the device reboots itself. A firewall rule that blocks
`Home Assistant → lock:6053` therefore does not merely make the lock
unavailable, it puts it into a reboot loop every 15 minutes. If you see the
uptime sensor resetting on a 15 minute cycle, check this rule first.

### mDNS across VLANs

`wifi.use_address` is `doorbot.local`, which relies on mDNS. mDNS is link-local
multicast to `224.0.0.251` and **does not cross VLANs** on its own. If the lock
and Home Assistant are on different VLANs you have two options:

- Run an mDNS repeater between the two VLANs (on pfSense, the Avahi package).
- Skip mDNS entirely: give the lock a DHCP reservation and set
  `use_address` to that IP, then add the device to Home Assistant by IP.

The second option is simpler and removes a moving part. mDNS is then only a
convenience for the ESPHome dashboard.

## One-time pairing

Pairing the SwitchBot keypad is the only step that needs the internet, and only
once. The wizard signs into your SwitchBot account to fetch the keypad's
communication key, then generates a fresh session key on the ESP32 itself.

| Purpose | Protocol | Port | Destination |
| --- | --- | --- | --- |
| SwitchBot account | TCP | 443 | `account.api.switchbot.net` |
| SwitchBot device and key API | TCP | 443 | `wonderlabs.<region>.api.switchbot.net` |
| DNS | UDP | 53 | your resolver |
| NTP | UDP | 123 | `pool.ntp.org` or a local server |

NTP matters more than it looks: the HTTPS requests above validate certificates,
which fails if the clock is wrong, and the ESP32 has no battery-backed clock. If
you block outbound NTP, point `time.sntp.servers` at a local NTP server instead.

After pairing you can drop all four of these rules. Everything from then on is
local: the keypad talks BLE to the lock, and the lock talks to Home Assistant
over the local API.

## Suggested VLAN layout

If the lock lives on an isolated IoT VLAN:

- Allow `Home Assistant → lock` on TCP 6053, and on 3232 if you want OTA.
- Allow `admin PC → lock` on TCP 80 while pairing, then remove it.
- Allow the lock outbound to the internet on TCP 443, UDP 53 and UDP 123 while
  pairing, then remove it.
- Deny everything else, including any lock-initiated traffic to your trusted
  networks. Nothing in DoorBot needs it.

## Provisioning Wi-Fi over USB

Wi-Fi credentials reach the device two ways, and it is worth knowing which one
is in force.

**Compiled in.** `wifi.ssid`/`wifi.password` come from `esphome/secrets.yaml`
via `!secret`, so they are baked into the image at build time. A factory flash
erases NVS, so the compiled credentials are what the device falls back to. If
`secrets.yaml` still holds the `secrets.yaml.example` placeholders, the device
will boot and hunt for a network that does not exist:

```
[W][wifi:1542]: No matching network found
[I][wifi:1128]: Connecting to 'TestNet' (any) ...
[W][wifi_esp32:865]: Disconnected reason='Probe Request Unsuccessful'
```

That message means the credentials were never set, not that they were lost.

**Provisioned at runtime.** `improv_serial:` accepts credentials over the USB
cable and stores them in NVS, with no reflash. Either use the *Configure Wi-Fi*
button at <https://web.esphome.io>, or run the bundled CLI tool:

```bash
tools/improv_provision.py --port /dev/ttyACM0 --ssid 'MyNetwork' --password 'secret'
```

Runtime provisioning survives OTA updates but **not** a factory flash over USB.
Fix `secrets.yaml` as well, or the next cable flash reverts to the placeholder.

The ESP32-S3 has no 5 GHz radio, so the SSID must be reachable on 2.4 GHz.

## Finding the device after it connects

The lock is deliberately not discoverable from a trusted network when the two
are on separate VLANs — mDNS is link-local multicast to `224.0.0.251` and does
not cross a router, so `doorbot.local` will not resolve from another subnet.

Scan for the API port instead, then give the lock a DHCP reservation:

```bash
nmap -Pn -p 6053 --open 10.0.20.0/24
```

Once you have the address, set `use_address:` in `doorbot.yaml` (or add the
ESPHome integration by IP) so Home Assistant does not depend on mDNS at all.
