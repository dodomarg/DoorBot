#include "feetech_servo.h"
#include "esphome/core/application.h"
#include "esphome/core/log.h"
#include "esphome/core/hal.h"

namespace esphome {
namespace feetech_servo {

static const char *const TAG = "feetech_servo";

const char *move_result_to_string(MoveResult result) {
  switch (result) {
    case MoveResult::IDLE:
      return "idle";
    case MoveResult::MOVING:
      return "moving";
    case MoveResult::ARRIVED:
      return "arrived";
    case MoveResult::JAMMED:
      return "jammed";
    case MoveResult::TIMEOUT:
      return "timeout";
    case MoveResult::OFFLINE:
      return "offline";
    case MoveResult::ABORTED:
      return "aborted";
  }
  return "unknown";
}

const char *model_number_to_string(uint16_t model) {
  switch (model) {
    case STS_MODEL_STS3215:
      // Waveshare sells this as ST3215; Feetech's own part number is STS3215.
      return "ST3215 / STS3215";
    case STS_MODEL_ST3235:
      // Steel-geared sibling of the ST3215. Same control table, same 4096-step
      // encoder, same protocol -- it differs only mechanically.
      return "ST3235";
    case STS_MODEL_STS3250:
      return "STS3250";
    case STS_MODEL_SM8512BL:
      return "SM8512BL";
    case SCS_MODEL_SCS0009:
      return "SCS0009";
    default:
      return "unknown";
  }
}

bool model_is_scs_series(uint16_t model) { return model == SCS_MODEL_SCS0009; }

static std::string describe_error(uint8_t flags) {
  if (flags == 0)
    return "ok";
  std::string out;
  const struct {
    uint8_t bit;
    const char *name;
  } names[] = {
      {STS_ERR_VOLTAGE, "voltage"},   {STS_ERR_SENSOR, "sensor"}, {STS_ERR_TEMPERATURE, "temperature"},
      {STS_ERR_CURRENT, "current"},   {STS_ERR_ANGLE, "angle"},   {STS_ERR_OVERLOAD, "overload"},
  };
  for (auto &entry : names) {
    if (flags & entry.bit) {
      if (!out.empty())
        out += ", ";
      out += entry.name;
    }
  }
  return out;
}

void FeetechServo::setup() {
  this->flush_input_();
  this->online_ = this->ping();
  if (!this->online_) {
    ESP_LOGW(TAG, "No response from servo id %u - check wiring, power and baud rate", this->servo_id_);
  } else {
    ESP_LOGI(TAG, "Servo id %u is responding", this->servo_id_);
    // Read the model number before anything else. Every SMS/STS servo shares
    // one control table, so this does not change how the servo is driven, but
    // an SCS-series part would need protocol 1 and a 1024-step encoder -- it
    // would answer a ping and then report positions that are quietly wrong.
    auto model = this->read_register_u16(STS_REG_MODEL_NUMBER);
    if (model.has_value()) {
      this->model_number_ = *model;
      if (model_is_scs_series(this->model_number_)) {
        ESP_LOGE(TAG,
                 "Servo reports model %u (%s), which is an SCS-series part. This component speaks the "
                 "SMS/STS protocol; positions and limits will be wrong. Refusing to configure it.",
                 this->model_number_, model_number_to_string(this->model_number_));
        this->online_ = false;
        this->mark_failed();
      } else {
        ESP_LOGI(TAG, "Servo model number %u (%s)", this->model_number_,
                 model_number_to_string(this->model_number_));
      }
    } else {
      ESP_LOGW(TAG, "Could not read the servo's model number");
    }
  }

  if (this->online_) {
    // SAFETY: release before anything else. A servo keeps its torque state
    // across an ESP32 reset, so a crash or a watchdog reboot while energised
    // would otherwise leave the door held shut with no way to turn it by hand.
    // This is the recovery path the release watchdog reboots into.
    if (this->release_torque_now_()) {
      ESP_LOGI(TAG, "Servo released at boot");
    } else {
      ESP_LOGE(TAG, "Could not release the servo at boot - it may be holding the lock");
    }
    // Bring the servo's own travel limits in line with the configuration. Both
    // helpers read before writing, so a servo that is already set up costs
    // nothing and takes no EEPROM wear.
    if (!this->apply_multi_turn(this->want_multi_turn_))
      ESP_LOGW(TAG, "Could not apply the %s travel range",
               this->want_multi_turn_ ? "multi-turn" : "single-turn");
    this->refresh_motion_();
    this->goal_ = this->position_ >= 0 ? this->position_ : 0;
  }
#ifdef USE_BINARY_SENSOR
  if (this->online_sensor_ != nullptr)
    this->online_sensor_->publish_state(this->online_);
#endif
#ifdef USE_TEXT_SENSOR
  if (this->model_text_sensor_ != nullptr) {
    if (this->model_number_ == 0) {
      this->model_text_sensor_->publish_state("offline");
    } else {
      char buf[40];
      snprintf(buf, sizeof(buf), "%s (%u)", model_number_to_string(this->model_number_), this->model_number_);
      this->model_text_sensor_->publish_state(buf);
    }
  }
#endif
  this->publish_result_();
}

void FeetechServo::dump_config() {
  ESP_LOGCONFIG(TAG, "Feetech SMS/STS bus servo:");
  ESP_LOGCONFIG(TAG, "  Servo ID: %u", this->servo_id_);
  ESP_LOGCONFIG(TAG, "  Model number: %u (%s)", this->model_number_, model_number_to_string(this->model_number_));
  ESP_LOGCONFIG(TAG, "  Default speed: %u steps/s", this->default_speed_);
  ESP_LOGCONFIG(TAG, "  Default acceleration: %u", this->default_acceleration_);
  ESP_LOGCONFIG(TAG, "  Travel: %s", this->multi_turn_ ? "multi-turn (+/- 7 turns)" : "single turn");
  ESP_LOGCONFIG(TAG, "  Arrival tolerance: %u steps", this->tolerance_);
  ESP_LOGCONFIG(TAG, "  Jam load: %u (0.1%%)", this->jam_load_);
  ESP_LOGCONFIG(TAG, "  Move timeout: %u ms", this->move_timeout_ms_);
  ESP_LOGCONFIG(TAG, "  Online: %s", YESNO(this->online_));
  this->check_uart_settings(1000000);
}

// -------------------------------------------------------------------- frames
void FeetechServo::flush_input_() {
  uint8_t dummy;
  while (this->available() > 0) {
    this->read_byte(&dummy);
  }
}

bool FeetechServo::read_byte_with_timeout_(uint8_t *out) {
  const uint32_t start = millis();
  while (millis() - start < this->timeout_ms_) {
    if (this->available() > 0)
      return this->read_byte(out);
    yield();
  }
  return false;
}

bool FeetechServo::send_and_receive_(uint8_t instruction, const uint8_t *params, uint8_t param_count,
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
  // A well-formed but *wrong* reply is the dangerous case: a stale write-ACK
  // arriving late carries no payload, and accepting it here would be read as
  // "position 0, no load, 0 V" - which the move engine would then try to
  // correct by driving the lock a very long way.
  if (response != nullptr && payload_len != response_len) {
    ESP_LOGW(TAG, "Servo %u replied with %u bytes, expected %u", this->servo_id_, payload_len, response_len);
    return false;
  }

  // Every reply carries the servo's current error flags, so each transaction
  // doubles as a health check without costing an extra read.
  if (error != this->last_error_) {
    if (error != 0)
      ESP_LOGW(TAG, "Servo %u reports: %s", this->servo_id_, describe_error(error).c_str());
    this->last_error_ = error;
#ifdef USE_TEXT_SENSOR
    if (this->error_text_sensor_ != nullptr)
      this->error_text_sensor_->publish_state(describe_error(error));
#endif
#ifdef USE_BINARY_SENSOR
    if (this->overload_sensor_ != nullptr)
      this->overload_sensor_->publish_state((error & STS_ERR_OVERLOAD) != 0);
#endif
  }
  return true;
}

// ------------------------------------------------------------------ registers
bool FeetechServo::write_register_u8(uint8_t reg, uint8_t value) {
  const uint8_t params[2] = {reg, value};
  // The servo acknowledges writes with a status frame, so this confirms the
  // write landed rather than assuming it did.
  return this->send_and_receive_(STS_INST_WRITE, params, 2, nullptr, 0);
}

bool FeetechServo::write_register_u16(uint8_t reg, uint16_t value) {
  // Little-endian: low byte first.
  const uint8_t params[3] = {reg, static_cast<uint8_t>(value & 0xFF), static_cast<uint8_t>(value >> 8)};
  return this->send_and_receive_(STS_INST_WRITE, params, 3, nullptr, 0);
}

bool FeetechServo::read_block_(uint8_t reg, uint8_t length, uint8_t *out) {
  const uint8_t params[2] = {reg, length};
  return this->send_and_receive_(STS_INST_READ, params, 2, out, length);
}

optional<uint8_t> FeetechServo::read_register_u8(uint8_t reg) {
  uint8_t buffer = 0;
  if (!this->read_block_(reg, 1, &buffer))
    return {};
  return buffer;
}

optional<uint16_t> FeetechServo::read_register_u16(uint8_t reg) {
  uint8_t buffer[2] = {0, 0};
  if (!this->read_block_(reg, 2, buffer))
    return {};
  return static_cast<uint16_t>(buffer[0] | (buffer[1] << 8));
}

bool FeetechServo::write_eeprom_u8_(uint8_t reg, uint8_t value) {
  // Lock register: 0 unlocks EEPROM for writing, 1 locks it again.
  this->write_register_u8(STS_REG_LOCK, 0);
  delay(5);
  const bool ok = this->write_register_u8(reg, value);
  delay(5);
  this->write_register_u8(STS_REG_LOCK, 1);
  return ok;
}

bool FeetechServo::write_eeprom_u16_(uint8_t reg, uint16_t value) {
  this->write_register_u8(STS_REG_LOCK, 0);
  delay(5);
  const bool ok = this->write_register_u16(reg, value);
  delay(5);
  this->write_register_u8(STS_REG_LOCK, 1);
  return ok;
}

bool FeetechServo::ensure_eeprom_u8_(uint8_t reg, uint8_t value) {
  const auto current = this->read_register_u8(reg);
  if (current.has_value() && *current == value)
    return true;
  ESP_LOGI(TAG, "EEPROM reg %u: %d -> %u", reg, current.has_value() ? *current : -1, value);
  return this->write_eeprom_u8_(reg, value);
}

bool FeetechServo::ensure_eeprom_u16_(uint8_t reg, uint16_t value) {
  const auto current = this->read_register_u16(reg);
  if (current.has_value() && *current == value)
    return true;
  ESP_LOGI(TAG, "EEPROM reg %u: %d -> %u", reg, current.has_value() ? *current : -1, value);
  return this->write_eeprom_u16_(reg, value);
}

// ----------------------------------------------------------------- encoding
int FeetechServo::decode_sign_magnitude(uint16_t raw, uint8_t sign_bit) {
  const uint16_t magnitude = raw & ((1u << sign_bit) - 1u);
  const bool negative = (raw >> sign_bit) & 1u;
  return negative ? -static_cast<int>(magnitude) : static_cast<int>(magnitude);
}

uint16_t FeetechServo::encode_sign_magnitude(int value, uint8_t sign_bit) {
  const uint16_t magnitude = static_cast<uint16_t>(value < 0 ? -value : value) & ((1u << sign_bit) - 1u);
  return value < 0 ? (magnitude | (1u << sign_bit)) : magnitude;
}

// ------------------------------------------------------------------ control
bool FeetechServo::ping() { return this->send_and_receive_(STS_INST_PING, nullptr, 0, nullptr, 1); }

optional<uint8_t> FeetechServo::read_status() { return this->read_register_u8(STS_REG_STATUS); }

void FeetechServo::set_torque(bool enabled) {
  if (enabled)
    this->torque_since_ = millis();
  if (this->write_register_u8(STS_REG_TORQUE_ENABLE, enabled ? STS_TORQUE_ON : STS_TORQUE_OFF)) {
    ESP_LOGD(TAG, "Torque %s", enabled ? "enabled" : "released");
  } else {
    ESP_LOGW(TAG, "Torque %s was not acknowledged", enabled ? "enable" : "release");
  }
  this->torque_on_ = enabled;
  if (!enabled)
    this->publish_holding_(false);
}

optional<bool> FeetechServo::read_torque_enabled() {
  const auto raw = this->read_register_u8(STS_REG_TORQUE_ENABLE);
  if (!raw.has_value())
    return {};
  return *raw != STS_TORQUE_OFF;
}

int32_t FeetechServo::clamp_target_(int32_t position) const {
  if (this->multi_turn_) {
    if (position < STS_MULTITURN_MIN)
      return STS_MULTITURN_MIN;
    if (position > STS_MULTITURN_MAX)
      return STS_MULTITURN_MAX;
    return position;
  }
  if (position < 0)
    return 0;
  if (position > STS_RESOLUTION - 1)
    return STS_RESOLUTION - 1;
  return position;
}

void FeetechServo::move_to(int32_t position, int speed, int acceleration) {
  position = this->clamp_target_(position);

  const uint8_t accel = acceleration < 0 ? this->default_acceleration_ : static_cast<uint8_t>(acceleration);
  uint16_t velocity = speed < 0 ? this->default_speed_ : static_cast<uint16_t>(speed);
  if (velocity > STS_MAX_SPEED)
    velocity = STS_MAX_SPEED;

  this->goal_ = position;
  // Restart the energised clock: this is the moment the servo starts resisting
  // a hand turn, and the safety watchdog measures from here.
  this->torque_since_ = millis();
  this->write_register_u8(STS_REG_TORQUE_ENABLE, STS_TORQUE_ON);
  this->write_register_u8(STS_REG_ACCELERATION, accel);
  this->write_register_u16(STS_REG_GOAL_VELOCITY, encode_sign_magnitude(velocity, 15));
  // Goal position is sign+magnitude with bit 15 as the sign, which matters as
  // soon as multi-turn puts negative targets in range.
  this->write_register_u16(STS_REG_GOAL_POSITION, encode_sign_magnitude(position, 15));
  ESP_LOGD(TAG, "Goal %d (speed %u, accel %u)", (int) position, velocity, accel);
}

void FeetechServo::start_move(int32_t position, int speed, int acceleration) {
  const int32_t clamped = this->clamp_target_(position);
  if (clamped != position) {
    // Silently truncating a goal is how a lock ends up reporting a successful
    // turn it never made - say so, loudly.
    ESP_LOGW(TAG, "Target %d is outside the %s travel range, clamped to %d", (int) position,
             this->multi_turn_ ? "multi-turn" : "single-turn", (int) clamped);
  }
  position = clamped;
  this->move_to(position, speed, acceleration);

  this->move_mode_ = MoveMode::GOTO;
  this->move_speed_ = speed;
  this->move_started_ = millis();
  this->move_last_poll_ = this->move_started_;
  this->jam_since_ = 0;
  this->settled_samples_ = 0;
  this->move_result_ = MoveResult::MOVING;
  this->publish_result_();
  ESP_LOGD(TAG, "Move to %d started", (int) position);
}

void FeetechServo::start_seek_stall(int direction, int load_threshold, int32_t max_steps, int speed) {
  if (this->position_ < 0 && !this->refresh_motion_()) {
    this->finish_move_(MoveResult::OFFLINE);
    return;
  }
  this->seek_direction_ = direction >= 0 ? 1 : -1;
  // The goal has to creep forward at the speed the servo was actually told to
  // move at. Walking it faster makes the goal run away from the servo, and the
  // lag that produces looks exactly like hitting an end stop.
  int32_t per_poll = (static_cast<int32_t>(speed) * static_cast<int32_t>(this->move_poll_ms_)) / 1000;
  if (per_poll < 1)
    per_poll = 1;
  if (per_poll > 40)
    per_poll = 40;
  this->seek_step_ = per_poll * this->seek_direction_;
  this->seek_remaining_ = max_steps;
  // Kept separate from jam_load_ so a gentle calibration threshold does not
  // silently become the jam threshold for every later move.
  this->seek_load_ = load_threshold > 0 ? static_cast<uint16_t>(load_threshold) : this->jam_load_;
  this->seek_last_position_ = this->position_;
  this->seek_progress_at_ = millis();
  this->move_mode_ = MoveMode::SEEK;
  this->move_speed_ = speed;
  this->goal_ = this->position_;
  this->move_started_ = millis();
  this->move_last_poll_ = this->move_started_;
  this->jam_since_ = 0;
  this->settled_samples_ = 0;
  this->move_result_ = MoveResult::MOVING;
  this->publish_result_();
  ESP_LOGI(TAG, "Seeking end stop %s from %d", this->seek_direction_ > 0 ? "upwards" : "downwards",
           (int) this->position_);
}

void FeetechServo::abort_move() {
  if (this->move_result_ != MoveResult::MOVING)
    return;
  // SAFETY: do NOT command a hold at the current position first. That re-arms
  // torque and would clamp the lock exactly where the abort was meant to free
  // it. Cutting torque is what stops the servo; finish_move_() does that.
  this->finish_move_(MoveResult::ABORTED);
}

void FeetechServo::finish_move_(MoveResult result) {
  this->move_result_ = result;
  this->publish_result_();
  if (result == MoveResult::ARRIVED) {
    ESP_LOGI(TAG, "Move finished at %d (goal %d, load %d)", (int) this->position_, (int) this->goal_,
             this->load_);
  } else {
    ESP_LOGW(TAG, "Move ended as %s at %d (goal %d, load %d)", move_result_to_string(result),
             (int) this->position_, (int) this->goal_, this->load_);
  }
  // SAFETY: the move is over, so the servo must stop resisting the thumbturn.
  // This is the normal release path; enforce_safe_state_() is the backstop for
  // when it fails.
  if (!this->release_torque_now_())
    ESP_LOGE(TAG, "Could not release torque after the move - the watchdog will retry");
  this->verify_holding();
}

bool FeetechServo::release_torque_now_() {
  this->write_register_u8(STS_REG_TORQUE_ENABLE, STS_TORQUE_OFF);
  // Trust nothing: read it back. A write that was never acknowledged would
  // otherwise leave the lock energised while we report it as released.
  const auto now_off = this->read_register_u8(STS_REG_TORQUE_ENABLE);
  if (!now_off.has_value())
    return false;
  this->torque_on_ = (*now_off != STS_TORQUE_OFF);
  if (this->torque_on_)
    return false;
  this->publish_holding_(false);
  return true;
}

void FeetechServo::enforce_safe_state_() {
  const uint32_t now = millis();

  if (this->move_result_ == MoveResult::MOVING) {
    // A move that overruns its energised budget is aborted rather than allowed
    // to keep pushing. finish_move_() releases on the way out.
    if (now - this->torque_since_ > this->max_energised_ms_) {
      ESP_LOGE(TAG, "Move exceeded the %u ms energised limit - aborting and releasing",
               (unsigned) this->max_energised_ms_);
      this->finish_move_(MoveResult::TIMEOUT);
    }
    return;
  }

  if (!this->torque_on_) {
    this->release_failures_ = 0;
    this->status_clear_error();
    return;
  }

  // Torque is on with no move running. Give a just-finished move a moment for
  // its own release to land, then take over.
  if (now - this->torque_since_ < this->release_grace_ms_)
    return;
  if (now - this->release_retry_at_ < 250)
    return;
  this->release_retry_at_ = now;

  if (this->release_torque_now_()) {
    ESP_LOGW(TAG, "Watchdog released a servo that was left energised at rest");
    this->release_failures_ = 0;
    this->status_clear_error();
    return;
  }

  this->release_failures_++;
  ESP_LOGE(TAG, "Watchdog could not release the servo (attempt %u of %u)",
           (unsigned) this->release_failures_, (unsigned) MAX_RELEASE_FAILURES);
  if (this->release_failures_ < MAX_RELEASE_FAILURES)
    return;

  // Escalation, but only where escalating can actually help.
  if (!this->online_) {
    // The bus is dead. A reboot cannot issue a release it cannot transmit, and
    // rebooting on a 1-second cadence would cost us logs, OTA and the HA
    // connection -- everything needed to diagnose this. Stay up and shout.
    this->status_set_error(LOG_STR("Servo may be left energised and the bus is unreachable"));
    ESP_LOGE(TAG,
             "SERVO UNREACHABLE while possibly energised. Firmware cannot release it; "
             "cut servo power to restore manual operation.");
    this->release_failures_ = 0;  // keep retrying and re-reporting, do not reboot
    return;
  }

  // The servo answers but will not drop torque. A reboot re-runs setup(), which
  // forces torque off before anything else, and costs a few seconds of
  // availability to recover manual operation of the door.
  ESP_LOGE(TAG, "Servo refuses to release torque - restarting to force it off");
  delay(50);  // let the log line reach the wire before we go
  App.safe_reboot();
}

void FeetechServo::publish_result_() {
#ifdef USE_TEXT_SENSOR
  if (this->result_text_sensor_ != nullptr)
    this->result_text_sensor_->publish_state(move_result_to_string(this->move_result_));
#endif
}

void FeetechServo::publish_holding_(bool holding) {
  if (holding == this->holding_)
    return;
  this->holding_ = holding;
#ifdef USE_BINARY_SENSOR
  if (this->holding_sensor_ != nullptr)
    this->holding_sensor_->publish_state(holding);
#endif
}

bool FeetechServo::verify_holding() {
  const auto torque = this->read_torque_enabled();
  if (!torque.has_value()) {
    this->publish_holding_(false);
    return false;
  }
  this->torque_on_ = *torque;
  const bool on_target =
      this->position_ >= 0 && abs(this->position_ - this->goal_) <= static_cast<int32_t>(this->tolerance_);
  const bool overloaded = (this->last_error_ & (STS_ERR_OVERLOAD | STS_ERR_CURRENT)) != 0;
  const bool holding = *torque && on_target && !overloaded;
  this->publish_holding_(holding);
  return holding;
}

float FeetechServo::turns() const {
  if (this->position_ < 0)
    return 0.0f;
  return this->position_ / static_cast<float>(STS_RESOLUTION);
}

// ------------------------------------------------------------ configuration
bool FeetechServo::apply_multi_turn(bool enabled) {
  // Official ST3215 memory table, register 0x21: "When performing multi-turn
  // absolute position control, [min and max angle] are set to 0." With both
  // limits at zero the goal register accepts -30719..30719 instead of 0..4095.
  const uint16_t want_max = enabled ? 0 : STS_RESOLUTION - 1;
  this->ensure_eeprom_u8_(STS_REG_OPERATING_MODE, STS_MODE_POSITION);
  this->ensure_eeprom_u16_(STS_REG_MIN_POSITION_LIMIT, 0);
  this->ensure_eeprom_u16_(STS_REG_MAX_POSITION_LIMIT, want_max);

  // Read the limits back rather than trusting the write. An EEPROM write only
  // lands if the preceding unlock landed too, and believing a write that did
  // not take would let us send goals the servo silently clamps - which looks
  // like a lock that reports success while barely moving.
  const auto min_now = this->read_register_u16(STS_REG_MIN_POSITION_LIMIT);
  const auto max_now = this->read_register_u16(STS_REG_MAX_POSITION_LIMIT);
  if (!min_now.has_value() || !max_now.has_value() || *min_now != 0 || *max_now != want_max) {
    ESP_LOGW(TAG, "Travel range did not stick (min %d, max %d, wanted 0/%u)",
             min_now.has_value() ? *min_now : -1, max_now.has_value() ? *max_now : -1, want_max);
    return false;
  }
  this->multi_turn_ = enabled;
  this->range_applied_ = true;
  ESP_LOGI(TAG, "Travel range: %s", enabled ? "multi-turn" : "single turn");
  return true;
}

bool FeetechServo::set_operating_mode(uint8_t mode) {
  if (mode > STS_MODE_STEP)
    return false;
  return this->ensure_eeprom_u8_(STS_REG_OPERATING_MODE, mode);
}

bool FeetechServo::set_torque_limit(uint16_t limit) {
  if (limit > STS_MAX_TORQUE)
    limit = STS_MAX_TORQUE;
  return this->write_register_u16(STS_REG_TORQUE_LIMIT, limit);
}

bool FeetechServo::set_max_torque(uint16_t limit) {
  if (limit > STS_MAX_TORQUE)
    limit = STS_MAX_TORQUE;
  return this->ensure_eeprom_u16_(STS_REG_MAX_TORQUE_LIMIT, limit);
}

bool FeetechServo::write_position_limits(int32_t min_pos, int32_t max_pos) {
  const bool ok_min = this->ensure_eeprom_u16_(STS_REG_MIN_POSITION_LIMIT, encode_sign_magnitude(min_pos, 15));
  const bool ok_max = this->ensure_eeprom_u16_(STS_REG_MAX_POSITION_LIMIT, encode_sign_magnitude(max_pos, 15));
  this->multi_turn_ = (min_pos == 0 && max_pos == 0);
  return ok_min && ok_max;
}

bool FeetechServo::set_protection(uint8_t overload_torque_pct, uint16_t protection_time_ms,
                             uint8_t protective_torque_pct) {
  if (overload_torque_pct > 100)
    overload_torque_pct = 100;
  if (protective_torque_pct > 100)
    protective_torque_pct = 100;
  // Register 35 counts in 10 ms units and saturates at 254 (2.54 s).
  uint16_t ticks = protection_time_ms / 10;
  if (ticks > 254)
    ticks = 254;
  const bool a = this->ensure_eeprom_u8_(STS_REG_OVERLOAD_TORQUE, overload_torque_pct);
  const bool b = this->ensure_eeprom_u8_(STS_REG_PROTECTION_TIME, static_cast<uint8_t>(ticks));
  const bool c = this->ensure_eeprom_u8_(STS_REG_PROTECTIVE_TORQUE, protective_torque_pct);
  return a && b && c;
}

bool FeetechServo::set_overload_protection_enabled(bool enabled) {
  const auto current = this->read_register_u8(STS_REG_UNLOADING_CONDITION);
  if (!current.has_value())
    return false;
  const uint8_t updated =
      enabled ? (*current | STS_ERR_OVERLOAD) : static_cast<uint8_t>(*current & ~STS_ERR_OVERLOAD);
  if (updated == *current)
    return true;
  if (!enabled)
    ESP_LOGW(TAG, "Overload unloading disabled - the servo will no longer protect itself from stalling");
  return this->write_eeprom_u8_(STS_REG_UNLOADING_CONDITION, updated);
}

bool FeetechServo::set_homing_offset(int offset) {
  if (offset > 2047)
    offset = 2047;
  if (offset < -2047)
    offset = -2047;
  // Present_Position = Actual_Position - Homing_Offset. Sign is bit 11.
  return this->ensure_eeprom_u16_(STS_REG_HOMING_OFFSET, encode_sign_magnitude(offset, 11));
}

optional<int> FeetechServo::read_homing_offset() {
  const auto raw = this->read_register_u16(STS_REG_HOMING_OFFSET);
  if (!raw.has_value())
    return {};
  return decode_sign_magnitude(*raw, 11);
}

bool FeetechServo::recenter_here() {
  // Register 40 accepts 128 as "correct the current position to 2048".
  if (!this->write_register_u8(STS_REG_TORQUE_ENABLE, STS_TORQUE_RECENTER))
    return false;
  delay(20);
  this->refresh_motion_();
  this->goal_ = this->position_;
  ESP_LOGI(TAG, "Re-centred: this position now reads %d", (int) this->position_);
  return true;
}

bool FeetechServo::change_servo_id(uint8_t new_id) {
  if (new_id > 253)
    return false;
  this->write_register_u8(STS_REG_LOCK, 0);
  delay(5);
  const bool ok = this->write_register_u8(STS_REG_ID, new_id);
  delay(5);
  if (ok) {
    // The servo answers on the new ID from here on, so switch before re-locking.
    this->servo_id_ = new_id;
    ESP_LOGI(TAG, "Servo ID changed to %u", new_id);
  }
  this->write_register_u8(STS_REG_LOCK, 1);
  return ok;
}

// ------------------------------------------------------------- move engine
bool FeetechServo::refresh_motion_() {
  // Present_Position(56,2) Present_Velocity(58,2) Present_Load(60,2)
  // Present_Voltage(62,1) Present_Temperature(63,1) -> one 8 byte read.
  uint8_t buffer[8] = {0};
  if (!this->read_block_(STS_REG_PRESENT_POSITION, 8, buffer)) {
    if (this->consecutive_failures_ < 250)
      this->consecutive_failures_++;
    if (this->consecutive_failures_ >= 3 && this->online_) {
      this->online_ = false;
      ESP_LOGW(TAG, "Lost contact with servo %u", this->servo_id_);
#ifdef USE_BINARY_SENSOR
      if (this->online_sensor_ != nullptr)
        this->online_sensor_->publish_state(false);
#endif
      this->publish_holding_(false);
    }
    return false;
  }

  this->consecutive_failures_ = 0;
  if (!this->online_) {
    this->online_ = true;
#ifdef USE_BINARY_SENSOR
    if (this->online_sensor_ != nullptr)
      this->online_sensor_->publish_state(true);
#endif
  }
  // A servo whose power came up after the ESP32 missed setup() entirely, so it
  // would still be limited to one revolution while we happily send multi-turn
  // goals. Apply the range the first time we can actually talk to it, but no
  // more than once every few seconds so a persistent failure cannot flood the
  // bus in the middle of a move.
  const uint32_t now_ms = millis();
  if (!this->range_applied_ && now_ms - this->range_retry_at_ >= 5000) {
    this->range_retry_at_ = now_ms;
    this->apply_multi_turn(this->want_multi_turn_);
  }

  // Position is sign+magnitude with bit 15 as the sign. In single-turn mode the
  // value is always positive, so this decode is correct either way.
  const int32_t raw_position =
      decode_sign_magnitude(static_cast<uint16_t>(buffer[0] | (buffer[1] << 8)), 15);
  this->position_ = this->unwrap_position_(raw_position);
  this->velocity_ = decode_sign_magnitude(static_cast<uint16_t>(buffer[2] | (buffer[3] << 8)), 15);
  this->load_ = decode_sign_magnitude(static_cast<uint16_t>(buffer[4] | (buffer[5] << 8)), 10);
  this->voltage_ = buffer[6] / 10.0f;
  this->temperature_ = buffer[7];
  return true;
}

int32_t FeetechServo::unwrap_position_(int32_t raw) {
  if (!this->multi_turn_) {
    this->unwrap_primed_ = false;
    this->turn_offset_ = 0;
    return raw;
  }
  // Some firmware revisions do report an accumulated multi-turn position. If
  // the reading is already outside one revolution it needs no help from us, and
  // unwrapping it as well would double-count every crossing.
  if (raw < 0 || raw > STS_RESOLUTION - 1) {
    this->unwrap_primed_ = false;
    this->turn_offset_ = 0;
    return raw;
  }
  if (!this->unwrap_primed_) {
    this->raw_position_last_ = raw;
    this->unwrap_primed_ = true;
  }
  // A jump of more than half a revolution between two samples 25 ms apart is
  // not real motion -- the servo's counter rolled over.
  const int32_t delta = raw - this->raw_position_last_;
  if (delta > STS_RESOLUTION / 2) {
    this->turn_offset_ -= STS_RESOLUTION;
  } else if (delta < -(STS_RESOLUTION / 2)) {
    this->turn_offset_ += STS_RESOLUTION;
  }
  this->raw_position_last_ = raw;
  return raw + this->turn_offset_;
}

void FeetechServo::loop() {
  this->enforce_safe_state_();

  if (this->move_result_ != MoveResult::MOVING)
    return;

  const uint32_t now = millis();
  if (now - this->move_last_poll_ < this->move_poll_ms_)
    return;
  this->move_last_poll_ = now;

  if (!this->refresh_motion_()) {
    if (this->consecutive_failures_ >= 3)
      this->finish_move_(MoveResult::OFFLINE);
    return;
  }

  const int32_t error = this->goal_ - this->position_;
  const int32_t distance = error < 0 ? -error : error;
  const int abs_load = this->load_ < 0 ? -this->load_ : this->load_;
  const int abs_velocity = this->velocity_ < 0 ? -this->velocity_ : this->velocity_;

  if (this->move_mode_ == MoveMode::SEEK) {
    // Walk the goal forwards in small increments until the servo pushes back.
    if (abs_load >= static_cast<int>(this->seek_load_)) {
      ESP_LOGI(TAG, "End stop found at %d (load %d)", (int) this->position_, abs_load);
      this->move_to(this->position_, this->move_speed_);
      this->finish_move_(MoveResult::ARRIVED);
      return;
    }

    // Load thresholds are hard to get right across different mechanisms, so
    // back them up with the thing that is unambiguous: the servo was told to
    // move, is being fed a goal it has not reached, and is not moving.
    const int32_t travelled = this->position_ - this->seek_last_position_;
    if ((travelled < 0 ? -travelled : travelled) >= 3) {
      this->seek_last_position_ = this->position_;
      this->seek_progress_at_ = now;
    } else if (distance > static_cast<int32_t>(this->tolerance_) &&
               now - this->seek_progress_at_ >= STS_SEEK_STALL_MS) {
      ESP_LOGI(TAG, "Servo stopped moving at %d (goal %d) - treating as an end stop", (int) this->position_,
               (int) this->goal_);
      this->move_to(this->position_, this->move_speed_);
      this->finish_move_(MoveResult::ARRIVED);
      return;
    }

    if (this->seek_remaining_ <= 0 || now - this->move_started_ > this->move_timeout_ms_) {
      this->move_to(this->position_, this->move_speed_);
      this->finish_move_(MoveResult::TIMEOUT);
      return;
    }
    // Only advance the goal while the servo is keeping up, so it can never run
    // away and manufacture a lag that looks like a stall.
    const int32_t step = this->seek_step_ < 0 ? -this->seek_step_ : this->seek_step_;
    if (distance <= step * 4) {
      const int32_t next = this->clamp_target_(this->goal_ + this->seek_step_);
      if (next == this->goal_) {
        // Ran into the configured travel limit without stalling.
        this->move_to(this->position_, this->move_speed_);
        this->finish_move_(MoveResult::TIMEOUT);
        return;
      }
      this->seek_remaining_ -= step;
      this->move_to(next, this->move_speed_);
    }
  } else {
    if (distance <= static_cast<int32_t>(this->tolerance_)) {
      // Require two consecutive in-tolerance samples so we do not call the move
      // done while the servo is still coasting through the target.
      if (++this->settled_samples_ >= 2) {
        this->finish_move_(MoveResult::ARRIVED);
        return;
      }
    } else {
      this->settled_samples_ = 0;
    }

    // A jam is high load with no progress. Holding hard *on* target is not a
    // jam, which is why the arrival test above runs first.
    if (abs_load >= static_cast<int>(this->jam_load_) && abs_velocity <= 5) {
      if (this->jam_since_ == 0) {
        this->jam_since_ = now;
      } else if (now - this->jam_since_ >= this->jam_confirm_ms_) {
        this->move_to(this->position_, this->move_speed_);
        this->finish_move_(MoveResult::JAMMED);
        return;
      }
    } else {
      this->jam_since_ = 0;
    }

    if (now - this->move_started_ >= this->move_timeout_ms_) {
      this->move_to(this->position_, this->move_speed_);
      this->finish_move_(MoveResult::TIMEOUT);
      return;
    }
  }

  App.feed_wdt();
}

// ------------------------------------------------------------------- polling
void FeetechServo::update() {
  // The move engine is already sampling at 25 ms; do not compete with it for
  // the bus, just republish what it has gathered.
  if (this->move_result_ != MoveResult::MOVING) {
    if (!this->refresh_motion_())
      return;

    const auto moving_flag = this->read_register_u8(STS_REG_MOVING);
    this->moving_ = moving_flag.has_value() ? (*moving_flag != 0) : (abs(this->velocity_) > 2);
    this->verify_holding();
  } else {
    this->moving_ = true;
  }

#ifdef USE_SENSOR
  if (this->position_sensor_ != nullptr)
    this->position_sensor_->publish_state(this->position_);
  if (this->turns_sensor_ != nullptr)
    this->turns_sensor_->publish_state(this->turns());
  if (this->load_sensor_ != nullptr)
    this->load_sensor_->publish_state(abs(this->load_));
  if (this->voltage_sensor_ != nullptr)
    this->voltage_sensor_->publish_state(this->voltage_);
  if (this->temperature_sensor_ != nullptr)
    this->temperature_sensor_->publish_state(this->temperature_);
  if (this->current_sensor_ != nullptr && this->move_result_ != MoveResult::MOVING) {
    const auto raw_current = this->read_register_u16(STS_REG_PRESENT_CURRENT);
    if (raw_current.has_value())
      this->current_sensor_->publish_state(*raw_current * 6.5f);  // 6.5 mA per count
  }
#endif
#ifdef USE_BINARY_SENSOR
  if (this->moving_sensor_ != nullptr)
    this->moving_sensor_->publish_state(this->moving_);
#endif
}

}  // namespace feetech_servo
}  // namespace esphome
