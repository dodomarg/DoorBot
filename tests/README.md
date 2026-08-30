# Tests

End-to-end tests that drive the running add-on over its HTTP API using the
`mock` backend. No hardware, no third-party packages.

```bash
# terminal 1 - start the add-on against a throwaway database
cd doorbot/rootfs/opt/doorbot
DOORBOT_DATA=/tmp/doorbot-data DOORBOT_PORT=8099 python3 -m app

# terminal 2
python3 tests/e2e.py
```

24 checks covering the calibration wizard, lock/unlock, every PIN code type,
one-time burning, per-source rate limiting, the SwitchBot keypad counter
(including both 255-wraparound cases), jam detection and stickiness, and the
event log.
