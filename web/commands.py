"""Voice command keywords stored in SQLite."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_COMMANDS: list[tuple[str, str]] = [
    ("stop", "stop_speech"),
    ("cancel", "cancel_request"),
    ("never mind", "cancel_request"),
    ("repeat", "repeat_last"),
    ("pause listening", "pause_listening"),
    ("resume listening", "resume_listening"),
]

VALID_ACTIONS = frozenset(
    {
        "stop_speech",
        "cancel_request",
        "repeat_last",
        "pause_listening",
        "resume_listening",
    }
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL UNIQUE COLLATE NOCASE,
    action TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_voice_commands_enabled
    ON voice_commands (enabled);
"""


@dataclass
class VoiceCommand:
    id: int
    keyword: str
    action: str
    enabled: bool
    created_at: str


class VoiceCommandStore:
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
                count = conn.execute("SELECT COUNT(*) FROM voice_commands").fetchone()[0]
                if count == 0:
                    now = datetime.now(timezone.utc).isoformat()
                    conn.executemany(
                        """
                        INSERT INTO voice_commands (keyword, action, enabled, created_at)
                        VALUES (?, ?, 1, ?)
                        """,
                        [(keyword, action, now) for keyword, action in DEFAULT_COMMANDS],
                    )
                    conn.commit()

    def list_all(self) -> list[VoiceCommand]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM voice_commands ORDER BY keyword COLLATE NOCASE"
                ).fetchall()
        return [self._row_to_command(row) for row in rows]

    def list_enabled(self) -> list[VoiceCommand]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM voice_commands
                    WHERE enabled = 1
                    ORDER BY LENGTH(keyword) DESC, keyword COLLATE NOCASE
                    """
                ).fetchall()
        return [self._row_to_command(row) for row in rows]

    def add(self, keyword: str, action: str) -> VoiceCommand:
        keyword = keyword.strip()
        action = action.strip()
        if not keyword:
            raise ValueError("Keyword is required")
        if action not in VALID_ACTIONS:
            raise ValueError(f"Invalid action: {action}")

        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO voice_commands (keyword, action, enabled, created_at)
                    VALUES (?, ?, 1, ?)
                    """,
                    (keyword, action, now),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM voice_commands WHERE id = ?",
                    (int(cursor.lastrowid),),
                ).fetchone()
        return self._row_to_command(row)

    def delete(self, command_id: int) -> bool:
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM voice_commands WHERE id = ?",
                    (command_id,),
                )
                conn.commit()
                return cursor.rowcount > 0

    def update(
        self,
        command_id: int,
        *,
        keyword: str | None = None,
        action: str | None = None,
        enabled: bool | None = None,
    ) -> VoiceCommand | None:
        fields: list[str] = []
        values: list[object] = []
        if keyword is not None:
            keyword = keyword.strip()
            if not keyword:
                raise ValueError("Keyword is required")
            fields.append("keyword = ?")
            values.append(keyword)
        if action is not None:
            action = action.strip()
            if action not in VALID_ACTIONS:
                raise ValueError(f"Invalid action: {action}")
            fields.append("action = ?")
            values.append(action)
        if enabled is not None:
            fields.append("enabled = ?")
            values.append(1 if enabled else 0)
        if not fields:
            return self.get(command_id)

        values.append(command_id)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    f"UPDATE voice_commands SET {', '.join(fields)} WHERE id = ?",
                    values,
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM voice_commands WHERE id = ?",
                    (command_id,),
                ).fetchone()
        if row is None:
            return None
        return self._row_to_command(row)

    def get(self, command_id: int) -> VoiceCommand | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM voice_commands WHERE id = ?",
                    (command_id,),
                ).fetchone()
        if row is None:
            return None
        return self._row_to_command(row)

    def match(self, text: str) -> VoiceCommand | None:
        haystack = text.strip().lower()
        if not haystack:
            return None
        for command in self.list_enabled():
            if command.keyword.lower() in haystack:
                return command
        return None

    @staticmethod
    def _row_to_command(row: sqlite3.Row) -> VoiceCommand:
        return VoiceCommand(
            id=row["id"],
            keyword=row["keyword"],
            action=row["action"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
        )

    def to_dict(self, command: VoiceCommand) -> dict[str, object]:
        return {
            "id": command.id,
            "keyword": command.keyword,
            "action": command.action,
            "enabled": command.enabled,
            "created_at": command.created_at,
        }
