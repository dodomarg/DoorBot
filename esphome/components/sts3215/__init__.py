"""ESPHome component for Feetech STS3215 / SMS-STS serial bus servos.

Designed for a Seeed XIAO ESP32S3 plugged into a Seeed "Bus Servo Driver Board
for XIAO" (a.k.a. XIAO Bus Servo Adapter), which handles the half-duplex bus
conversion, so a plain UART with separate tx/rx pins is all that is needed.
"""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import binary_sensor, sensor, uart
from esphome.const import (
    CONF_CURRENT,
    CONF_ID,
    CONF_TEMPERATURE,
    CONF_VOLTAGE,
    DEVICE_CLASS_CONNECTIVITY,
    DEVICE_CLASS_CURRENT,
    DEVICE_CLASS_TEMPERATURE,
    DEVICE_CLASS_VOLTAGE,
    ENTITY_CATEGORY_DIAGNOSTIC,
    STATE_CLASS_MEASUREMENT,
    UNIT_CELSIUS,
    UNIT_VOLT,
)

CODEOWNERS = ["@dodomarg"]
DEPENDENCIES = ["uart"]
AUTO_LOAD = ["sensor", "binary_sensor"]
MULTI_CONF = True

CONF_SERVO_ID = "servo_id"
CONF_DEFAULT_SPEED = "default_speed"
CONF_DEFAULT_ACCELERATION = "default_acceleration"
CONF_POSITION = "position"
CONF_LOAD = "load"
CONF_MOVING = "moving"
CONF_ONLINE = "online"

UNIT_MILLIAMP = "mA"
UNIT_STEPS = "steps"

sts3215_ns = cg.esphome_ns.namespace("sts3215")
STS3215 = sts3215_ns.class_("STS3215", cg.PollingComponent, uart.UARTDevice)

CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(STS3215),
            cv.Optional(CONF_SERVO_ID, default=1): cv.int_range(min=0, max=253),
            cv.Optional(CONF_DEFAULT_SPEED, default=800): cv.int_range(min=0, max=4000),
            cv.Optional(CONF_DEFAULT_ACCELERATION, default=30): cv.int_range(min=0, max=255),
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
    "sts3215", baud_rate=1000000, require_tx=True, require_rx=True
)

SENSOR_SETTERS = {
    CONF_POSITION: "set_position_sensor",
    CONF_LOAD: "set_load_sensor",
    CONF_VOLTAGE: "set_voltage_sensor",
    CONF_TEMPERATURE: "set_temperature_sensor",
    CONF_CURRENT: "set_current_sensor",
}

BINARY_SENSOR_SETTERS = {
    CONF_MOVING: "set_moving_sensor",
    CONF_ONLINE: "set_online_sensor",
}


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await uart.register_uart_device(var, config)

    cg.add(var.set_servo_id(config[CONF_SERVO_ID]))
    cg.add(var.set_default_speed(config[CONF_DEFAULT_SPEED]))
    cg.add(var.set_default_acceleration(config[CONF_DEFAULT_ACCELERATION]))

    for key, setter in SENSOR_SETTERS.items():
        if key in config:
            sens = await sensor.new_sensor(config[key])
            cg.add(getattr(var, setter)(sens))

    for key, setter in BINARY_SENSOR_SETTERS.items():
        if key in config:
            bsens = await binary_sensor.new_binary_sensor(config[key])
            cg.add(getattr(var, setter)(bsens))
