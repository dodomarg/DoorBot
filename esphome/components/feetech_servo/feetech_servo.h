#pragma once

#include "esphome/core/component.h"
#include "esphome/core/optional.h"
#include "esphome/components/uart/uart.h"
#ifdef USE_SENSOR
#include "esphome/components/sensor/sensor.h"
#endif
#ifdef USE_BINARY_SENSOR
#include "esphome/components/binary_sensor/binary_sensor.h"
#endif
#ifdef USE_TEXT_SENSOR
#include "esphome/components/text_sensor/text_sensor.h"
#endif

namespace esphome {
namespace feetech_servo {

// Feetech SMS/STS serial bus protocol ("protocol 0").
// Frame: 0xFF 0xFF | ID | LEN | INSTRUCTION | PARAMS... | CHECKSUM
//   LEN      = number of params + 2
//   CHECKSUM = ~(ID + LEN + INSTRUCTION + sum(PARAMS)) & 0xFF
// 16-bit values are little-endian (low byte first).
static const uint8_t STS_HEADER = 0xFF;
static const uint8_t STS_INST_PING = 0x01;
static const uint8_t STS_INST_READ = 0x02;
static const uint8_t STS_INST_WRITE = 0x03;

// Control table addresses. Verified against the official Feetech/Waveshare
// "ST3215 memory register map" V3.7 spreadsheet and cross-checked with
// huggingface/lerobot's STS_SMS_SERIES_CONTROL_TABLE.
static const uint8_t STS_REG_MODEL_NUMBER = 3;  // read-only, 2 bytes
static const uint8_t STS_REG_ID = 5;
static const uint8_t STS_REG_BAUD_RATE = 6;
static const uint8_t STS_REG_MIN_POSITION_LIMIT = 9;   // "Minimum angle"
static const uint8_t STS_REG_MAX_POSITION_LIMIT = 11;  // "Maximum angle"
static const uint8_t STS_REG_MAX_TEMPERATURE = 13;
static const uint8_t STS_REG_MAX_TORQUE_LIMIT = 16;
static const uint8_t STS_REG_UNLOADING_CONDITION = 19;
static const uint8_t STS_REG_PROTECTION_CURRENT = 28;
static const uint8_t STS_REG_HOMING_OFFSET = 31;  // "Position correction"
static const uint8_t STS_REG_OPERATING_MODE = 33;
static const uint8_t STS_REG_PROTECTIVE_TORQUE = 34;
static const uint8_t STS_REG_PROTECTION_TIME = 35;
static const uint8_t STS_REG_OVERLOAD_TORQUE = 36;
static const uint8_t STS_REG_TORQUE_ENABLE = 40;
static const uint8_t STS_REG_ACCELERATION = 41;
static const uint8_t STS_REG_GOAL_POSITION = 42;
static const uint8_t STS_REG_GOAL_VELOCITY = 46;
static const uint8_t STS_REG_TORQUE_LIMIT = 48;
static const uint8_t STS_REG_LOCK = 55;
static const uint8_t STS_REG_PRESENT_POSITION = 56;
static const uint8_t STS_REG_PRESENT_VELOCITY = 58;
static const uint8_t STS_REG_PRESENT_LOAD = 60;
static const uint8_t STS_REG_PRESENT_VOLTAGE = 62;
static const uint8_t STS_REG_PRESENT_TEMPERATURE = 63;
static const uint8_t STS_REG_STATUS = 65;
static const uint8_t STS_REG_MOVING = 66;
static const uint8_t STS_REG_PRESENT_CURRENT = 69;

// Register 40 accepts 128 as a command: "arbitrary current position correction
// to 2048", i.e. re-zero the servo on wherever it happens to be sitting.
static const uint8_t STS_TORQUE_OFF = 0;
static const uint8_t STS_TORQUE_ON = 1;
static const uint8_t STS_TORQUE_RECENTER = 128;

// How long the servo may sit still, short of a goal it is being fed, before a
// seek concludes it has run into the mechanical end stop.
static const uint32_t STS_SEEK_STALL_MS = 400;

// Operating modes (register 33).
static const uint8_t STS_MODE_POSITION = 0;
static const uint8_t STS_MODE_VELOCITY = 1;
static const uint8_t STS_MODE_PWM = 2;
static const uint8_t STS_MODE_STEP = 3;

// Servo status (register 65) and unloading condition (register 19) share this
// bit layout.
static const uint8_t STS_ERR_VOLTAGE = 1 << 0;
static const uint8_t STS_ERR_SENSOR = 1 << 1;
static const uint8_t STS_ERR_TEMPERATURE = 1 << 2;
static const uint8_t STS_ERR_CURRENT = 1 << 3;
static const uint8_t STS_ERR_ANGLE = 1 << 4;
static const uint8_t STS_ERR_OVERLOAD = 1 << 5;

// 12-bit encoder: 0..4095 covers a full turn.
static const int32_t STS_RESOLUTION = 4096;
// Multi-turn travel limit imposed by the goal-position register (+/- ~7.5 turns).
static const int32_t STS_MULTITURN_MIN = -30719;
static const int32_t STS_MULTITURN_MAX = 30719;
// Register 46 tops out here; 50 steps/s is about 0.732 RPM.
static const uint16_t STS_MAX_SPEED = 3400;
static const uint16_t STS_MAX_TORQUE = 1000;

/// Outcome of a commanded move. Anything other than MOVING is terminal.
enum class MoveResult : uint8_t {
  IDLE = 0,
  MOVING,
  ARRIVED,
  JAMMED,
  TIMEOUT,
  OFFLINE,
  ABORTED,
};

const char *move_result_to_string(MoveResult result);

// Model numbers reported by register 3. Every SMS/STS servo shares one control
// table, a 4096-step encoder and protocol 0, so an unrecognised STS model is
// still driven correctly -- the number is reported for diagnostics rather than
// used to gate behaviour. The SCS series is the genuine incompatibility: it is
// protocol 1, big-endian, and 1024 steps per revolution, so its positions would
// be silently wrong here. Numbers cross-checked against
// huggingface/lerobot's MODEL_NUMBER_TABLE.
static const uint16_t STS_MODEL_STS3215 = 777;
static const uint16_t STS_MODEL_STS3250 = 2825;
static const uint16_t STS_MODEL_SM8512BL = 11272;
static const uint16_t SCS_MODEL_SCS0009 = 1284;

/// Human-readable name for a model number, or "unknown" if not recognised.
const char *model_number_to_string(uint16_t model);

/// True for model numbers known to use the incompatible SCSCL protocol.
bool model_is_scs_series(uint16_t model);

class FeetechServo : public PollingComponent, public uart::UARTDevice {
 public:
  void setup() override;
  void loop() override;
  void update() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::DATA; }

