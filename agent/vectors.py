"""Vector helpers: float32 BLOB packing and cosine similarity (numpy fallback)."""

from __future__ import annotations

import logging
import sqlite3

import numpy as np

logger = logging.getLogger(__name__)


def pack(vec: list[float] | np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def unpack(blob: bytes | None) -> np.ndarray | None:
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a is None or b is None or a.shape != b.shape:
        return 0.0
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def try_load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    """Load the sqlite-vec extension into conn. Returns False if unavailable."""
    try:
        import sqlite_vec  # type: ignore

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception as exc:  # pragma: no cover - depends on platform
        logger.debug("sqlite-vec unavailable, using numpy fallback: %s", exc)
        return False
