#pragma once
#include <cstdint>

// Torque safety policy, deliberately kept as a pure function of plain values.
//
// This is the one piece of DoorBot where a bug can physically trap a person, so
// it is written to be tested rather than reasoned about. It touches no
// hardware, no globals and no clock: everything it needs is passed in, so the
// host-side test in tests/safety_policy_test.cpp can drive it through millis()
// rollover, a corrupted deadline, a clock that jumps backwards and a clock that
// has stopped entirely.
//
// The invariant: torque may be on only while a move is running or while an
// explicitly granted, time-bounded hold is running. Every energised period ends
// by a deadline this policy owns. When inputs are self-contradictory the answer
// is always to release.

namespace esphome {
namespace feetech_servo {

enum class SafetyAction : uint8_t {
  NONE,                  ///< Current state is legitimate; do nothing.
  ABORT_MOVE,            ///< Move overran its budget: abort it and release.
  RELEASE_HOLD_EXPIRED,  ///< A granted hold has ended.
  RELEASE_AT_REST,       ///< Torque is on with nothing justifying it.
};

struct SafetyState {
  uint32_t now;              ///< millis()
  bool moving;               ///< a move is in progress
  bool torque_on;            ///< last known torque state
  uint32_t torque_since;     ///< millis() when torque was energised
  bool hold_active;          ///< a bounded hold has been granted
  uint32_t hold_until;       ///< millis() deadline of that hold
  uint32_t hold_started;     ///< millis() when the hold began
  uint32_t energised_loops;  ///< loops elapsed since torque was energised
};

struct SafetyLimits {
  uint32_t max_energised_ms;   ///< longest a move may stay energised
  uint32_t release_grace_ms;   ///< settle time before torque at rest is a fault
  uint32_t max_hold_ms;        ///< absolute ceiling on any hold
  uint32_t stuck_clock_loops;  ///< loops with a frozen clock before we give up
};

/// Unsigned subtraction is wrap-safe: it stays correct across the ~49.7 day
/// millis() rollover as long as the interval itself is under 2^32 ms.
inline uint32_t elapsed_since(uint32_t now, uint32_t then) { return now - then; }

/// Wrap-safe "is now at or past deadline", correct for intervals under ~24.8
/// days either side. A plain now >= deadline would be wrong across a rollover.
inline bool deadline_passed(uint32_t now, uint32_t deadline) {
  return static_cast<int32_t>(now - deadline) >= 0;
}

inline SafetyAction evaluate_safety(const SafetyState &s, const SafetyLimits &l) {
  // A clock that has not advanced by even a millisecond across thousands of
  // loops is stopped. No deadline derived from it will ever fire, so time-based
  // reasoning is abandoned and the servo is released.
  const uint32_t energised_ms = elapsed_since(s.now, s.torque_since);
  const bool clock_stalled = s.energised_loops > l.stuck_clock_loops && energised_ms == 0;

  if (s.moving) {
    if (clock_stalled)
      return SafetyAction::ABORT_MOVE;
    if (energised_ms > l.max_energised_ms)
      return SafetyAction::ABORT_MOVE;
    return SafetyAction::NONE;
  }

  if (!s.torque_on)
    return SafetyAction::NONE;

  if (s.hold_active) {
    if (clock_stalled)
      return SafetyAction::RELEASE_HOLD_EXPIRED;
    // Three independent reasons to end a hold, because relying on one value
    // being sane is how a door stays clamped. The granted deadline is the
    // normal path; the elapsed-time ceiling does not trust that deadline; and
    // a hold that appears to have started in the future (clock stepped
    // backwards) yields a huge elapsed value and so also releases.
    if (deadline_passed(s.now, s.hold_until))
      return SafetyAction::RELEASE_HOLD_EXPIRED;
    if (elapsed_since(s.now, s.hold_started) > l.max_hold_ms)
      return SafetyAction::RELEASE_HOLD_EXPIRED;
    return SafetyAction::NONE;
  }

  // Torque on, no move, no granted hold. This is the case the wizard's old
  // "Hold position" button produced, and it is refused here rather than in the
  // interface: whatever asked for it, the servo is let go.
  if (energised_ms < l.release_grace_ms && !clock_stalled)
    return SafetyAction::NONE;
  return SafetyAction::RELEASE_AT_REST;
}

}  // namespace feetech_servo
}  // namespace esphome