  void set_servo_id(uint8_t id) { this->servo_id_ = id; }
  void set_default_speed(uint16_t speed) { this->default_speed_ = speed; }
  void set_default_acceleration(uint8_t accel) { this->default_acceleration_ = accel; }
  void set_multi_turn(bool enabled) { this->want_multi_turn_ = enabled; }
  void set_tolerance(uint16_t steps) { this->tolerance_ = steps; }
  void set_jam_load(uint16_t load) { this->jam_load_ = load; }
  void set_move_timeout(uint32_t ms) { this->move_timeout_ms_ = ms; }

#ifdef USE_SENSOR
  void set_position_sensor(sensor::Sensor *s) { this->position_sensor_ = s; }
  void set_load_sensor(sensor::Sensor *s) { this->load_sensor_ = s; }
  void set_voltage_sensor(sensor::Sensor *s) { this->voltage_sensor_ = s; }
  void set_temperature_sensor(sensor::Sensor *s) { this->temperature_sensor_ = s; }
  void set_current_sensor(sensor::Sensor *s) { this->current_sensor_ = s; }
  void set_turns_sensor(sensor::Sensor *s) { this->turns_sensor_ = s; }
#endif
#ifdef USE_BINARY_SENSOR
  void set_moving_sensor(binary_sensor::BinarySensor *s) { this->moving_sensor_ = s; }
  void set_online_sensor(binary_sensor::BinarySensor *s) { this->online_sensor_ = s; }
  void set_holding_sensor(binary_sensor::BinarySensor *s) { this->holding_sensor_ = s; }
  void set_overload_sensor(binary_sensor::BinarySensor *s) { this->overload_sensor_ = s; }
#endif
#ifdef USE_TEXT_SENSOR
  void set_result_text_sensor(text_sensor::TextSensor *s) { this->result_text_sensor_ = s; }
  void set_error_text_sensor(text_sensor::TextSensor *s) { this->error_text_sensor_ = s; }
  void set_model_text_sensor(text_sensor::TextSensor *s) { this->model_text_sensor_ = s; }
#endif

