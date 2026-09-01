// Host-side tests for the torque safety policy.
//
// Build and run from the repo root:
//   g++ -std=c++17 -Wall -Wextra -o /tmp/safety_test tests/safety_policy_test.cpp
//   /tmp/safety_test
//
// These run on the PC, not the ESP32, so the clock can be driven anywhere:
// through the 49.7-day millis() rollover, backwards, or stopped dead. The
// question every case asks is the same one that matters on a door -- does the
// servo end up released?

#include "../esphome/components/feetech_servo/safety_policy.h"

#include <cstdio>
#include <cstdint>
#include <string>

using esphome::feetech_servo::deadline_passed;
using esphome::feetech_servo::elapsed_since;
using esphome::feetech_servo::evaluate_safety;
using esphome::feetech_servo::SafetyAction;
using esphome::feetech_servo::SafetyLimits;
using esphome::feetech_servo::SafetyState;

static int failures = 0;
static int checks = 0;

static const char *name_of(SafetyAction a) {
  switch (a) {
    case SafetyAction::NONE: return "NONE";
    case SafetyAction::ABORT_MOVE: return "ABORT_MOVE";
    case SafetyAction::RELEASE_HOLD_EXPIRED: return "RELEASE_HOLD_EXPIRED";
    case SafetyAction::RELEASE_AT_REST: return "RELEASE_AT_REST";
  }
  return "?";
}

static void ok(const std::string &what, SafetyAction got, SafetyAction want) {
  checks++;
  if (got == want) {
    printf("PASS %s\n", what.c_str());
  } else {
    printf("FAIL %s (got %s, wanted %s)\n", what.c_str(), name_of(got), name_of(want));
    failures++;
  }
}

static void ok_true(const std::string &what, bool cond) {
  checks++;
  if (cond) {
    printf("PASS %s\n", what.c_str());
  } else {
    printf("FAIL %s\n", what.c_str());
    failures++;
  }
}

// The values the firmware actually ships with.
static const SafetyLimits LIMITS{15000, 750, 60000, 10000};

// Torque on, nothing else set. Individual cases override what they care about.
static SafetyState energised(uint32_t now, uint32_t since) {
  SafetyState s{};
  s.now = now;
  s.torque_on = true;
  s.torque_since = since;
  return s;
}

