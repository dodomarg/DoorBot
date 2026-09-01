# Tests

Two suites. Run both.

## Torque safety policy (host, no hardware, no server)

The firmware's torque decision lives in a pure function so it can be compiled
and driven on a PC. This is the one that matters most: it proves the servo is
always released, and that a hold can never outlive its ceiling, under clock
conditions that are impractical to reproduce on the device.

```bash
g++ -std=c++17 -Wall -Wextra -o /tmp/safety_test tests/safety_policy_test.cpp && /tmp/safety_test
```

27 checks: the 49.7-day `millis()` rollover, a stopped clock, a clock stepped
backwards, a corrupted deadline, and an exhaustive sweep asserting that a 60 s
hold always ends, never exceeds the ceiling, and that torque with no
justification is released at every point on the clock.

## End-to-end (add-on HTTP API)

Drives the running add-on over its HTTP API using the `mock` backend. No
hardware, no third-party packages.

```bash
# terminal 1 - start the add-on against a throwaway database
cd doorbot/rootfs/opt/doorbot
DOORBOT_DATA=/tmp/doorbot-data DOORBOT_PORT=8099 python3 -m app

# terminal 2
python3 tests/e2e.py
```

113 checks covering the calibration wizard, lock/unlock, multi-turn travel,
bounded holds and the refusal of indefinite ones, hold-open and its fail-secure
release, every PIN code type, one-time burning, per-source rate limiting, the
SwitchBot keypad credential path (method/slot identity, raw method bytes, day
and hour windows, duress, debouncing, lockdown mode), jam detection and
stickiness, and the event log.

The suite clears existing codes and credentials before it runs and uses a
per-run source id for the rate-limit tests, so it is safe to run repeatedly
against the same server.
