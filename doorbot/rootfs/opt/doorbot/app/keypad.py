"""SwitchBot Keypad (WoKeypad) event handling.

Protocol notes -- all verified against pySwitchbot (sblibs/pySwitchbot), the
library Home Assistant uses:

* The keypad advertises under SwitchBot's BLE service UUID
  ``0000fd3d-0000-1000-8000-00805f9b34fb`` with service-data byte 0 == ``'y'``
  (or ``'Y'``), and manufacturer data under company id ``2409``.
* ``service_data[2] & 0x7F``  -> battery percentage.
* ``manufacturer_data[6]``    -> ``attempt_state``, an 8-bit wrapping counter.
* The advertisement is **not encrypted** (``isEncrypted: False``).

The success rule below is taken from the original pySwitchbot implementation in
commit 536f7c5 of PR #252: the counter advances by **1 for a rejected attempt**
and by **2 for an accepted one**, so a delta of 2 (modulo 256) means the keypad
accepted a PIN.

SECURITY: because the advertisement is unencrypted and carries no nonce or
signature, it is replayable by anyone with a BLE radio, and it does not say
*which* PIN was used. Treat it as a convenience trigger, not as the sole
authority for opening a door. ``require_local_pin`` keeps DoorBot's own PIN
validation in charge and uses the keypad only as a hint.
"""

from __future__ import annotations

import time
from typing import Any

SERVICE_UUID = "0000fd3d-0000-1000-8000-00805f9b34fb"
LEGACY_SERVICE_UUID = "00000d00-0000-1000-8000-00805f9b34fb"
MANUFACTURER_ID = 2409
MODEL_CHARS = ("y", "Y")

RESULT_ACCEPTED = "accepted"
RESULT_REJECTED = "rejected"
RESULT_UNKNOWN = "unknown"


def parse_advertisement(
    service_data: bytes | None, manufacturer_data: bytes | None
) -> dict[str, Any] | None:
    """Decode a raw SwitchBot Keypad advertisement.

    Mirrors ``switchbot.adv_parsers.keypad.process_wokeypad``.
    """
    if not service_data or not manufacturer_data:
        return None
    if len(service_data) < 3 or len(manufacturer_data) < 7:
        return None
    if chr(service_data[0]) not in MODEL_CHARS:
        return None
    return {
        "battery": service_data[2] & 0b0111_1111,
        "attempt_state": manufacturer_data[6],
    }


def classify(previous: int | None, current: int) -> str:
    """Compare two consecutive ``attempt_state`` values.

    Returns ``accepted``, ``rejected`` or ``unknown`` (first sighting / no change).
    """
    if previous is None or previous < 0:
        return RESULT_UNKNOWN
    delta = (current - previous) % 256
    if delta == 0:
        return RESULT_UNKNOWN
    if delta >= 2:
        return RESULT_ACCEPTED
    return RESULT_REJECTED


class KeypadWatcher:
    """Stateful tracker for one keypad, with basic anti-replay protection."""

    def __init__(self, db: Any) -> None:
        self.db = db

    def _state(self) -> dict[str, Any]:
        state = self.db.get_setting("keypad_state") or {}
        return {
            "last_attempt_state": state.get("last_attempt_state"),
            "battery": state.get("battery"),
            "last_seen": state.get("last_seen"),
            "last_result": state.get("last_result", RESULT_UNKNOWN),
            "address": state.get("address", ""),
        }

    def snapshot(self) -> dict[str, Any]:
        state = self._state()
        settings = self.settings()
        state["configured"] = bool(settings.get("enabled"))
        state["settings"] = settings
        return state

    def settings(self) -> dict[str, Any]:
        stored = self.db.get_setting("keypad_settings") or {}
        return {
            "enabled": bool(stored.get("enabled", False)),
            "address": str(stored.get("address", "")),
            # When true, an accepted keypad event alone will NOT open the door;
            # DoorBot still requires its own PIN check from another source.
            "require_local_pin": bool(stored.get("require_local_pin", False)),
            "action": stored.get("action", "unlock"),  # unlock | toggle | notify
            "min_interval_seconds": int(stored.get("min_interval_seconds", 2)),
        }

    def save_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        settings = self.settings()
        for key in settings:
            if key in values:
                settings[key] = values[key]
        settings["enabled"] = bool(settings["enabled"])
        settings["require_local_pin"] = bool(settings["require_local_pin"])
        settings["min_interval_seconds"] = max(
            0, int(settings["min_interval_seconds"] or 0)
        )
        if settings["action"] not in ("unlock", "toggle", "notify"):
            settings["action"] = "unlock"
        self.db.set_setting("keypad_settings", settings)
        self.db.log("keypad", "Keypad settings saved", actor="ui")
        return settings

    def ingest(
        self,
        attempt_state: int,
        battery: int | None = None,
        address: str = "",
    ) -> dict[str, Any]:
        """Feed a new attempt_state reading and decide what it means."""
        state = self._state()
        previous = state["last_attempt_state"]
        now = int(time.time())
        result = classify(previous, attempt_state)

        settings = self.settings()
        throttled = False
        if (
            result == RESULT_ACCEPTED
            and state["last_seen"]
            and now - int(state["last_seen"]) < settings["min_interval_seconds"]
        ):
            throttled = True
            result = RESULT_UNKNOWN

        self.db.set_setting(
            "keypad_state",
            {
                "last_attempt_state": attempt_state,
                "battery": battery if battery is not None else state["battery"],
                "last_seen": now,
                "last_result": result,
                "address": address or state["address"],
            },
        )

        if result != RESULT_UNKNOWN:
            self.db.log(
                f"keypad_{result}",
                "Keypad accepted a PIN"
                if result == RESULT_ACCEPTED
                else "Keypad rejected a PIN",
                actor="keypad",
                attempt_state=attempt_state,
                previous=previous,
                battery=battery,
            )

        return {
            "result": result,
            "attempt_state": attempt_state,
            "previous": previous,
            "battery": battery,
            "throttled": throttled,
            "settings": settings,
        }
