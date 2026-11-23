"""SQLite store for conversations, messages, memories, documents, and chunks.

Everything lives in one on-disk file (default ``data/agent.db``). Embeddings are stored
as float32 BLOBs. When the sqlite-vec extension loads, similarity search runs in SQL via
``vec_distance_cosine``; otherwise a numpy brute-force fallback is used.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from agent.migrations import STORE_SCHEMA_VERSION, migrate_store, schema_version
from agent.vectors import cosine, pack, try_load_sqlite_vec, unpack

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT 'New conversation',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'web'
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user','assistant','system','tool')),
    content TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages (conversation_id, id);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, content='messages', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'fact',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','pending','rejected')),
    source TEXT NOT NULL DEFAULT 'manual',
    source_conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
    embedding BLOB,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories (status);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    mime TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL UNIQUE,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    embed_model TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding BLOB
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks (document_id, idx);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ScoredChunk:
    chunk_id: int
    document_id: int
    filename: str
    idx: int
    content: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "filename": self.filename,
            "idx": self.idx,
            "content": self.content,
            "score": round(self.score, 4),
        }


class AgentStore:
    """Thread-safe SQLite store. One connection per call; a lock serialises writes."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.vector_backend = "numpy"
        default_mode = os.getenv("DEFAULT_CHAT_MODE", "agent").strip() or "agent"
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            migrate_store(conn, default_chat_mode=default_mode)
            conn.commit()
            conn.execute("PRAGMA journal_mode=WAL")
            conn.commit()
        with self._connect() as conn:
            if try_load_sqlite_vec(conn):
                self.vector_backend = "sqlite-vec"
        self._schema_version = STORE_SCHEMA_VERSION

    # ------------------------------------------------------------------ infra
    def _has_table(self, name: str) -> bool:
        rows = self._query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
        return bool(rows)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if getattr(self, "vector_backend", "numpy") == "sqlite-vec":
            try_load_sqlite_vec(conn)
        return conn

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(sql, tuple(params))
                conn.commit()
                return cur
            finally:
                conn.close()

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        conn = self._connect()
        try:
            return conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()

    def stats(self) -> dict[str, Any]:
        def count(sql: str) -> int:
            return int(self._query(sql)[0][0])

        proj = count("SELECT COUNT(*) FROM projects") if self._has_table("projects") else 0
        ver_rows = self._query("SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations") if self._has_table(
            "schema_migrations"
        ) else [{"v": 0}]
        return {
            "db_path": str(self.db_path),
            "vector_backend": self.vector_backend,
            "schema_version": int(ver_rows[0]["v"]) if ver_rows else 0,
            "projects": proj,
            "conversations": count("SELECT COUNT(*) FROM conversations"),
            "messages": count("SELECT COUNT(*) FROM messages"),
            "memories_active": count("SELECT COUNT(*) FROM memories WHERE status='active'"),
            "memories_pending": count("SELECT COUNT(*) FROM memories WHERE status='pending'"),
            "documents": count("SELECT COUNT(*) FROM documents"),
            "chunks": count("SELECT COUNT(*) FROM chunks"),
        }

    # --------------------------------------------------------- projects
    def list_projects(self, *, status: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM projects WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY id ASC"
        return [_project_dict(r) for r in self._query(sql, params)]

    def get_project(self, project_id: int) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM projects WHERE id=?", (project_id,))
        return _project_dict(rows[0]) if rows else None

    def get_project_by_slug(self, slug: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM projects WHERE slug=?", (slug,))
        return _project_dict(rows[0]) if rows else None

    def general_project_id(self) -> int | None:
        p = self.get_project_by_slug("general")
        return int(p["id"]) if p else None

    def create_project(self, name: str, description: str = "") -> dict[str, Any]:
        name = (name or "").strip()[:120]
        if not name:
            raise ValueError("Project name is required")
        base_slug = _slugify(name)
        slug = base_slug
        n = 2
        while self.get_project_by_slug(slug):
            slug = f"{base_slug}-{n}"[:80]
            n += 1
        now = utcnow()
        default_mode = os.getenv("DEFAULT_CHAT_MODE", "agent").strip() or "agent"
        cur = self._exec(
            """INSERT INTO conversations (
                title, created_at, updated_at, source, kind, status, brief, aliases, mode
            ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (f"{name} supervisor", now, now, "system", "supervisor", "active", "", "[]", "agent"),
        )
        sup_id = int(cur.lastrowid)
        settings = json.dumps({"default_mode": default_mode})
        cur = self._exec(
            """INSERT INTO projects (name, slug, description, status, supervisor_conversation_id, settings, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (name, slug, (description or "")[:2000], "active", sup_id, settings, now, now),
        )
        project_id = int(cur.lastrowid)
        self._exec("UPDATE conversations SET project_id=? WHERE id=?", (project_id, sup_id))
        proj = self.get_project(project_id)
        if proj is None:
            raise RuntimeError("Project insert failed")
        return proj

    # --------------------------------------------------------- conversations
    def create_conversation(
        self,
        title: str | None = None,
        source: str = "web",
        *,
        project_id: int | None = None,
        kind: str = "chat",
        parent_id: int | None = None,
        mode: str | None = None,
        brief: str = "",
        aliases: list[str] | None = None,
    ) -> dict[str, Any]:
        now = utcnow()
        project_id = project_id or self.general_project_id()
        if mode is None:
            mode = os.getenv("DEFAULT_CHAT_MODE", "agent").strip() or "agent"
        cur = self._exec(
            """INSERT INTO conversations (
                title, created_at, updated_at, source, project_id, kind, parent_id, status,
                mode, brief, aliases
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                (title or "New conversation").strip()[:200] or "New conversation",
                now,
                now,
                source,
                project_id,
                kind,
                parent_id,
                "active",
                mode,
                (brief or "")[:4000],
                json.dumps(aliases or [], ensure_ascii=False),
            ),
        )
        return self.get_conversation(int(cur.lastrowid))  # type: ignore[return-value]

    def get_conversation(self, conv_id: int) -> dict[str, Any] | None:
        rows = self._query(
            """SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS message_count,
                      p.name AS project_name, p.slug AS project_slug
               FROM conversations c
               LEFT JOIN projects p ON p.id = c.project_id
               WHERE c.id=?""",
            (conv_id,),
        )
        return _conversation_dict(rows[0]) if rows else None

    def get_conversation_mode(self, conv_id: int) -> str | None:
        rows = self._query("SELECT mode FROM conversations WHERE id=?", (conv_id,))
        if not rows:
            return None
        mode = rows[0]["mode"]
        return str(mode) if mode else None

    def set_conversation_mode(self, conv_id: int, mode: str) -> dict[str, Any] | None:
        mode = _normalize_stored_mode(mode)
        cur = self._exec(
            "UPDATE conversations SET mode=?, updated_at=? WHERE id=?",
            (mode, utcnow(), conv_id),
        )
        return self.get_conversation(conv_id) if cur.rowcount else None

    def add_context_injection(
        self,
        *,
        from_conversation_id: int | None,
        to_conversation_id: int,
        content: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        cur = self._exec(
            """INSERT INTO context_injections (from_conversation_id, to_conversation_id, content, reason, created_at)
               VALUES (?,?,?,?,?)""",
            (from_conversation_id, to_conversation_id, content[:8000], reason, utcnow()),
        )
        rows = self._query("SELECT * FROM context_injections WHERE id=?", (int(cur.lastrowid),))
        return dict(rows[0]) if rows else {}

    def list_conversations(
        self,
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
        *,
        project_id: int | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        base = """SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS message_count,
                  (SELECT content FROM messages m WHERE m.conversation_id=c.id ORDER BY m.id DESC LIMIT 1) AS last_message
                  FROM conversations c"""
        params: list[Any] = []
        conditions: list[str] = []
        if project_id is not None:
            conditions.append("c.project_id=?")
            params.append(project_id)
        if status:
            conditions.append("c.status=?")
            params.append(status)
        if q and q.strip():
            conditions.append("""(c.title LIKE ? OR c.id IN (
                        SELECT m.conversation_id FROM messages m JOIN messages_fts f ON f.rowid=m.id
                        WHERE messages_fts MATCH ?))""")
            params += [f"%{q.strip()}%", _fts_query(q)]
        if conditions:
            base += " WHERE " + " AND ".join(conditions)
        base += " ORDER BY c.updated_at DESC, c.id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        out = []
        for r in self._query(base, params):
            d = _conversation_dict(r, include_project=False)
            if d.get("last_message"):
                d["last_message"] = d["last_message"][:160]
            out.append(d)
        return out

    def rename_conversation(self, conv_id: int, title: str) -> dict[str, Any] | None:
        title = title.strip()[:200]
        if not title:
            raise ValueError("Title is empty")
        conv = self.get_conversation(conv_id)
        if conv and conv.get("kind") == "supervisor":
            raise ValueError("Supervisor conversations cannot be renamed")
        cur = self._exec("UPDATE conversations SET title=? WHERE id=?", (title, conv_id))
        return self.get_conversation(conv_id) if cur.rowcount else None

    def update_conversation_brief(self, conv_id: int, brief: str) -> dict[str, Any] | None:
        brief = (brief or "").strip()[:4000]
        cur = self._exec(
            "UPDATE conversations SET brief=?, updated_at=? WHERE id=?",
            (brief, utcnow(), conv_id),
        )
        return self.get_conversation(conv_id) if cur.rowcount else None

    def set_conversation_status(self, conv_id: int, status: str) -> dict[str, Any] | None:
        if status not in {"active", "idle", "archived"}:
            raise ValueError(f"Invalid status {status!r}")
        conv = self.get_conversation(conv_id)
        if conv and conv.get("kind") == "supervisor":
            raise ValueError("Supervisor conversation status is fixed")
        cur = self._exec(
            "UPDATE conversations SET status=?, updated_at=? WHERE id=?",
            (status, utcnow(), conv_id),
        )
        return self.get_conversation(conv_id) if cur.rowcount else None

    def set_conversation_aliases(self, conv_id: int, aliases: list[str]) -> dict[str, Any] | None:
        clean = [str(a).strip()[:80] for a in aliases if str(a).strip()][:20]
        cur = self._exec(
            "UPDATE conversations SET aliases=?, updated_at=? WHERE id=?",
            (json.dumps(clean, ensure_ascii=False), utcnow(), conv_id),
        )
        return self.get_conversation(conv_id) if cur.rowcount else None

    def move_conversation(self, conv_id: int, project_id: int) -> dict[str, Any]:
        conv = self.get_conversation(conv_id)
        if not conv:
            raise ValueError("Conversation not found")
        if conv.get("kind") == "supervisor":
            raise ValueError("Cannot move a supervisor conversation")
        proj = self.get_project(project_id)
        if not proj:
            raise ValueError("Project not found")
        parent_id = conv.get("parent_id")
        if conv.get("kind") == "sub":
            parent_id = proj.get("supervisor_conversation_id")
        elif parent_id and parent_id == conv.get("id"):
            parent_id = None
        self._exec(
            "UPDATE conversations SET project_id=?, parent_id=?, updated_at=? WHERE id=?",
            (project_id, parent_id, utcnow(), conv_id),
        )
        out = self.get_conversation(conv_id)
        if out is None:
            raise ValueError("Conversation not found")
        return out

    def list_sub_chats(
        self,
        project_id: int,
        *,
        include_archived: bool = False,
        supervisor_id: int | None = None,
    ) -> list[dict[str, Any]]:
        proj = self.get_project(project_id)
        if not proj:
            return []
        sup_id = supervisor_id or proj.get("supervisor_conversation_id")
        conditions = ["c.project_id=?", "c.kind IN ('sub', 'chat')", "c.id != ?"]
        params: list[Any] = [project_id, sup_id or -1]
        if not include_archived:
            conditions.append("c.status != 'archived'")
        sql = f"""SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS message_count
                  FROM conversations c WHERE {" AND ".join(conditions)}
                  ORDER BY c.updated_at DESC, c.id DESC"""
        return [_conversation_dict(r, include_project=False) for r in self._query(sql, params)]

    def update_project_settings(self, project_id: int, patch: dict[str, Any]) -> dict[str, Any]:
        proj = self.get_project(project_id)
        if not proj:
            raise ValueError("Project not found")
        settings = dict(proj.get("settings") or {})
        for key, val in patch.items():
            if val is None:
                settings.pop(key, None)
            else:
                settings[key] = val
        self._exec(
            "UPDATE projects SET settings=?, updated_at=? WHERE id=?",
            (json.dumps(settings, ensure_ascii=False), utcnow(), project_id),
        )
        out = self.get_project(project_id)
        if out is None:
            raise ValueError("Project not found")
        return out

    def cursor_workspace_for_conversation(self, conv_id: int, default: Any) -> Any:
        conv = self.get_conversation(conv_id)
        if not conv or not conv.get("project_id"):
            return default
        proj = self.get_project(int(conv["project_id"]))
        if not proj:
            return default
        wd = (proj.get("settings") or {}).get("cursor_workdir")
        if wd and str(wd).strip():
            from pathlib import Path

            p = Path(str(wd)).expanduser()
            return p if p.is_dir() else default
        return default

    def delete_conversation(self, conv_id: int) -> bool:
        conv = self.get_conversation(conv_id)
        if not conv:
            return False
        if conv.get("kind") == "supervisor":
            raise ValueError("Supervisor conversations cannot be deleted")
        return self._exec("DELETE FROM conversations WHERE id=?", (conv_id,)).rowcount > 0

    def touch(self, conv_id: int) -> None:
        self._exec("UPDATE conversations SET updated_at=? WHERE id=?", (utcnow(), conv_id))

    def latest_active_conversation(self, idle_minutes: int, source: str | None = None) -> dict[str, Any] | None:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=idle_minutes)).isoformat(timespec="seconds")
        sql = "SELECT id FROM conversations WHERE updated_at >= ?"
        params: list[Any] = [cutoff]
        if source:
            sql += " AND source=?"
            params.append(source)
        rows = self._query(sql + " ORDER BY updated_at DESC, id DESC LIMIT 1", params)
        return self.get_conversation(int(rows[0]["id"])) if rows else None

    # --------------------------------------------------------------- messages
    def add_message(self, conv_id: int, role: str, content: str, meta: dict[str, Any] | None = None) -> int:
        cur = self._exec(
            "INSERT INTO messages (conversation_id, role, content, meta, created_at) VALUES (?,?,?,?,?)",
            (conv_id, role, content, json.dumps(meta or {}, ensure_ascii=False), utcnow()),
        )
        self.touch(conv_id)
        return int(cur.lastrowid)

    def get_messages(self, conv_id: int, limit: int | None = None) -> list[dict[str, Any]]:
        if limit:
            rows = self._query(
                "SELECT * FROM (SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?) ORDER BY id",
                (conv_id, limit),
            )
        else:
            rows = self._query("SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conv_id,))
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["meta"] = json.loads(d.get("meta") or "{}")
            except json.JSONDecodeError:
                d["meta"] = {}
            out.append(d)
        return out

    def search_messages(self, q: str, limit: int = 50) -> list[dict[str, Any]]:
        if not q.strip():
            return []
        rows = self._query(
            """SELECT m.id, m.conversation_id, m.role, m.created_at, c.title,
                      snippet(messages_fts, 0, '[', ']', '…', 12) AS snippet
               FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid
               JOIN conversations c ON c.id = m.conversation_id
               WHERE messages_fts MATCH ? ORDER BY rank LIMIT ?""",
            (_fts_query(q), limit),
        )
        return [dict(r) for r in rows]

    # --------------------------------------------------------------- memories
    def add_memory(
        self,
        content: str,
        *,
        category: str = "fact",
        status: str = "active",
        source: str = "manual",
        source_conversation_id: int | None = None,
        project_id: int | None = None,
        embedding: list[float] | np.ndarray | None = None,
    ) -> dict[str, Any]:
        content = content.strip()
        if not content:
            raise ValueError("Memory content is empty")
        if status not in {"active", "pending", "rejected"}:
            raise ValueError(f"Invalid status {status!r}")
        now = utcnow()
        cur = self._exec(
            """INSERT INTO memories (content, category, status, source, source_conversation_id, project_id, embedding, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (content[:2000], (category or "fact")[:40], status, source, source_conversation_id, project_id,
             pack(embedding) if embedding is not None else None, now, now),
        )
        return self.get_memory(int(cur.lastrowid))  # type: ignore[return-value]

    def get_memory(self, mem_id: int) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM memories WHERE id=?", (mem_id,))
        return _memory_dict(rows[0]) if rows else None

    def list_memories(self, status: str | None = None, q: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM memories WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status=?"
            params.append(status)
        if q and q.strip():
            sql += " AND content LIKE ?"
            params.append(f"%{q.strip()}%")
        sql += " ORDER BY updated_at DESC, id DESC"
        return [_memory_dict(r) for r in self._query(sql, params)]

    def update_memory(
        self,
        mem_id: int,
        *,
        content: str | None = None,
        category: str | None = None,
        status: str | None = None,
        embedding: list[float] | np.ndarray | None = None,
    ) -> dict[str, Any] | None:
        sets, params = [], []
        if content is not None:
            if not content.strip():
                raise ValueError("Memory content is empty")
            sets.append("content=?")
            params.append(content.strip()[:2000])
        if category is not None:
            sets.append("category=?")
            params.append(category[:40] or "fact")
        if status is not None:
            if status not in {"active", "pending", "rejected"}:
                raise ValueError(f"Invalid status {status!r}")
            sets.append("status=?")
            params.append(status)
        if embedding is not None:
            sets.append("embedding=?")
            params.append(pack(embedding))
        if not sets:
            return self.get_memory(mem_id)
        sets.append("updated_at=?")
        params.append(utcnow())
        params.append(mem_id)
        cur = self._exec(f"UPDATE memories SET {', '.join(sets)} WHERE id=?", params)
        return self.get_memory(mem_id) if cur.rowcount else None

    def delete_memory(self, mem_id: int) -> bool:
        return self._exec("DELETE FROM memories WHERE id=?", (mem_id,)).rowcount > 0

    def search_memories(
        self,
        query_vec: list[float] | np.ndarray,
        k: int = 5,
        statuses: tuple[str, ...] = ("active",),
        *,
        project_id: int | None = None,
    ) -> list[tuple[dict[str, Any], float]]:
        placeholders = ",".join("?" for _ in statuses)
        scope_sql = ""
        scope_params: list[Any] = []
        if project_id is not None:
            scope_sql = " AND (project_id IS NULL OR project_id=?)"
            scope_params.append(project_id)
        q = np.asarray(query_vec, dtype=np.float32)
        if self.vector_backend == "sqlite-vec":
            rows = self._query(
                f"""SELECT *, 1 - vec_distance_cosine(embedding, ?) AS score FROM memories
                    WHERE embedding IS NOT NULL AND length(embedding)=? AND status IN ({placeholders}){scope_sql}
                    ORDER BY score DESC LIMIT ?""",
                (pack(q), q.nbytes, *statuses, *scope_params, k),
            )
            return [(_memory_dict(r), float(r["score"])) for r in rows]
        rows = self._query(
            f"SELECT * FROM memories WHERE embedding IS NOT NULL AND status IN ({placeholders}){scope_sql}",
            (*statuses, *scope_params),
        )
        scored = [(_memory_dict(r), cosine(q, unpack(r["embedding"]))) for r in rows]
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:k]

    # -------------------------------------------------------------- documents
    def find_document_by_hash(self, sha256: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM documents WHERE sha256=?", (sha256,))
        return dict(rows[0]) if rows else None

    def add_document(
        self,
        *,
        filename: str,
        mime: str | None,
        size_bytes: int,
        sha256: str,
        chunks: list[str],
        embeddings: list[list[float]] | None,
        embed_model: str | None,
        project_id: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """INSERT INTO documents (filename, mime, size_bytes, sha256, chunk_count, embed_model,
                       project_id, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (filename, mime, size_bytes, sha256, len(chunks), embed_model, project_id, utcnow()),
                )
                doc_id = int(cur.lastrowid)
                conn.executemany(
                    "INSERT INTO chunks (document_id, idx, content, embedding) VALUES (?,?,?,?)",
                    [
                        (doc_id, i, text, pack(embeddings[i]) if embeddings else None)
                        for i, text in enumerate(chunks)
                    ],
                )
                conn.commit()
            finally:
                conn.close()
        return self.get_document(doc_id)  # type: ignore[return-value]

    def get_document(self, doc_id: int) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM documents WHERE id=?", (doc_id,))
        return dict(rows[0]) if rows else None

    def list_documents(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._query("SELECT * FROM documents ORDER BY id DESC")]

    def get_chunks(self, doc_id: int, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT id, document_id, idx, content FROM chunks WHERE document_id=? ORDER BY idx LIMIT ?",
            (doc_id, limit),
        )
        return [dict(r) for r in rows]

    def delete_document(self, doc_id: int) -> bool:
        return self._exec("DELETE FROM documents WHERE id=?", (doc_id,)).rowcount > 0

    def search_chunks(
        self, query_vec: list[float] | np.ndarray, k: int = 4, *, project_id: int | None = None,
    ) -> list[ScoredChunk]:
        q = np.asarray(query_vec, dtype=np.float32)
        scope_sql = ""
        scope_params: list[Any] = []
        if project_id is not None:
            scope_sql = " AND (d.project_id IS NULL OR d.project_id=?)"
            scope_params.append(project_id)
        if self.vector_backend == "sqlite-vec":
            rows = self._query(
                f"""SELECT ch.id, ch.document_id, ch.idx, ch.content, d.filename,
                          1 - vec_distance_cosine(ch.embedding, ?) AS score
                   FROM chunks ch JOIN documents d ON d.id = ch.document_id
                   WHERE ch.embedding IS NOT NULL AND length(ch.embedding)=?{scope_sql}
                   ORDER BY score DESC LIMIT ?""",
                (pack(q), q.nbytes, *scope_params, k),
            )
            return [
                ScoredChunk(r["id"], r["document_id"], r["filename"], r["idx"], r["content"], float(r["score"]))
                for r in rows
            ]
        rows = self._query(
            f"""SELECT ch.id, ch.document_id, ch.idx, ch.content, ch.embedding, d.filename, d.project_id
               FROM chunks ch JOIN documents d ON d.id = ch.document_id
               WHERE ch.embedding IS NOT NULL{scope_sql}""",
            tuple(scope_params),
        )
        scored = [
            ScoredChunk(r["id"], r["document_id"], r["filename"], r["idx"], r["content"], cosine(q, unpack(r["embedding"])))
            for r in rows
        ]
        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:k]

    # ------------------------------------------------------------ email drafts
    def save_email_draft(
        self,
        *,
        to_addrs: list[str],
        cc_addrs: list[str],
        subject: str,
        body: str,
        reply_to_uid: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cur = self._exec(
            """INSERT INTO email_drafts (created_at, updated_at, to_addrs, cc_addrs, subject, body, reply_to_uid, meta)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                now,
                now,
                json.dumps(to_addrs),
                json.dumps(cc_addrs),
                subject[:500],
                body[:50000],
                reply_to_uid,
                json.dumps(meta or {}),
            ),
        )
        return int(cur.lastrowid)

    def get_email_draft(self, draft_id: int) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM email_drafts WHERE id=?", (draft_id,))
        return dict(rows[0]) if rows else None

    def delete_email_draft(self, draft_id: int) -> bool:
        return self._exec("DELETE FROM email_drafts WHERE id=?", (draft_id,)).rowcount > 0

    def set_message_content(self, message_id: int, content: str) -> bool:
        content = content[:50000]
        row = self._query("SELECT id, content FROM messages WHERE id=?", (message_id,))
        if not row:
            return False
        updated = self._exec("UPDATE messages SET content=? WHERE id=?", (content, message_id)).rowcount > 0
        if updated:
            self._reindex_message_fts(message_id, old_content=row[0]["content"], new_content=content)
        return updated > 0

    def _reindex_message_fts(self, message_id: int, *, old_content: str, new_content: str) -> None:
        """Keep messages_fts in sync when content is updated outside a full row replace."""
        self._exec(
            "INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', ?, ?)",
            (message_id, old_content),
        )
        self._exec(
            "INSERT INTO messages_fts(rowid, content) VALUES (?, ?)",
            (message_id, new_content),
        )

    def last_user_message_id(self, conversation_id: int) -> int | None:
        rows = self._query(
            "SELECT id FROM messages WHERE conversation_id=? AND role='user' ORDER BY id DESC LIMIT 1",
            (conversation_id,),
        )
        return int(rows[0]["id"]) if rows else None


VALID_CHAT_MODES = frozenset({"agent", "route", "chat", "cursor", "tools", "stream"})


def _normalize_stored_mode(mode: str) -> str:
    m = (mode or "agent").strip().lower()
    if m == "stream":
        return "chat"
    if m == "tools":
        return "route"
    if m not in {"agent", "route", "chat", "cursor"}:
        raise ValueError(f"Invalid mode {mode!r}")
    return m


def _conversation_dict(row: sqlite3.Row, *, include_project: bool = True) -> dict[str, Any]:
    d = dict(row)
    try:
        aliases = json.loads(d.get("aliases") or "[]")
        d["aliases"] = aliases if isinstance(aliases, list) else []
    except json.JSONDecodeError:
        d["aliases"] = []
    if not include_project:
        d.pop("project_name", None)
        d.pop("project_slug", None)
    return d


def _project_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    try:
        d["settings"] = json.loads(d.get("settings") or "{}")
    except json.JSONDecodeError:
        d["settings"] = {}
    return d


def _memory_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d.pop("embedding", None)
    d.pop("score", None)
    d["has_embedding"] = row["embedding"] is not None
    return d


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s[:60] if s else "project")


def _fts_query(q: str) -> str:
    """Turn free text into a safe FTS5 query (prefix match on each token)."""
    tokens = [t for t in "".join(ch if ch.isalnum() else " " for ch in q).split() if t]
    if not tokens:
        return '""'
    return " ".join(f'"{t}"*' for t in tokens[:12])
