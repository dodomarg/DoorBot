"""PIN code storage and validation.

Codes are never stored in plain text. Each code gets a random salt and is
hashed with PBKDF2-HMAC-SHA256, the same way you would treat a password. Only a
short hint (e.g. "1••••6") is kept so the UI can show something recognisable.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import secrets
import time
from typing import Any

from .db import Database

PBKDF2_ROUNDS = 120_000
KINDS = ("permanent", "temporary", "one_time", "recurring", "duress")
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class CodeError(ValueError):
    """Raised when a submitted code definition is not acceptable."""


def hash_code(code: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", code.encode("utf-8"), salt, PBKDF2_ROUNDS
    ).hex()


def make_hint(code: str) -> str:
    if len(code) <= 2:
        return "•" * len(code)
    return f"{code[0]}{'•' * (len(code) - 2)}{code[-1]}"


def validate_code_string(code: str) -> str:
    code = (code or "").strip()
    if not code.isdigit():
        raise CodeError("A PIN must contain digits only.")
    if not 4 <= len(code) <= 12:
        raise CodeError("A PIN must be between 4 and 12 digits long.")
    return code


def _minutes_now(now: dt.datetime) -> int:
    return now.hour * 60 + now.minute


class CodeStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------ CRUD
    def list_codes(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM codes ORDER BY name COLLATE NOCASE")
        return [self._public(dict(row)) for row in rows]

    def get(self, code_id: int) -> dict[str, Any] | None:
        rows = self.db.query("SELECT * FROM codes WHERE id = ?", (code_id,))
        return self._public(dict(rows[0])) if rows else None

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = (payload.get("name") or "").strip()
        if not name:
            raise CodeError("Please give the code a name.")

        code = validate_code_string(payload.get("code", ""))
        if self._find_matching(code) is not None:
            raise CodeError("That PIN is already in use by another entry.")

        fields = self._normalise(payload)
        salt = secrets.token_bytes(16)

        code_id = self.db.execute(
            """INSERT INTO codes
               (name, code_hash, code_salt, code_hint, kind, enabled, valid_from,
                valid_to, days_mask, start_minute, end_minute, max_uses,
                keypad_slot, notes, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                name,
                hash_code(code, salt),
                salt.hex(),
                make_hint(code),
                fields["kind"],
                1 if fields["enabled"] else 0,
                fields["valid_from"],
                fields["valid_to"],
                fields["days_mask"],
                fields["start_minute"],
                fields["end_minute"],
                fields["max_uses"],
                fields["keypad_slot"],
                fields["notes"],
                int(time.time()),
            ),
        )
        self.db.log("code_added", f"Added code '{name}'", actor="ui", code_id=code_id)
        return self.get(code_id)  # type: ignore[return-value]

    def update(self, code_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self.db.query("SELECT * FROM codes WHERE id = ?", (code_id,))
        if not existing:
            raise CodeError("That code no longer exists.")

        fields = self._normalise(payload, base=dict(existing[0]))
        name = (payload.get("name") or existing[0]["name"]).strip()
        if not name:
            raise CodeError("Please give the code a name.")

        sets = [
            "name = ?",
            "kind = ?",
            "enabled = ?",
            "valid_from = ?",
            "valid_to = ?",
            "days_mask = ?",
            "start_minute = ?",
            "end_minute = ?",
            "max_uses = ?",
            "keypad_slot = ?",
            "notes = ?",
        ]
        params: list[Any] = [
            name,
            fields["kind"],
            1 if fields["enabled"] else 0,
            fields["valid_from"],
            fields["valid_to"],
            fields["days_mask"],
            fields["start_minute"],
            fields["end_minute"],
            fields["max_uses"],
            fields["keypad_slot"],
            fields["notes"],
        ]

        if payload.get("code"):
            code = validate_code_string(payload["code"])
            other = self._find_matching(code)
            if other is not None and other["id"] != code_id:
                raise CodeError("That PIN is already in use by another entry.")
            salt = secrets.token_bytes(16)
            sets += ["code_hash = ?", "code_salt = ?", "code_hint = ?"]
            params += [hash_code(code, salt), salt.hex(), make_hint(code)]

        params.append(code_id)
        self.db.execute(f"UPDATE codes SET {', '.join(sets)} WHERE id = ?", params)
        self.db.log("code_updated", f"Updated code '{name}'", actor="ui", code_id=code_id)
        return self.get(code_id)  # type: ignore[return-value]

    def delete(self, code_id: int) -> None:
        rows = self.db.query("SELECT name FROM codes WHERE id = ?", (code_id,))
        if not rows:
            raise CodeError("That code no longer exists.")
        self.db.execute("DELETE FROM codes WHERE id = ?", (code_id,))
        self.db.log(
            "code_deleted", f"Deleted code '{rows[0]['name']}'", actor="ui",
            code_id=code_id,
        )

    def reset_usage(self, code_id: int) -> dict[str, Any] | None:
        self.db.execute(
            "UPDATE codes SET use_count = 0, enabled = 1 WHERE id = ?", (code_id,)
        )
        return self.get(code_id)

    # ------------------------------------------------------------ validation
    def check(self, code: str, now: dt.datetime | None = None) -> dict[str, Any]:
        """Validate a PIN. Returns a result dict; never raises for a bad PIN."""
        now = now or dt.datetime.now()
        code = (code or "").strip()

        if not code.isdigit():
            return {"allowed": False, "reason": "invalid_format"}

        match = self._find_matching(code)
        if match is None:
            return {"allowed": False, "reason": "unknown_code"}

        reason = self._window_reason(match, now)
        if reason:
            return {
                "allowed": False,
                "reason": reason,
                "code_id": match["id"],
                "name": match["name"],
            }

        return {
            "allowed": True,
            "reason": "ok",
            "code_id": match["id"],
            "name": match["name"],
            "kind": match["kind"],
            "duress": match["kind"] == "duress",
        }

    def register_use(self, code_id: int) -> None:
        rows = self.db.query("SELECT * FROM codes WHERE id = ?", (code_id,))
        if not rows:
            return
        row = dict(rows[0])
        use_count = row["use_count"] + 1
        enabled = row["enabled"]
        if row["kind"] == "one_time" or (
            row["max_uses"] is not None and use_count >= row["max_uses"]
        ):
            enabled = 0
        self.db.execute(
            "UPDATE codes SET use_count = ?, last_used = ?, enabled = ? WHERE id = ?",
            (use_count, int(time.time()), enabled, code_id),
        )

    # --------------------------------------------------------------- internal
    def _find_matching(self, code: str) -> dict[str, Any] | None:
        """Constant-time-ish lookup across all stored codes."""
        found: dict[str, Any] | None = None
        for row in self.db.query("SELECT * FROM codes"):
            item = dict(row)
            candidate = hash_code(code, bytes.fromhex(item["code_salt"]))
            if hmac.compare_digest(candidate, item["code_hash"]) and found is None:
                found = item
        return found

    @staticmethod
    def _window_reason(row: dict[str, Any], now: dt.datetime) -> str | None:
        if not row["enabled"]:
            return "disabled"
        ts = int(now.timestamp())
        if row["valid_from"] and ts < row["valid_from"]:
            return "not_yet_valid"
        if row["valid_to"] and ts > row["valid_to"]:
            return "expired"
        if row["max_uses"] is not None and row["use_count"] >= row["max_uses"]:
            return "use_limit_reached"
        if row["kind"] == "recurring":
            if not (row["days_mask"] >> now.weekday()) & 1:
                return "wrong_day"
            minutes = _minutes_now(now)
            start, end = row["start_minute"], row["end_minute"]
            if start <= end:
                inside = start <= minutes < end
            else:  # window wraps past midnight
                inside = minutes >= start or minutes < end
            if not inside:
                return "outside_hours"
        return None

    @staticmethod
    def _normalise(payload: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
        base = base or {}

        def pick(key: str, default: Any) -> Any:
            if key in payload:
                return payload[key]
            return base.get(key, default)

        kind = str(pick("kind", "permanent"))
        if kind not in KINDS:
            raise CodeError(f"Unknown code type '{kind}'.")

        def as_ts(key: str) -> int | None:
            value = pick(key, None)
            if value in (None, "", 0):
                return None
            if isinstance(value, (int, float)):
                return int(value)
            try:
                # Accept "2026-09-01T18:30" from <input type=datetime-local>
                return int(dt.datetime.fromisoformat(str(value)).timestamp())
            except ValueError as exc:
                raise CodeError(f"Could not understand the date in '{key}'.") from exc

        def as_int(key: str, default: int) -> int:
            value = pick(key, default)
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        max_uses = pick("max_uses", None)
        if kind == "one_time":
            max_uses = 1
        elif max_uses in (None, "", 0):
            max_uses = None
        else:
            max_uses = int(max_uses)

        slot = pick("keypad_slot", None)
        slot = int(slot) if str(slot).strip() not in ("", "None") else None

        valid_from, valid_to = as_ts("valid_from"), as_ts("valid_to")
        if valid_from and valid_to and valid_to <= valid_from:
            raise CodeError("The end date must be after the start date.")

        days_mask = as_int("days_mask", 127) & 0b1111111
        if kind == "recurring" and days_mask == 0:
            raise CodeError("Pick at least one day for a recurring code.")

        return {
            "kind": kind,
            "enabled": bool(pick("enabled", True)),
            "valid_from": valid_from,
            "valid_to": valid_to,
            "days_mask": days_mask,
            "start_minute": max(0, min(1440, as_int("start_minute", 0))),
            "end_minute": max(0, min(1440, as_int("end_minute", 1440))),
            "max_uses": max_uses,
            "keypad_slot": slot,
            "notes": str(pick("notes", "")),
        }

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        row.pop("code_hash", None)
        row.pop("code_salt", None)
        row["enabled"] = bool(row["enabled"])
        row["days"] = [
            DAY_NAMES[i] for i in range(7) if (row.get("days_mask", 127) >> i) & 1
        ]
        return row


def suggest_code(length: int = 6) -> str:
    """Generate a random PIN that avoids trivially guessable patterns."""
    while True:
        code = "".join(secrets.choice("0123456789") for _ in range(length))
        if len(set(code)) < 3:
            continue
        digits = [int(c) for c in code]
        deltas = {b - a for a, b in zip(digits, digits[1:])}
        if len(deltas) == 1 and deltas.pop() in (-1, 0, 1):
            continue  # sequential or repeated
        return code


__all__ = [
    "CodeStore",
    "CodeError",
    "suggest_code",
    "validate_code_string",
    "DAY_NAMES",
    "KINDS",
]
