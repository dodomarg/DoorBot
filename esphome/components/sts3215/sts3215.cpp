#include "sts3215.h"
#include "esphome/core/application.h"
#include "esphome/core/log.h"
#include "esphome/core/hal.h"

namespace esphome {
namespace sts3215 {

static const char *const TAG = "sts3215";

void STS3215::setup() {
  this->flush_input_();
  this->online_ = this->ping();
  if (!this->online_) {
    ESP_LOGW(TAG, "No response from servo id %u - check wiring, power and baud rate", this->servo_id_);
  } else {
    ESP_LOGI(TAG, "Servo id %u is responding", this->servo_id_);
  }
#ifdef USE_BINARY_SENSOR
  if (this->online_sensor_ != nullptr)
    this->online_sensor_->publish_state(this->online_);
#endif
}

void STS3215::dump_config() {
  ESP_LOGCONFIG(TAG, "Feetech STS3215 bus servo:");
  ESP_LOGCONFIG(TAG, "  Servo ID: %u", this->servo_id_);
  ESP_LOGCONFIG(TAG, "  Default speed: %u", this->default_speed_);
  ESP_LOGCONFIG(TAG, "  Default acceleration: %u", this->default_acceleration_);
  ESP_LOGCONFIG(TAG, "  Online: %s", YESNO(this->online_));
  this->check_uart_settings(1000000);
}

// -------------------------------------------------------------------- frames
void STS3215::flush_input_() {
  uint8_t dummy;
  while (this->available() > 0) {
    this->read_byte(&dummy);
  }
}

bool STS3215::read_byte_with_timeout_(uint8_t *out) {
  const uint32_t start = millis();
  while (millis() - start < this->timeout_ms_) {
    if (this->available() > 0)
      return this->read_byte(out);
    yield();
  }
  return false;
}

bool STS3215::send_and_receive_(uint8_t instruction, const uint8_t *params, uint8_t param_count,
                                uint8_t *response, uint8_t response_len) {
  this->flush_input_();

  const uint8_t length = param_count + 2;
  uint8_t checksum = this->servo_id_ + length + instruction;

  uint8_t frame[16];
  uint8_t n = 0;
  frame[n++] = STS_HEADER;
  frame[n++] = STS_HEADER;
  frame[n++] = this->servo_id_;
  frame[n++] = length;
  frame[n++] = instruction;
  for (uint8_t i = 0; i < param_count && n < sizeof(frame) - 1; i++) {
    frame[n++] = params[i];
    checksum += params[i];
  }
  frame[n++] = ~checksum;

  this->write_array(frame, n);
  this->flush();

  // Broadcast writes get no reply.
  if (response == nullptr && response_len == 0)
    return true;

  // Hunt for the 0xFF 0xFF header (the bus can echo our own transmission).
  uint8_t byte = 0;
  uint8_t header_seen = 0;
  const uint32_t start = millis();
  while (millis() - start < this->timeout_ms_) {
    if (!this->read_byte_with_timeout_(&byte))
      return false;
    if (byte == STS_HEADER) {
      header_seen++;
      if (header_seen >= 2)
        break;
    } else {
      header_seen = 0;
    }
  }
  if (header_seen < 2)
    return false;

  uint8_t id = 0, len = 0, error = 0;
  if (!this->read_byte_with_timeout_(&id) || !this->read_byte_with_timeout_(&len))
    return false;
  if (id != this->servo_id_ || len < 2)
    return false;
  if (!this->read_byte_with_timeout_(&error))
    return false;

  const uint8_t payload_len = len - 2;
  uint8_t sum = id + len + error;
  for (uint8_t i = 0; i < payload_len; i++) {
    uint8_t value = 0;
    if (!this->read_byte_with_timeout_(&value))
      return false;
    sum += value;
    if (response != nullptr && i < response_len)
      response[i] = value;
  }

  uint8_t received_checksum = 0;
  if (!this->read_byte_with_timeout_(&received_checksum))
    return false;
  if (received_checksum != static_cast<uint8_t>(~sum)) {
    ESP_LOGW(TAG, "Checksum mismatch from servo %u", this->servo_id_);
    return false;
  }
  if (error != 0) {
    ESP_LOGW(TAG, "Servo %u reported status flags 0x%02X", this->servo_id_, error);
  }
  return true;
}

// ------------------------------------------------------------------ registers
bool STS3215::write_register_u8(uint8_t reg, uint8_t value) {
  const uint8_t params[2] = {reg, value};
  return this->send_and_receive_(STS_INST_WRITE, params, 2, nullptr, 0);
}

bool STS3215::write_register_u16(uint8_t reg, uint16_t value) {
  // Little-endian: low byte first.
  const uint8_t params[3] = {reg, static_cast<uint8_t>(value & 0xFF), static_cast<uint8_t>(value >> 8)};
  return this->send_and_receive_(STS_INST_WRITE, params, 3, nullptr, 0);
}

bool STS3215::read_block_(uint8_t reg, uint8_t length, uint8_t *out) {
  const uint8_t params[2] = {reg, length};
  return this->send_and_receive_(STS_INST_READ, params, 2, out, length);
}

optional<uint8_t> STS3215::read_register_u8(uint8_t reg) {
  uint8_t buffer = 0;
  if (!this->read_block_(reg, 1, &buffer))
    return {};
  return buffer;
}

optional<uint16_t> STS3215::read_register_u16(uint8_t reg) {
  uint8_t buffer[2] = {0, 0};
  if (!this->read_block_(reg, 2, buffer))
    return {};
  return static_cast<uint16_t>(buffer[0] | (buffer[1] << 8));
}

bool STS3215::write_eeprom_u16_(uint8_t reg, uint16_t value) {
  // Lock register: 0 unlocks EEPROM for writing, 1 locks it again.
  this->write_register_u8(STS_REG_LOCK, 0);
  delay(5);
  const bool ok = this->write_register_u16(reg, value);
  delay(5);
  this->write_register_u8(STS_REG_LOCK, 1);
  return ok;
}

// ----------------------------------------------------------------- encoding
int STS3215::decode_sign_magnitude(uint16_t raw, uint8_t sign_bit) {
  const uint16_t magnitude = raw & ((1u << sign_bit) - 1u);
  const bool negative = (raw >> sign_bit) & 1u;
  return negative ? -static_cast<int>(magnitude) : static_cast<int>(magnitude);
}

uint16_t STS3215::encode_sign_magnitude(int value, uint8_t sign_bit) {
  const uint16_t magnitude = static_cast<uint16_t>(value < 0 ? -value : value) & ((1u << sign_bit) - 1u);
  return value < 0 ? (magnitude | (1u << sign_bit)) : magnitude;
}

// ------------------------------------------------------------------ control
bool STS3215::ping() {
  return this->send_and_receive_(STS_INST_PING, nullptr, 0, nullptr, 1);
}

void STS3215::set_torque(bool enabled) {
  this->write_register_u8(STS_REG_TORQUE_ENABLE, enabled ? 1 : 0);
  ESP_LOGD(TAG, "Torque %s", enabled ? "enabled" : "released");
}

void STS3215::move_to(int position, int speed, int acceleration) {
  if (position < 0)
    position = 0;
  if (position > STS_RESOLUTION - 1)
    position = STS_RESOLUTION - 1;

  const uint8_t accel = acceleration < 0 ? this->default_acceleration_ : static_cast<uint8_t>(acceleration);
  const uint16_t velocity = speed < 0 ? this->default_speed_ : static_cast<uint16_t>(speed);

  this->write_register_u8(STS_REG_TORQUE_ENABLE, 1);
  this->write_register_u8(STS_REG_ACCELERATION, accel);
  this->write_register_u16(STS_REG_GOAL_VELOCITY, encode_sign_magnitude(velocity, 15));
  this->write_register_u16(STS_REG_GOAL_POSITION, static_cast<uint16_t>(position));
  ESP_LOGD(TAG, "Moving to %d (speed %u, accel %u)", position, velocity, accel);
}

void STS3215::set_torque_limit(uint16_t limit) {
  if (limit > 1000)
    limit = 1000;
  this->write_register_u16(STS_REG_TORQUE_LIMIT, limit);
}

void STS3215::write_position_limits(uint16_t min_pos, uint16_t max_pos) {
  this->write_eeprom_u16_(STS_REG_MIN_POSITION_LIMIT, min_pos);
  this->write_eeprom_u16_(STS_REG_MAX_POSITION_LIMIT, max_pos);
}

bool STS3215::change_servo_id(uint8_t new_id) {
  this->write_register_u8(STS_REG_LOCK, 0);
  delay(5);
  const bool ok = this->write_register_u8(STS_REG_ID, new_id);
  delay(5);
  if (ok)
    this->servo_id_ = new_id;
  this->write_register_u8(STS_REG_LOCK, 1);
  return ok;
}

int STS3215::home_to_stall(int direction, int load_threshold, int max_steps, int speed) {
  if (this->position_ < 0)
    this->update();
  if (this->position_ < 0)
    return -1;

  const int step = direction >= 0 ? 20 : -20;
  int travelled = 0;
  int target = this->position_;

  while (travelled < max_steps) {
    target += step;
    if (target < 0 || target > STS_RESOLUTION - 1)
      break;
    this->move_to(target, speed);
    delay(60);

    uint8_t buffer[6] = {0};
    if (this->read_block_(STS_REG_PRESENT_POSITION, 6, buffer)) {
      const int measured = buffer[0] | (buffer[1] << 8);
      const int abs_load =
          abs(decode_sign_magnitude(static_cast<uint16_t>(buffer[4] | (buffer[5] << 8)), 10));
      if (abs_load >= load_threshold) {
        ESP_LOGI(TAG, "Stalled at position %d with load %d", measured, abs_load);
        this->move_to(measured, speed);
        return measured;
      }
      // If the servo stopped following us, treat that as a stall too.
      if (abs(measured - target) > 120) {
        ESP_LOGI(TAG, "Servo fell behind at %d (target %d) - treating as end stop", measured, target);
        this->move_to(measured, speed);
        return measured;
      }
    }
    travelled += abs(step);
    App.feed_wdt();
  }

  ESP_LOGW(TAG, "Never stalled within %d steps", max_steps);
  return -1;
}

// ------------------------------------------------------------------- polling
void STS3215::update() {
  // Present_Position(56,2) Present_Velocity(58,2) Present_Load(60,2)
  // Present_Voltage(62,1) Present_Temperature(63,1) -> one 8 byte read.
  uint8_t buffer[8] = {0};
  const bool ok = this->read_block_(STS_REG_PRESENT_POSITION, 8, buffer);

  if (!ok) {
    if (this->consecutive_failures_ < 250)
      this->consecutive_failures_++;
    if (this->consecutive_failures_ >= 3 && this->online_) {
      this->online_ = false;
      ESP_LOGW(TAG, "Lost contact with servo %u", this->servo_id_);
#ifdef USE_BINARY_SENSOR
      if (this->online_sensor_ != nullptr)
        this->online_sensor_->publish_state(false);
#endif
    }
    return;
  }

  this->consecutive_failures_ = 0;
  if (!this->online_) {
    this->online_ = true;
#ifdef USE_BINARY_SENSOR
    if (this->online_sensor_ != nullptr)
      this->online_sensor_->publish_state(true);
#endif
  }

  this->position_ = buffer[0] | (buffer[1] << 8);
  this->velocity_ = decode_sign_magnitude(static_cast<uint16_t>(buffer[2] | (buffer[3] << 8)), 15);
  this->load_ = decode_sign_magnitude(static_cast<uint16_t>(buffer[4] | (buffer[5] << 8)), 10);
  const float voltage = buffer[6] / 10.0f;
  const float temperature = buffer[7];

  const auto moving_flag = this->read_register_u8(STS_REG_MOVING);
  this->moving_ = moving_flag.has_value() ? (*moving_flag != 0) : (abs(this->velocity_) > 2);

#ifdef USE_SENSOR
  if (this->position_sensor_ != nullptr)
    this->position_sensor_->publish_state(this->position_);
  if (this->load_sensor_ != nullptr)
    this->load_sensor_->publish_state(abs(this->load_));
  if (this->voltage_sensor_ != nullptr)
    this->voltage_sensor_->publish_state(voltage);
  if (this->temperature_sensor_ != nullptr)
    this->temperature_sensor_->publish_state(temperature);
  if (this->current_sensor_ != nullptr) {
    const auto raw_current = this->read_register_u16(STS_REG_PRESENT_CURRENT);
    if (raw_current.has_value())
      this->current_sensor_->publish_state(*raw_current * 6.5f);  // ~6.5 mA per count
  }
#endif
#ifdef USE_BINARY_SENSOR
  if (this->moving_sensor_ != nullptr)
    this->moving_sensor_->publish_state(this->moving_);
#endif
}

}  // namespace sts3215
}  // namespace esphome
