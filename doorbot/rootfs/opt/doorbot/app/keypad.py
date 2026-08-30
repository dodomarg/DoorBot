"""SwitchBot Keypad credential handling.

DoorBot talks to a SwitchBot Keypad the same way a real SwitchBot Lock does.
The ESP32 impersonates a lock over BLE (see the ``switchbot_keypad_bridge``
component in ``esphome/doorbot.yaml``), the keypad pairs to it, and every
unlock arrives as an **AES-CTR encrypted** frame rather than a broadcast.

The decrypted frame carries two things DoorBot cares about:

``method``
    How the credential was presented -- ``pin``, ``nfc``, ``fingerprint``,
    ``face`` (or ``unknown``). These map to the method byte in the frame:
    PIN ``0x04``, NFC ``0x08``, fingerprint ``0x0C``, face ``0x18``.

``index``
    The zero-based **credential slot**, i.e. the order the credential was added
    in the SwitchBot app. Slot 0 is the first credential, slot 1 the second, and
    so on.

Together those identify *who* is at the door, which is what makes per-person
naming, scheduling and auditing possible. This module maps ``(method, slot)``
to a DoorBot credential record and enforces the schedule.

Why this replaced the old approach: DoorBot used to sniff the keypad's
unencrypted status advertisement and watch an ``attempt_state`` counter
(+1 rejected, +2 accepted). That worked without any pairing, but the
advertisement is replayable and anonymous -- it never revealed which PIN was
used. The encrypted path has neither weakness, so the advertisement path was
removed entirely.
"""

from __future__ import annotations

import time
from typing import Any

METHOD_PIN = "pin"
METHOD_NFC = "nfc"
METHOD_FINGERPRINT = "fingerprint"
METHOD_FACE = "face"
METHOD_UNKNOWN = "unknown"

#: Method byte inside a decrypted keypad frame -> DoorBot's method name.
METHOD_BYTES = {
    0x04: METHOD_PIN,
    0x08: METHOD_NFC,
    0x0C: METHOD_FINGERPRINT,
    0x18: METHOD_FACE,
}

METHODS = (METHOD_PIN, METHOD_NFC, METHOD_FINGERPRINT, METHOD_FACE, METHOD_UNKNOWN)

METHOD_LABELS = {
    METHOD_PIN: "PIN",
    METHOD_NFC: "NFC tag",
    METHOD_FINGERPRINT: "Fingerprint",
    METHOD_FACE: "Face",
    METHOD_UNKNOWN: "Unknown",
}

METHOD_ICONS = {
    METHOD_PIN: "\u2328",
    METHOD_NFC: "\U0001f4f6",
    METHOD_FINGERPRINT: "\u261d",
    METHOD_FACE: "\U0001f642",
    METHOD_UNKNOWN: "\u2753",
}

RESULT_ACCEPTED = "accepted"
RESULT_REJECTED = "rejected"
RESULT_UNKNOWN = "unknown"

#: Slots are what the SwitchBot app hands out; keep the range sane.
MAX_SLOT = 99


def normalise_method(value: Any) -> str:
    """Accept either a method name or the raw method byte from a frame."""
    if isinstance(value, bool):
        return METHOD_UNKNOWN
    if isinstance(value, int):
        return METHOD_BYTES.get(value, METHOD_UNKNOWN)
    text = str(value or "").strip().lower()
    if text in METHODS:
        return text
    # Tolerate "0x0c" / "12" coming from an automation template.
    try:
        return METHOD_BYTES.get(int(text, 0), METHOD_UNKNOWN)
    except (TypeError, ValueError):
        return METHOD_UNKNOWN


def credential_key(method: str, slot: int) -> str:
    return f"{normalise_method(method)}:{int(slot)}"


def describe(method: str, slot: int, name: str = "") -> str:
    label = METHOD_LABELS.get(normalise_method(method), "Unknown")
    if name:
        return f"{name} ({label} slot {slot})"
    return f"{label} slot {slot}"


