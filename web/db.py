"""SQLite persistence for web request logs."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS request_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    client_ip TEXT,
    input_type TEXT NOT NULL,
    user_message TEXT NOT NULL,
    ai_reply TEXT,
    routed INTEGER NOT NULL DEFAULT 0,
    action TEXT,
    action_details TEXT,
    duration_ms INTEGER,
    status TEXT NOT NULL DEFAULT 'success',
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_request_logs_created_at
    ON request_logs (created_at DESC);
"""


@dataclass
class RequestLogEntry:
    id: int
    created_at: str
    client_ip: str | None
    input_type: str
    user_message: str
    ai_reply: str | None
    routed: bool
    action: str | None
    action_details: dict[str, Any]
    duration_ms: int | None
    status: str
    error_message: str | None


class RequestLogStore:
    """Thread-safe SQLite store for web assistant interactions."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)

    def add(
        self,
        *,
        client_ip: str | None,
        input_type: str,
        user_message: str,
        ai_reply: str | None = None,
        routed: bool = False,
        action: str | None = None,
        action_details: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        status: str = "success",
        error_message: str | None = None,
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        details_json = json.dumps(action_details or {}, ensure_ascii=False)
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO request_logs (
                        created_at, client_ip, input_type, user_message, ai_reply,
                        routed, action, action_details, duration_ms, status, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        created_at,
                        client_ip,
                        input_type,
                        user_message,
                        ai_reply,
                        1 if routed else 0,
                        action,
                        details_json,
                        duration_ms,
                        status,
                        error_message,
                    ),
                )
                conn.commit()
                return int(cursor.lastrowid)

    def list_recent(self, limit: int = 100) -> list[RequestLogEntry]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM request_logs
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def get(self, log_id: int) -> RequestLogEntry | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM request_logs WHERE id = ?",
                    (log_id,),
                ).fetchone()
        if row is None:
            return None
        return self._row_to_entry(row)

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> RequestLogEntry:
        details_raw = row["action_details"] or "{}"
        try:
            details = json.loads(details_raw)
        except json.JSONDecodeError:
            details = {"raw": details_raw}
        return RequestLogEntry(
            id=row["id"],
            created_at=row["created_at"],
            client_ip=row["client_ip"],
            input_type=row["input_type"],
            user_message=row["user_message"],
            ai_reply=row["ai_reply"],
            routed=bool(row["routed"]),
            action=row["action"],
            action_details=details,
            duration_ms=row["duration_ms"],
            status=row["status"],
            error_message=row["error_message"],
        )

    def to_dict(self, entry: RequestLogEntry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "created_at": entry.created_at,
            "client_ip": entry.client_ip,
            "input_type": entry.input_type,
            "user_message": entry.user_message,
            "ai_reply": entry.ai_reply,
            "routed": entry.routed,
            "action": entry.action,
            "action_details": entry.action_details,
            "duration_ms": entry.duration_ms,
            "status": entry.status,
            "error_message": entry.error_message,
        }