int main() {
  // ---------------------------------------------------------- arithmetic
  ok_true("elapsed_since is wrap-safe across rollover",
          elapsed_since(50, 0xFFFFFFF0u) == 66);
  ok_true("deadline_passed is false just before the deadline",
          !deadline_passed(999, 1000));
  ok_true("deadline_passed is true on the deadline", deadline_passed(1000, 1000));
  ok_true("deadline_passed is wrap-safe when the deadline wrapped",
          deadline_passed(20, 0xFFFFFFF0u));
  ok_true("deadline_passed is wrap-safe just before a wrapped deadline",
          !deadline_passed(0xFFFFFFE0u, 0xFFFFFFF0u));

  // ------------------------------------------------------- normal moves
  {
    SafetyState s = energised(5000, 1000);
    s.moving = true;
    ok("a move inside its budget is left alone", evaluate_safety(s, LIMITS), SafetyAction::NONE);
  }
  {
    SafetyState s = energised(17000, 1000);
    s.moving = true;
    ok("a move past its energised budget is aborted", evaluate_safety(s, LIMITS),
       SafetyAction::ABORT_MOVE);
  }
  {
    // Torque energised at 20 ms before rollover, now 10 ms after it.
    SafetyState s = energised(10, 0xFFFFFFECu);
    s.moving = true;
    ok("a move spanning the millis() rollover is not falsely aborted",
       evaluate_safety(s, LIMITS), SafetyAction::NONE);
  }
  {
    // Same rollover, but genuinely over budget by 16 s.
    SafetyState s = energised(16000, 0xFFFFFFECu);
    s.moving = true;
    ok("a move spanning the rollover still aborts when genuinely overrun",
       evaluate_safety(s, LIMITS), SafetyAction::ABORT_MOVE);
  }

  // --------------------------------------------- torque at rest (the wizard)
  {
    SafetyState s = energised(1200, 1000);
    ok("torque just after a move is given its grace period",
       evaluate_safety(s, LIMITS), SafetyAction::NONE);
  }
  {
    SafetyState s = energised(2000, 1000);
    ok("torque at rest past the grace period is released",
       evaluate_safety(s, LIMITS), SafetyAction::RELEASE_AT_REST);
  }
  {
    // This is precisely the old "Hold position" button: torque enabled with no
    // move and no granted hold. It must not be possible to sustain it.
    SafetyState s = energised(4000000, 1000);
    ok("an indefinite hold request is refused by the policy itself",
       evaluate_safety(s, LIMITS), SafetyAction::RELEASE_AT_REST);
  }
  {
    SafetyState s{};
    s.now = 900000;
    s.torque_on = false;
    ok("a released servo needs no action", evaluate_safety(s, LIMITS), SafetyAction::NONE);
  }

  // ------------------------------------------------------- granted holds
  {
    SafetyState s = energised(11000, 1000);
    s.hold_active = true;
    s.hold_started = 1000;
    s.hold_until = 31000;
    ok("a hold inside its window is allowed", evaluate_safety(s, LIMITS), SafetyAction::NONE);
  }
  {
    SafetyState s = energised(31000, 1000);
    s.hold_active = true;
    s.hold_started = 1000;
    s.hold_until = 31000;
    ok("a hold is released the moment its deadline arrives",
       evaluate_safety(s, LIMITS), SafetyAction::RELEASE_HOLD_EXPIRED);
  }
  {
    // Hold granted 20 ms before rollover for 30 s; now 10 s past rollover.
    SafetyState s = energised(10000, 0xFFFFFFECu);
    s.hold_active = true;
    s.hold_started = 0xFFFFFFECu;
    s.hold_until = 0xFFFFFFECu + 30000;  // wraps
    ok("a hold spanning the millis() rollover is not cut short",
       evaluate_safety(s, LIMITS), SafetyAction::NONE);
  }
  {
    SafetyState s = energised(40000, 0xFFFFFFECu);
    s.hold_active = true;
    s.hold_started = 0xFFFFFFECu;
    s.hold_until = 0xFFFFFFECu + 30000;
    ok("a hold spanning the rollover still expires on time",
       evaluate_safety(s, LIMITS), SafetyAction::RELEASE_HOLD_EXPIRED);
  }

  // ----------------------------------------------- exotic fault conditions
  {
    // A corrupted or maliciously long deadline must not extend the hold: the
    // independent elapsed-time ceiling catches it.
    SafetyState s = energised(200000, 1000);
    s.hold_active = true;
    s.hold_started = 1000;
    s.hold_until = 0x7FFFFFFFu;  // ~24 days away
    ok("a corrupted deadline cannot extend a hold past the ceiling",
       evaluate_safety(s, LIMITS), SafetyAction::RELEASE_HOLD_EXPIRED);
  }
  {
    // Clock stepped backwards: the hold appears to start in the future.
    SafetyState s = energised(1000, 1000);
    s.hold_active = true;
    s.hold_started = 500000;
    s.hold_until = 530000;
    ok("a hold that appears to start in the future is released",
       evaluate_safety(s, LIMITS), SafetyAction::RELEASE_HOLD_EXPIRED);
  }
  {
    // Clock stopped: now never advances past the moment torque came on, so no
    // deadline can ever fire. The loop counter is the only way out.
    SafetyState s = energised(1000, 1000);
    s.hold_active = true;
    s.hold_started = 1000;
    s.hold_until = 31000;
    s.energised_loops = 10001;
    ok("a stopped clock still releases a hold", evaluate_safety(s, LIMITS),
       SafetyAction::RELEASE_HOLD_EXPIRED);
  }
  {
    SafetyState s = energised(1000, 1000);
    s.moving = true;
    s.energised_loops = 10001;
    ok("a stopped clock still aborts a move", evaluate_safety(s, LIMITS),
       SafetyAction::ABORT_MOVE);
  }
  {
    SafetyState s = energised(1000, 1000);
    s.energised_loops = 10001;
    ok("a stopped clock still releases torque at rest", evaluate_safety(s, LIMITS),
       SafetyAction::RELEASE_AT_REST);
  }
  {
    // A healthy clock that has simply run many loops in a short time must not
    // be mistaken for a stopped one.
    SafetyState s = energised(1100, 1000);
    s.hold_active = true;
    s.hold_started = 1000;
    s.hold_until = 31000;
    s.energised_loops = 500000;
    ok("a fast loop with a live clock is not mistaken for a stopped clock",
       evaluate_safety(s, LIMITS), SafetyAction::NONE);
  }
  {
    // hold_active set but the deadline left at zero, e.g. partially
    // initialised state. Must fail closed.
    SafetyState s = energised(90000, 1000);
    s.hold_active = true;
    s.hold_started = 0;
    s.hold_until = 0;
    ok("a hold with an uninitialised deadline releases", evaluate_safety(s, LIMITS),
       SafetyAction::RELEASE_HOLD_EXPIRED);
  }

  // ------------------------------------- exhaustive sweep: no hold runs forever
  {
    // Walk a 60 s hold in 250 ms steps from an origin just before rollover and
    // assert it always ends, and never later than the ceiling.
    const uint32_t start = 0xFFFFFF00u;
    bool released = false;
    uint32_t released_after = 0;
    for (uint32_t dt = 0; dt <= 120000; dt += 250) {
      SafetyState s = energised(start + dt, start);
      s.hold_active = true;
      s.hold_started = start;
      s.hold_until = start + 60000;
      if (evaluate_safety(s, LIMITS) == SafetyAction::RELEASE_HOLD_EXPIRED) {
        released = true;
        released_after = dt;
        break;
      }
    }
    ok_true("a 60 s hold across the rollover always ends", released);
    ok_true("and ends no later than the 60 s ceiling", released && released_after <= 60000);
  }
  {
    // Every combination of "torque is on" with no justification must release,
    // whatever the clock is doing.
    bool always = true;
    for (uint32_t now = 0; now < 0xFFFFFFFFu - 5000000u; now += 997000u) {
      SafetyState s = energised(now, now - 5000);
      if (evaluate_safety(s, LIMITS) != SafetyAction::RELEASE_AT_REST)
        always = false;
    }
    ok_true("unjustified torque is released at every point on the clock", always);
  }

  printf("\n%d checks, %d failures\n", checks, failures);
  if (failures == 0)
    printf("ALL SAFETY POLICY TESTS PASSED\n");
  return failures == 0 ? 0 : 1;
}
