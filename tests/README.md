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

59 checks covering the calibration wizard, lock/unlock, multi-turn travel,
holding verification, hold-open and its fail-secure release, every PIN code type,
one-time burning, per-source rate limiting, the SwitchBot keypad credential
path (method/slot identity, raw method bytes, day and hour windows, duress,
debouncing, lockdown mode), jam detection and stickiness, and the event log.

The suite clears existing codes and credentials before it runs and uses a
per-run source id for the rate-limit tests, so it is safe to run repeatedly
against the same server.
