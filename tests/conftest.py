"""Shared fixtures: deterministic fake embedder/LLM so tests run offline (no Ollama needed)."""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.config import AgentConfig  # noqa: E402
from agent.loop import Agent  # noqa: E402
from agent.store import AgentStore  # noqa: E402
from config import Settings  # noqa: E402

DIM = 64


def _vec(text: str) -> list[float]:
    v = np.zeros(DIM, dtype=np.float32)
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        if tok in {"search", "query", "document", "the", "a", "is", "of", "what", "my", "user"}:
            continue
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        v[h % DIM] += 1.0
    n = np.linalg.norm(v)
    return (v / n if n else v).tolist()


class FakeEmbedder:
    model = "fake-embed"

    def __init__(self) -> None:
        self.calls = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [_vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        return _vec(text)


class FakeLLM:
    model = "fake-llm"

    def __init__(self, reply: str = "Fake reply.", json_reply: str | None = None) -> None:
        self.reply = reply
        self.json_reply = json_reply or '{"memories": [{"content": "User likes green tea.", "category": "preference"}]}'
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append(messages)
        if kwargs.get("format") == "json":
            return {"content": self.json_reply}
        return {"content": self.reply}

    def chat_stream(self, messages: list[dict[str, str]], **kwargs: Any) -> Iterator[str]:
        self.calls.append(messages)
        for word in self.reply.split(" "):
            yield word + " "


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.getenv("SAHIR_RUN_FLAKY") == "1":
        return
    skip = pytest.mark.skip(
        reason="Flaky TTS-audio ML test excluded by default; set SAHIR_RUN_FLAKY=1 to run",
    )
    for item in items:
        if "flaky_kws" in item.keywords or "flaky_tts_audio" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _disable_web_auth_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most tests expect an open API; auth tests opt in explicitly."""
    monkeypatch.setenv("WEB_AUTH_ENABLED", "false")


@pytest.fixture
def store(tmp_path: Path) -> AgentStore:
    return AgentStore(tmp_path / "agent.db")


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def agent(tmp_path: Path, store: AgentStore, embedder: FakeEmbedder, llm: FakeLLM) -> Agent:
    cfg = AgentConfig(db_path=tmp_path / "agent.db", auto_memory=False, rag_min_score=0.1, memory_min_score=0.1)
    return Agent(Settings(), cfg, store, embedder, llm)
