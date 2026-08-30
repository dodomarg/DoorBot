"""Runtime configuration for the DoorBot add-on.

Reads Home Assistant add-on options from /data/options.json when running under
the Supervisor, and falls back to environment variables so the exact same code
can be run on a normal PC for development and testing.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    # "mock" runs a fully simulated STS3215 servo so the UI can be exercised
    # without any hardware. "esphome" drives a real device through Home Assistant.
    "backend": "mock",
    # Entity ids on the ESPHome node (only used when backend == "esphome").
    "lock_entity": "lock.doorbot_lock",
    "position_number_entity": "number.doorbot_target_position",
    "position_sensor_entity": "sensor.doorbot_position",
    "load_sensor_entity": "sensor.doorbot_load",
    "torque_switch_entity": "switch.doorbot_servo_torque",
    # Home Assistant connection. Under the Supervisor these are injected.
    "hass_url": "http://supervisor/core",
    "hass_token": "",
    # Behaviour
    "auto_lock_seconds": 0,
    "max_failed_attempts": 5,
    "lockout_seconds": 300,
    "log_level": "info",
}


@dataclass
class Config:
    options: dict[str, Any] = field(default_factory=dict)
    data_dir: Path = Path("/data")
    web_dir: Path = Path(__file__).resolve().parent.parent / "web"
    port: int = 8099
    ingress_entry: str = ""

    @classmethod
    def load(cls) -> "Config":
        options = dict(DEFAULTS)

        options_file = Path(os.environ.get("DOORBOT_OPTIONS", "/data/options.json"))
        if options_file.is_file():
            try:
                options.update(json.loads(options_file.read_text()))
            except (OSError, json.JSONDecodeError):
                pass

        # Environment overrides (used for local development).
        for key in DEFAULTS:
            env_key = f"DOORBOT_{key.upper()}"
            if env_key in os.environ:
                raw = os.environ[env_key]
                default = DEFAULTS[key]
                if isinstance(default, bool):
                    options[key] = raw.strip().lower() in ("1", "true", "yes", "on")
                elif isinstance(default, int):
                    try:
                        options[key] = int(raw)
                    except ValueError:
                        pass
                else:
                    options[key] = raw

        # The Supervisor injects a token for talking to the Core API.
        supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")
        if supervisor_token and not options.get("hass_token"):
            options["hass_token"] = supervisor_token

        data_dir = Path(os.environ.get("DOORBOT_DATA", "/data"))
        data_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            options=options,
            data_dir=data_dir,
            port=int(os.environ.get("DOORBOT_PORT", "8099")),
            ingress_entry=os.environ.get("DOORBOT_INGRESS_ENTRY", ""),
        )

    def get(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, DEFAULTS.get(key, default))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "doorbot.db"
