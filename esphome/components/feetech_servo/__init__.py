"""ESPHome component for Feetech SMS/STS serial bus servos.

Covers the ST3215 and ST3235, which share one control table, a 4096-step
encoder, protocol 0 and a 1,000,000 baud default. The model number in register
3 is read and reported at startup, and an SCS-series servo -- protocol 1, 1024
steps, big-endian -- is rejected rather than driven with the wrong encoding.

Designed for a Seeed XIAO ESP32S3 plugged into a Seeed "Bus Servo Driver Board
for XIAO" (a.k.a. XIAO Bus Servo Adapter), which handles the half-duplex bus
conversion, so a plain UART with separate tx/rx pins is all that is needed.
"""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import binary_sensor, sensor, text_sensor, uart
from esphome.const import (
    CONF_CURRENT,
    CONF_ID,
    CONF_TEMPERATURE,
    CONF_VOLTAGE,
    DEVICE_CLASS_CONNECTIVITY,
    DEVICE_CLASS_CURRENT,
    DEVICE_CLASS_PROBLEM,
    DEVICE_CLASS_TEMPERATURE,
    DEVICE_CLASS_VOLTAGE,
    ENTITY_CATEGORY_DIAGNOSTIC,
    STATE_CLASS_MEASUREMENT,
    UNIT_CELSIUS,
    UNIT_VOLT,
)

CODEOWNERS = ["@dodomarg"]
DEPENDENCIES = ["uart"]
AUTO_LOAD = ["sensor", "binary_sensor", "text_sensor"]
MULTI_CONF = True

CONF_SERVO_ID = "servo_id"
CONF_DEFAULT_SPEED = "default_speed"
CONF_DEFAULT_ACCELERATION = "default_acceleration"
CONF_POSITION = "position"
CONF_LOAD = "load"
CONF_MOVING = "moving"
CONF_ONLINE = "online"
CONF_HOLDING = "holding"
CONF_OVERLOAD = "overload"
CONF_TURNS = "turns"
CONF_MOVE_RESULT = "move_result"
CONF_ERROR = "error"
CONF_MODEL = "model"
CONF_MULTI_TURN = "multi_turn"
CONF_TOLERANCE = "tolerance"
CONF_JAM_LOAD = "jam_load"
CONF_MOVE_TIMEOUT = "move_timeout"

UNIT_MILLIAMP = "mA"
UNIT_STEPS = "steps"
UNIT_TURNS = "turns"

feetech_ns = cg.esphome_ns.namespace("feetech_servo")
FeetechServo = feetech_ns.class_("FeetechServo", cg.PollingComponent, uart.UARTDevice)

CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(FeetechServo),
            cv.Optional(CONF_SERVO_ID, default=1): cv.int_range(min=0, max=253),
            cv.Optional(CONF_DEFAULT_SPEED, default=800): cv.int_range(min=0, max=4000),
            cv.Optional(CONF_DEFAULT_ACCELERATION, default=30): cv.int_range(min=0, max=255),
            # Multi-turn absolute mode. The servo keeps reporting absolute
            # position across revolutions but forgets the revolution count on
            # power loss, which is fine for a lock that re-homes on demand.
            cv.Optional(CONF_MULTI_TURN, default=False): cv.boolean,
            cv.Optional(CONF_TOLERANCE, default=25): cv.int_range(min=1, max=2048),
            cv.Optional(CONF_JAM_LOAD, default=850): cv.int_range(min=0, max=1000),
            cv.Optional(
                CONF_MOVE_TIMEOUT, default="8s"
            ): cv.positive_time_period_milliseconds,
            cv.Optional(CONF_POSITION): sensor.sensor_schema(
                unit_of_measurement=UNIT_STEPS,
                accuracy_decimals=0,
                state_class=STATE_CLASS_MEASUREMENT,
            ),
            cv.Optional(CONF_LOAD): sensor.sensor_schema(
                accuracy_decimals=0,
                state_class=STATE_CLASS_MEASUREMENT,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_VOLTAGE): sensor.sensor_schema(
                unit_of_measurement=UNIT_VOLT,
                accuracy_decimals=1,
                device_class=DEVICE_CLASS_VOLTAGE,
                state_class=STATE_CLASS_MEASUREMENT,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_TEMPERATURE): sensor.sensor_schema(
                unit_of_measurement=UNIT_CELSIUS,
                accuracy_decimals=0,
                device_class=DEVICE_CLASS_TEMPERATURE,
                state_class=STATE_CLASS_MEASUREMENT,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_CURRENT): sensor.sensor_schema(
                unit_of_measurement=UNIT_MILLIAMP,
                accuracy_decimals=0,
                device_class=DEVICE_CLASS_CURRENT,
                state_class=STATE_CLASS_MEASUREMENT,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_TURNS): sensor.sensor_schema(
                unit_of_measurement=UNIT_TURNS,
                accuracy_decimals=2,
                state_class=STATE_CLASS_MEASUREMENT,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_HOLDING): binary_sensor.binary_sensor_schema(
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_OVERLOAD): binary_sensor.binary_sensor_schema(
                device_class=DEVICE_CLASS_PROBLEM,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_MOVE_RESULT): text_sensor.text_sensor_schema(
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_ERROR): text_sensor.text_sensor_schema(
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_MODEL): text_sensor.text_sensor_schema(
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_MOVING): binary_sensor.binary_sensor_schema(
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_ONLINE): binary_sensor.binary_sensor_schema(
                device_class=DEVICE_CLASS_CONNECTIVITY,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
        }
    )
    .extend(cv.polling_component_schema("1s"))
    .extend(uart.UART_DEVICE_SCHEMA)
)

FINAL_VALIDATE_SCHEMA = uart.final_validate_device_schema(
    "feetech_servo", baud_rate=1000000, require_tx=True, require_rx=True
)

SENSOR_SETTERS = {
    CONF_POSITION: "set_position_sensor",
    CONF_LOAD: "set_load_sensor",
    CONF_VOLTAGE: "set_voltage_sensor",
    CONF_TEMPERATURE: "set_temperature_sensor",
    CONF_CURRENT: "set_current_sensor",
    CONF_TURNS: "set_turns_sensor",
}

BINARY_SENSOR_SETTERS = {
    CONF_MOVING: "set_moving_sensor",
    CONF_ONLINE: "set_online_sensor",
    CONF_HOLDING: "set_holding_sensor",
    CONF_OVERLOAD: "set_overload_sensor",
}

TEXT_SENSOR_SETTERS = {
    CONF_MOVE_RESULT: "set_result_text_sensor",
    CONF_ERROR: "set_error_text_sensor",
    CONF_MODEL: "set_model_text_sensor",
}


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await uart.register_uart_device(var, config)

    cg.add(var.set_servo_id(config[CONF_SERVO_ID]))
    cg.add(var.set_default_speed(config[CONF_DEFAULT_SPEED]))
    cg.add(var.set_default_acceleration(config[CONF_DEFAULT_ACCELERATION]))
    cg.add(var.set_multi_turn(config[CONF_MULTI_TURN]))
    cg.add(var.set_tolerance(config[CONF_TOLERANCE]))
    cg.add(var.set_jam_load(config[CONF_JAM_LOAD]))
    cg.add(var.set_move_timeout(config[CONF_MOVE_TIMEOUT]))

    for key, setter in SENSOR_SETTERS.items():
        if key in config:
            sens = await sensor.new_sensor(config[key])
            cg.add(getattr(var, setter)(sens))

    for key, setter in BINARY_SENSOR_SETTERS.items():
        if key in config:
            bsens = await binary_sensor.new_binary_sensor(config[key])
            cg.add(getattr(var, setter)(bsens))

    for key, setter in TEXT_SENSOR_SETTERS.items():
        if key in config:
            tsens = await text_sensor.new_text_sensor(config[key])
            cg.add(getattr(var, setter)(tsens))