  // --- movement ------------------------------------------------------------
  /// Begin a closed-loop move. Non-blocking: poll move_result() or wait for
  /// move_busy() to go false. Supersedes any move already running.
  void start_move(int32_t position, int speed = -1, int acceleration = -1);
  /// Begin driving slowly in `direction` until the servo stalls, then stop.
  void start_seek_stall(int direction, int load_threshold, int32_t max_steps, int speed = -1);
  /// Stop where we are, keeping torque on so the position is still held.
  void abort_move();

  bool move_busy() const { return this->move_result_ == MoveResult::MOVING; }
  MoveResult move_result() const { return this->move_result_; }
  bool last_move_ok() const { return this->move_result_ == MoveResult::ARRIVED; }
  int32_t goal() const { return this->goal_; }

  /// Fire-and-forget write of the goal position, with no verification. Only
  /// useful for jogging; prefer start_move().
  void move_to(int32_t position, int speed = -1, int acceleration = -1);

  // --- torque and holding --------------------------------------------------
  void set_torque(bool enabled);
  /// Reads register 40 back rather than trusting the last write.
  optional<bool> read_torque_enabled();
  /// True when torque is confirmed on, the servo is within tolerance of its
  /// goal, and it is not reporting an overload.
  bool is_holding() const { return this->holding_; }
  /// Actively re-checks holding over the bus instead of using cached state.
  bool verify_holding();
  /// Last torque state actually read back from register 40, refreshed once per
  /// update interval. Cheap to call; does not touch the bus.
  bool torque_on() const { return this->torque_on_; }

  // --- diagnostics ---------------------------------------------------------
  bool ping();
  optional<uint8_t> read_status();
  uint8_t last_error() const { return this->last_error_; }
  int32_t position() const { return this->position_; }
  int load() const { return this->load_; }
  int velocity() const { return this->velocity_; }
  bool moving() const { return this->moving_; }
  bool online() const { return this->online_; }
  uint16_t model_number() const { return this->model_number_; }
  bool multi_turn() const { return this->multi_turn_; }
  /// Full turns away from the 2048 centre, for display.
  float turns() const;

  // --- configuration (all of this is reachable from Home Assistant) --------
  bool apply_multi_turn(bool enabled);
  bool set_operating_mode(uint8_t mode);
  bool set_torque_limit(uint16_t limit);
  bool set_max_torque(uint16_t limit);
  bool write_position_limits(int32_t min_pos, int32_t max_pos);
  bool set_protection(uint8_t overload_torque_pct, uint16_t protection_time_ms,
                      uint8_t protective_torque_pct);
  bool set_overload_protection_enabled(bool enabled);
  bool set_homing_offset(int offset);
  optional<int> read_homing_offset();
  /// Register 40 <- 128: makes wherever the servo is sitting read as 2048.
  bool recenter_here();
  bool change_servo_id(uint8_t new_id);
  uint8_t servo_id() const { return this->servo_id_; }

  // --- raw register access -------------------------------------------------
  bool write_register_u8(uint8_t reg, uint8_t value);
  bool write_register_u16(uint8_t reg, uint16_t value);
  optional<uint8_t> read_register_u8(uint8_t reg);
  optional<uint16_t> read_register_u16(uint8_t reg);

 protected:
  enum class MoveMode : uint8_t { GOTO, SEEK };

