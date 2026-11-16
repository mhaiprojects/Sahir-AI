"""Agent-layer configuration (env-driven, independent of the voice Settings)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class AgentConfig:
    db_path: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "agent.db")
    embed_model: str = "nomic-embed-text"
    history_turns: int = 12
    memory_top_k: int = 5
    rag_top_k: int = 4
    rag_min_score: float = 0.35
    memory_min_score: float = 0.30
    chunk_size: int = 900
    chunk_overlap: int = 150
    max_upload_mb: int = 25
    auto_memory: bool = True
    memory_dedup_threshold: float = 0.92
    session_idle_minutes: int = 30

    @classmethod
    def from_env(cls) -> "AgentConfig":
        return cls(
            db_path=Path(os.getenv("AGENT_DB_PATH") or PROJECT_ROOT / "data" / "agent.db"),
            embed_model=os.getenv("EMBED_MODEL", "nomic-embed-text"),
            history_turns=int(os.getenv("AGENT_HISTORY_TURNS", "12")),
            memory_top_k=int(os.getenv("MEMORY_TOP_K", "5")),
            rag_top_k=int(os.getenv("RAG_TOP_K", "4")),
            rag_min_score=float(os.getenv("RAG_MIN_SCORE", "0.35")),
            memory_min_score=float(os.getenv("MEMORY_MIN_SCORE", "0.30")),
            chunk_size=int(os.getenv("RAG_CHUNK_SIZE", "900")),
            chunk_overlap=int(os.getenv("RAG_CHUNK_OVERLAP", "150")),
            max_upload_mb=int(os.getenv("RAG_MAX_UPLOAD_MB", "25")),
            auto_memory=_bool("AGENT_AUTO_MEMORY", True),
            memory_dedup_threshold=float(os.getenv("MEMORY_DEDUP_THRESHOLD", "0.92")),
            session_idle_minutes=int(os.getenv("AGENT_SESSION_IDLE_MINUTES", "30")),
        )
