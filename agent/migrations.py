"""Additive SQLite migrations for Projects / supervisor schema (architecture §3)."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

logger = logging.getLogger(__name__)

STORE_SCHEMA_VERSION = 5
GENERAL_SLUG = "general"
GENERAL_NAME = "General"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _current_version(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "schema_migrations"):
        return 0
    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
    return int(row[0]) if row else 0


def _mark_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (version, utcnow()),
    )


def migrate_store(conn: sqlite3.Connection, *, default_chat_mode: str = "agent") -> int:
    """Apply migrations idempotently. Returns ``STORE_SCHEMA_VERSION``."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER NOT NULL UNIQUE,
            applied_at TEXT NOT NULL
        )"""
    )

    # --- v1: projects + context_injections
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
            supervisor_conversation_id INTEGER REFERENCES conversations(id),
            settings TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS context_injections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_conversation_id INTEGER REFERENCES conversations(id),
            to_conversation_id INTEGER REFERENCES conversations(id),
            content TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    _mark_version(conn, 1)

    # --- v2: conversation hierarchy / per-chat mode
    for col, decl in (
        ("project_id", "INTEGER REFERENCES projects(id)"),
        ("kind", "TEXT NOT NULL DEFAULT 'chat'"),
        ("parent_id", "INTEGER REFERENCES conversations(id)"),
        ("status", "TEXT NOT NULL DEFAULT 'active'"),
        ("mode", "TEXT"),
        ("brief", "TEXT NOT NULL DEFAULT ''"),
        ("aliases", "TEXT NOT NULL DEFAULT '[]'"),
    ):
        _add_column(conn, "conversations", col, decl)
    conn.execute(
        "UPDATE conversations SET kind = 'chat' WHERE kind IS NULL OR kind = ''"
    )
    conn.execute(
        "UPDATE conversations SET status = 'active' WHERE status IS NULL OR status = ''"
    )
    conn.execute(
        "UPDATE conversations SET brief = '' WHERE brief IS NULL"
    )
    conn.execute(
        "UPDATE conversations SET aliases = '[]' WHERE aliases IS NULL OR aliases = ''"
    )
    _mark_version(conn, 2)

    # --- v3: scoped memories / documents / tasks
    _add_column(conn, "memories", "project_id", "INTEGER REFERENCES projects(id)")
    _add_column(conn, "documents", "project_id", "INTEGER REFERENCES projects(id)")
    if _table_exists(conn, "tasks"):
        _add_column(conn, "tasks", "conversation_id", "INTEGER")
    _mark_version(conn, 3)

    # --- v4: built-in General project + backfill
    _ensure_general_project(conn, default_chat_mode=default_chat_mode)
    _mark_version(conn, 4)

    # --- v5: local email drafts (integration item 25)
    if _current_version(conn) < 5:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS email_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                to_addrs TEXT NOT NULL DEFAULT '[]',
                cc_addrs TEXT NOT NULL DEFAULT '[]',
                subject TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                reply_to_uid TEXT,
                meta TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        _mark_version(conn, 5)

    return STORE_SCHEMA_VERSION


def _ensure_general_project(conn: sqlite3.Connection, *, default_chat_mode: str) -> None:
    row = conn.execute("SELECT id, supervisor_conversation_id FROM projects WHERE slug=?", (GENERAL_SLUG,)).fetchone()
    if row:
        general_id = int(row[0])
        sup_id = row[1]
    else:
        now = utcnow()
        settings = json.dumps({"default_mode": default_chat_mode})
        cur = conn.execute(
            """INSERT INTO conversations (title, created_at, updated_at, source, kind, status, brief, aliases, mode)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                f"{GENERAL_NAME} supervisor",
                now,
                now,
                "system",
                "supervisor",
                "active",
                "",
                "[]",
                "agent",
            ),
        )
        sup_id = int(cur.lastrowid)
        cur = conn.execute(
            """INSERT INTO projects (name, slug, description, status, supervisor_conversation_id, settings, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (GENERAL_NAME, GENERAL_SLUG, "Built-in inbox project", "active", sup_id, settings, now, now),
        )
        general_id = int(cur.lastrowid)
        conn.execute("UPDATE conversations SET project_id=? WHERE id=?", (general_id, sup_id))
        logger.info("Created built-in project %r (id=%s, supervisor=%s)", GENERAL_NAME, general_id, sup_id)

    conn.execute(
        """UPDATE conversations SET project_id=?, kind='chat'
           WHERE (project_id IS NULL) AND id != ? AND (kind IS NULL OR kind IN ('chat', ''))""",
        (general_id, sup_id or -1),
    )
    # Existing rows may have kind default 'chat' from ALTER; supervisor stays supervisor.
    if sup_id:
        conn.execute("UPDATE conversations SET kind='supervisor', project_id=? WHERE id=?", (general_id, sup_id))


def schema_version(conn: sqlite3.Connection) -> int:
    return _current_version(conn)