  bool read_block_(uint8_t reg, uint8_t length, uint8_t *out);
  bool send_and_receive_(uint8_t instruction, const uint8_t *params, uint8_t param_count,
                         uint8_t *response, uint8_t response_len);
  void flush_input_();
  bool read_byte_with_timeout_(uint8_t *out);
  /// EEPROM registers are write protected; unlock, write, re-lock.
  bool write_eeprom_u8_(uint8_t reg, uint8_t value);
  bool write_eeprom_u16_(uint8_t reg, uint16_t value);
  /// Skips the write (and the EEPROM wear) when the value already matches.
  bool ensure_eeprom_u8_(uint8_t reg, uint8_t value);
  bool ensure_eeprom_u16_(uint8_t reg, uint16_t value);

  /// Reads position/velocity/load in one transaction. Returns false on a bus
  /// failure, in which case nothing is updated.
  bool refresh_motion_();
  void finish_move_(MoveResult result);
  void publish_result_();
  void publish_holding_(bool holding);
  int32_t clamp_target_(int32_t position) const;

  /// Feetech encodes some values as sign+magnitude rather than two's complement.
  static int decode_sign_magnitude(uint16_t raw, uint8_t sign_bit);
  static uint16_t encode_sign_magnitude(int value, uint8_t sign_bit);

  uint8_t servo_id_{1};
  uint16_t default_speed_{800};
  uint8_t default_acceleration_{30};
  uint32_t timeout_ms_{20};

  bool want_multi_turn_{false};
  bool multi_turn_{false};
  uint16_t tolerance_{25};
  uint16_t jam_load_{850};
  uint32_t move_timeout_ms_{8000};
  /// How often the move engine samples the servo while a move is running.
  uint32_t move_poll_ms_{25};
  /// Load must stay above the jam threshold this long before we call it a jam,
  /// so that a stiff cylinder is not mistaken for a blockage.
  uint32_t jam_confirm_ms_{350};

  int32_t position_{-1};
  int32_t goal_{0};
  int load_{0};
  int velocity_{0};
  float voltage_{0.0f};
  float temperature_{0.0f};
  bool moving_{false};
  bool online_{false};
  uint16_t model_number_{0};
  bool holding_{false};
  bool torque_on_{false};
  uint8_t last_error_{0};
  uint8_t consecutive_failures_{0};

  MoveResult move_result_{MoveResult::IDLE};
  MoveMode move_mode_{MoveMode::GOTO};
  uint32_t move_started_{0};
  uint32_t move_last_poll_{0};
  uint32_t jam_since_{0};
  uint8_t settled_samples_{0};
  int move_speed_{-1};
  // Whether the configured travel range has been confirmed on the servo. Until
  // it has, the servo may still be enforcing limits we did not choose.
  bool range_applied_{false};
  uint32_t range_retry_at_{0};
  int seek_direction_{1};
  int32_t seek_remaining_{0};
  int32_t seek_step_{20};
  // The seek's own stall threshold, kept apart from jam_load_ so running a
  // calibration cannot change how every later move detects a jam.
  uint16_t seek_load_{500};
  int32_t seek_last_position_{0};
  uint32_t seek_progress_at_{0};

#ifdef USE_SENSOR
  sensor::Sensor *position_sensor_{nullptr};
  sensor::Sensor *load_sensor_{nullptr};
  sensor::Sensor *voltage_sensor_{nullptr};
  sensor::Sensor *temperature_sensor_{nullptr};
  sensor::Sensor *current_sensor_{nullptr};
  sensor::Sensor *turns_sensor_{nullptr};
#endif
#ifdef USE_BINARY_SENSOR
  binary_sensor::BinarySensor *moving_sensor_{nullptr};
  binary_sensor::BinarySensor *online_sensor_{nullptr};
  binary_sensor::BinarySensor *holding_sensor_{nullptr};
  binary_sensor::BinarySensor *overload_sensor_{nullptr};
#endif
#ifdef USE_TEXT_SENSOR
  text_sensor::TextSensor *result_text_sensor_{nullptr};
  text_sensor::TextSensor *error_text_sensor_{nullptr};
  text_sensor::TextSensor *model_text_sensor_{nullptr};
#endif
};

}  // namespace feetech_servo
}  // namespace esphome
