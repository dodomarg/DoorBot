"""SQLite persistence for calibration, PIN codes and the event log."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS codes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    code_hash     TEXT    NOT NULL,
    code_salt     TEXT    NOT NULL,
    code_hint     TEXT    NOT NULL DEFAULT '',
    kind          TEXT    NOT NULL DEFAULT 'permanent',
    enabled       INTEGER NOT NULL DEFAULT 1,
    valid_from    INTEGER,
    valid_to      INTEGER,
    days_mask     INTEGER NOT NULL DEFAULT 127,
    start_minute  INTEGER NOT NULL DEFAULT 0,
    end_minute    INTEGER NOT NULL DEFAULT 1440,
    max_uses      INTEGER,
    use_count     INTEGER NOT NULL DEFAULT 0,
    last_used     INTEGER,
    notes         TEXT    NOT NULL DEFAULT '',
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         INTEGER NOT NULL,
    kind       TEXT    NOT NULL,
    actor      TEXT    NOT NULL DEFAULT '',
    message    TEXT    NOT NULL DEFAULT '',
    detail     TEXT    NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts DESC);
"""

# Sensible starting point for a Feetech STS3215 driving a euro-profile thumbturn.
DEFAULT_CALIBRATION: dict[str, Any] = {
    "locked_position": 2048,
    "unlocked_position": 1024,
    "overshoot": 0,
    "hold_ms": 400,
    "speed": 800,
    "acceleration": 30,
    "torque_limit": 700,
    "stall_load": 850,
    "invert": False,
    "servo_id": 1,
    "baud": 1000000,
    "auto_lock_seconds": 0,
    "calibrated": False,
}


class Database:
    """Small thread-safe wrapper around sqlite3."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        self._seed_defaults()

    def _seed_defaults(self) -> None:
        if self.get_setting("calibration") is None:
            self.set_setting("calibration", DEFAULT_CALIBRATION)

    # ---------------------------------------------------------------- helpers
    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(params)))

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur.lastrowid if cur.lastrowid is not None else cur.rowcount

    # --------------------------------------------------------------- settings
    def get_setting(self, key: str, default: Any = None) -> Any:
        rows = self.query("SELECT value FROM settings WHERE key = ?", (key,))
        if not rows:
            return default
        try:
            return json.loads(rows[0]["value"])
        except json.JSONDecodeError:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    # ------------------------------------------------------------ calibration
    def get_calibration(self) -> dict[str, Any]:
        cal = dict(DEFAULT_CALIBRATION)
        stored = self.get_setting("calibration") or {}
        if isinstance(stored, dict):
            cal.update(stored)
        return cal

    def save_calibration(self, values: dict[str, Any]) -> dict[str, Any]:
        cal = self.get_calibration()
        cal.update(values)
        self.set_setting("calibration", cal)
        return cal

    # ----------------------------------------------------------------- events
    def log(self, kind: str, message: str, actor: str = "", **detail: Any) -> None:
        self.execute(
            "INSERT INTO events (ts, kind, actor, message, detail) VALUES (?,?,?,?,?)",
            (int(time.time()), kind, actor, message, json.dumps(detail)),
        )
        # Keep the log bounded so /data never grows without limit.
        self.execute(
            "DELETE FROM events WHERE id NOT IN "
            "(SELECT id FROM events ORDER BY id DESC LIMIT 2000)"
        )

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)
        )
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["detail"] = json.loads(item["detail"])
            except (json.JSONDecodeError, TypeError):
                item["detail"] = {}
            out.append(item)
        return out