class KeypadWatcher:
    """Tracks the paired keypad and maps credential slots to people.

    A *credential* here is DoorBot's own record describing one slot in the
    SwitchBot app: who it belongs to, whether it is allowed, and when. The
    secret itself (the PIN digits, the fingerprint template) never leaves the
    keypad, so DoorBot stores no secret for these -- only the identity and the
    policy.
    """

    def __init__(self, db: Any) -> None:
        self.db = db

    # ------------------------------------------------------------------ state
    def _state(self) -> dict[str, Any]:
        state = self.db.get_setting("keypad_state") or {}
        return {
            "paired": bool(state.get("paired", False)),
            "keypad_name": state.get("keypad_name", ""),
            "battery": state.get("battery"),
            "last_seen": state.get("last_seen"),
            "last_result": state.get("last_result", RESULT_UNKNOWN),
            "last_method": state.get("last_method"),
            "last_slot": state.get("last_slot"),
            "address": state.get("address", ""),
        }

    def snapshot(self) -> dict[str, Any]:
        state = self._state()
        state["settings"] = self.settings()
        state["credentials"] = self.credentials()
        state["methods"] = [
            {"value": m, "label": METHOD_LABELS[m], "icon": METHOD_ICONS[m]}
            for m in METHODS
            if m != METHOD_UNKNOWN
        ]
        return state

    # --------------------------------------------------------------- settings
    def settings(self) -> dict[str, Any]:
        stored = self.db.get_setting("keypad_settings") or {}
        return {
            "enabled": bool(stored.get("enabled", True)),
            # When true, only slots with a DoorBot credential record may open
            # the door. When false, any slot the keypad accepts is allowed.
            "known_credentials_only": bool(
                stored.get("known_credentials_only", False)
            ),
            "action": stored.get("action", "unlock"),  # unlock | toggle | notify
            "min_interval_seconds": int(stored.get("min_interval_seconds", 2)),
        }

    def save_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        settings = self.settings()
        for key in settings:
            if key in values:
                settings[key] = values[key]
        settings["enabled"] = bool(settings["enabled"])
        settings["known_credentials_only"] = bool(settings["known_credentials_only"])
        settings["min_interval_seconds"] = max(
            0, int(settings["min_interval_seconds"] or 0)
        )
        if settings["action"] not in ("unlock", "toggle", "notify"):
            settings["action"] = "unlock"
        self.db.set_setting("keypad_settings", settings)
        self.db.log("keypad", "Keypad settings saved", actor="ui")
        return settings

    # ------------------------------------------------------------ credentials
    def _raw_credentials(self) -> dict[str, Any]:
        return self.db.get_setting("keypad_credentials") or {}

    def credentials(self) -> list[dict[str, Any]]:
        items = []
        for key, cred in self._raw_credentials().items():
            item = dict(cred)
            item["key"] = key
            item["label"] = METHOD_LABELS.get(item.get("method"), "Unknown")
            item["icon"] = METHOD_ICONS.get(item.get("method"), "?")
            items.append(item)
        items.sort(key=lambda c: (c.get("method", ""), c.get("slot", 0)))
        return items

    def save_credential(self, values: dict[str, Any]) -> dict[str, Any]:
        method = normalise_method(values.get("method"))
        if method == METHOD_UNKNOWN:
            raise ValueError("Choose how this credential is presented.")
        try:
            slot = int(values.get("slot"))
        except (TypeError, ValueError) as exc:
            raise ValueError("A credential slot number is required.") from exc
        if not 0 <= slot <= MAX_SLOT:
            raise ValueError(f"Slot must be between 0 and {MAX_SLOT}.")

        name = str(values.get("name", "")).strip()
        if not name:
            raise ValueError("Give this credential a name.")

        cred = {
            "method": method,
            "slot": slot,
            "name": name,
            "enabled": bool(values.get("enabled", True)),
            "notify": bool(values.get("notify", False)),
            "duress": bool(values.get("duress", False)),
            "days_mask": int(values.get("days_mask", 127)) & 0x7F,
            "start_minute": self._minute(values.get("start_minute"), 0),
            "end_minute": self._minute(values.get("end_minute"), 1440),
            "note": str(values.get("note", "")).strip(),
        }

        creds = self._raw_credentials()
        key = credential_key(method, slot)
        cred["created_at"] = creds.get(key, {}).get("created_at", int(time.time()))
        creds[key] = cred
        self.db.set_setting("keypad_credentials", creds)
        self.db.log(
            "keypad_credential",
            f"Saved keypad credential {describe(method, slot, name)}",
            actor="ui",
        )
        return {"key": key, **cred}

    def delete_credential(self, key: str) -> None:
        creds = self._raw_credentials()
        if key not in creds:
            raise KeyError(key)
        cred = creds.pop(key)
        self.db.set_setting("keypad_credentials", creds)
        self.db.log(
            "keypad_credential",
            "Removed keypad credential "
            + describe(
                cred.get("method", ""), cred.get("slot", 0), cred.get("name", "")
            ),
            actor="ui",
        )

    @staticmethod
    def _minute(value: Any, fallback: int) -> int:
        if value is None or value == "":
            return fallback
        if isinstance(value, str) and ":" in value:
            hours, _, minutes = value.partition(":")
            try:
                return (int(hours) * 60 + int(minutes)) % 1441
            except ValueError:
                return fallback
        try:
            return max(0, min(1440, int(value)))
        except (TypeError, ValueError):
            return fallback

    # ------------------------------------------------------------- evaluation
    def _window_reason(self, cred: dict[str, Any], now: float) -> str | None:
        """Return a rejection reason, or None when the credential is in window."""
        if not cred.get("enabled", True):
            return "This credential is disabled."

        local = time.localtime(now)
        days_mask = int(cred.get("days_mask", 127)) & 0x7F
        start = int(cred.get("start_minute", 0))
        end = int(cred.get("end_minute", 1440))
        minute_of_day = local.tm_hour * 60 + local.tm_min

        # Monday-first bitmask, matching the PIN code store.
        today_bit = 1 << local.tm_wday
        yesterday_bit = 1 << ((local.tm_wday - 1) % 7)

        if start == end:
            return "This credential has an empty time window."

        if start < end:
            if not days_mask & today_bit:
                return "This credential is not allowed today."
            if not start <= minute_of_day < end:
                return "This credential is outside its allowed hours."
            return None

        # Window wraps past midnight: the evening part belongs to today, the
        # morning part belongs to yesterday's day bit.
        if minute_of_day >= start:
            if not days_mask & today_bit:
                return "This credential is not allowed today."
            return None
        if minute_of_day < end:
            if not days_mask & yesterday_bit:
                return "This credential is not allowed today."
            return None
        return "This credential is outside its allowed hours."

    def ingest(
        self,
        method: Any,
        slot: int,
        keypad_name: str = "",
        battery: int | None = None,
        address: str = "",
    ) -> dict[str, Any]:
        """Handle one decrypted unlock frame from the paired keypad.

        The keypad has already verified the credential itself over an encrypted
        channel, so this is an authorisation decision, not an authentication
        one: DoorBot decides whether *this* credential is allowed *right now*.
        """
        method = normalise_method(method)
        slot = int(slot)
        now = time.time()
        state = self._state()
        settings = self.settings()

        key = credential_key(method, slot)
        cred = self._raw_credentials().get(key)

        result = RESULT_ACCEPTED
        reason = ""
        throttled = False

        if not settings["enabled"]:
            result, reason = RESULT_REJECTED, "The keypad is disabled in DoorBot."
        elif cred is None and settings["known_credentials_only"]:
            result, reason = (
                RESULT_REJECTED,
                f"Unknown credential ({describe(method, slot)}). Add it in DoorBot first.",
            )
        elif cred is not None:
            window = self._window_reason(cred, now)
            if window:
                result, reason = RESULT_REJECTED, window

        # Debounce duplicate frames, e.g. a retry from the keypad.
        if (
            result == RESULT_ACCEPTED
            and state["last_seen"]
            and state["last_method"] == method
            and state["last_slot"] == slot
            and now - float(state["last_seen"]) < settings["min_interval_seconds"]
        ):
            throttled = True
            result, reason = RESULT_UNKNOWN, "Ignored a repeated keypad event."

        name = cred.get("name", "") if cred else ""

        self.db.set_setting(
            "keypad_state",
            {
                "paired": True,
                "keypad_name": keypad_name or state["keypad_name"],
                "battery": battery if battery is not None else state["battery"],
                "last_seen": now,
                "last_result": result,
                "last_method": method,
                "last_slot": slot,
                "address": address or state["address"],
            },
        )

        if result != RESULT_UNKNOWN:
            self.db.log(
                f"keypad_{result}",
                (
                    f"Keypad unlocked by {describe(method, slot, name)}"
                    if result == RESULT_ACCEPTED
                    else f"Keypad refused {describe(method, slot, name)}: {reason}"
                ),
                actor=name or "keypad",
                method=method,
                slot=slot,
                credential=name,
                duress=bool(cred.get("duress")) if cred else False,
            )

        return {
            "result": result,
            "reason": reason,
            "method": method,
            "method_label": METHOD_LABELS[method],
            "slot": slot,
            "name": name,
            "known": cred is not None,
            "duress": bool(cred.get("duress")) if cred else False,
            "notify": bool(cred.get("notify")) if cred else False,
            "battery": battery,
            "throttled": throttled,
            "settings": settings,
        }
