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

namespace esphome {
namespace sts3215 {

// Feetech SMS/STS serial bus protocol (protocol "0").
// Frame: 0xFF 0xFF | ID | LEN | INSTRUCTION | PARAMS... | CHECKSUM
//   LEN      = number of params + 2
//   CHECKSUM = ~(ID + LEN + INSTRUCTION + sum(PARAMS)) & 0xFF
// 16-bit values are little-endian (low byte first).
static const uint8_t STS_HEADER = 0xFF;
static const uint8_t STS_INST_PING = 0x01;
static const uint8_t STS_INST_READ = 0x02;
static const uint8_t STS_INST_WRITE = 0x03;

// Control table addresses, cross-checked against the Feetech STS/SMS e-manual
// and huggingface/lerobot's STS_SMS_SERIES_CONTROL_TABLE.
static const uint8_t STS_REG_ID = 5;
static const uint8_t STS_REG_BAUD_RATE = 6;
static const uint8_t STS_REG_MIN_POSITION_LIMIT = 9;
static const uint8_t STS_REG_MAX_POSITION_LIMIT = 11;
static const uint8_t STS_REG_MAX_TORQUE_LIMIT = 16;
static const uint8_t STS_REG_PROTECTION_CURRENT = 28;
static const uint8_t STS_REG_OPERATING_MODE = 33;
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
static const uint8_t STS_REG_MOVING = 66;
static const uint8_t STS_REG_PRESENT_CURRENT = 69;

// 12-bit encoder: 0..4095 covers a full turn.
static const uint16_t STS_RESOLUTION = 4096;

class STS3215 : public PollingComponent, public uart::UARTDevice {
 public:
  void setup() override;
  void update() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::DATA; }

  void set_servo_id(uint8_t id) { this->servo_id_ = id; }
  void set_default_speed(uint16_t speed) { this->default_speed_ = speed; }
  void set_default_acceleration(uint8_t accel) { this->default_acceleration_ = accel; }

#ifdef USE_SENSOR
  void set_position_sensor(sensor::Sensor *s) { this->position_sensor_ = s; }
  void set_load_sensor(sensor::Sensor *s) { this->load_sensor_ = s; }
  void set_voltage_sensor(sensor::Sensor *s) { this->voltage_sensor_ = s; }
  void set_temperature_sensor(sensor::Sensor *s) { this->temperature_sensor_ = s; }
  void set_current_sensor(sensor::Sensor *s) { this->current_sensor_ = s; }
#endif
#ifdef USE_BINARY_SENSOR
  void set_moving_sensor(binary_sensor::BinarySensor *s) { this->moving_sensor_ = s; }
  void set_online_sensor(binary_sensor::BinarySensor *s) { this->online_sensor_ = s; }
#endif

  // --- high level control -------------------------------------------------
  bool ping();
  void set_torque(bool enabled);
  void move_to(int position, int speed = -1, int acceleration = -1);
  /// Drive slowly until the measured load exceeds `load_threshold`, then stop.
  /// Returns the position where it stalled, or -1 if it never stalled.
  int home_to_stall(int direction, int load_threshold, int max_steps, int speed);
  void write_position_limits(uint16_t min_pos, uint16_t max_pos);
  void set_torque_limit(uint16_t limit);
  bool change_servo_id(uint8_t new_id);

  // --- cached state -------------------------------------------------------
  int position() const { return this->position_; }
  int load() const { return this->load_; }
  bool moving() const { return this->moving_; }
  bool online() const { return this->online_; }

  // --- raw register access ------------------------------------------------
  bool write_register_u8(uint8_t reg, uint8_t value);
  bool write_register_u16(uint8_t reg, uint16_t value);
  optional<uint8_t> read_register_u8(uint8_t reg);
  optional<uint16_t> read_register_u16(uint8_t reg);

 protected:
  bool read_block_(uint8_t reg, uint8_t length, uint8_t *out);
  bool send_and_receive_(uint8_t instruction, const uint8_t *params, uint8_t param_count,
                         uint8_t *response, uint8_t response_len);
  void flush_input_();
  bool read_byte_with_timeout_(uint8_t *out);
  /// EEPROM registers are write protected; unlock, write, re-lock.
  bool write_eeprom_u16_(uint8_t reg, uint16_t value);

  /// Feetech encodes some values as sign+magnitude rather than two's complement.
  static int decode_sign_magnitude(uint16_t raw, uint8_t sign_bit);
  static uint16_t encode_sign_magnitude(int value, uint8_t sign_bit);

  uint8_t servo_id_{1};
  uint16_t default_speed_{800};
  uint8_t default_acceleration_{30};
  uint32_t timeout_ms_{20};

  int position_{-1};
  int load_{0};
  int velocity_{0};
  bool moving_{false};
  bool online_{false};
  uint8_t consecutive_failures_{0};

#ifdef USE_SENSOR
  sensor::Sensor *position_sensor_{nullptr};
  sensor::Sensor *load_sensor_{nullptr};
  sensor::Sensor *voltage_sensor_{nullptr};
  sensor::Sensor *temperature_sensor_{nullptr};
  sensor::Sensor *current_sensor_{nullptr};
#endif
#ifdef USE_BINARY_SENSOR
  binary_sensor::BinarySensor *moving_sensor_{nullptr};
  binary_sensor::BinarySensor *online_sensor_{nullptr};
#endif
};

}  // namespace sts3215
}  // namespace esphome
