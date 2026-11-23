"""Router Tier 1: fake embedder, threshold, cache, sticky fallback."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from agent.chat_router import RouteContext, route
from agent.gpu_config import get_gpu_config, reset_gpu_config
from agent.router_t1 import (
    RouterExample,
    RouterTier1Index,
    _hash_examples,
    invalidate_static_cache,
    try_tier1_route,
)


class BiasedEmbedder:
    """Deterministic vectors: dimension 0 = D, 1 = C, 2 = S."""

    model = "fake-t1"

    def embed_query(self, text: str) -> list[float]:
        return self._one(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        v = np.zeros(8, dtype=np.float32)
        low = text.lower()
        if any(k in low for k in ("summarise", "supervisor", "spawn", "status across", "what chats", "project", "panorama")):
            v[0] = 1.0
        if any(k in low for k in ("test", "button", "weather", "refactor", "explain", "fix")):
            v[1] = 1.0
        if any(k in low for k in ("stop", "pause", "switch", "new chat", "cancel")):
            v[2] = 1.0
        if np.linalg.norm(v) == 0:
            v[3] = 0.1
        v /= np.linalg.norm(v)
        return v.tolist()


@pytest.fixture(autouse=True)
def _reset_router_t1(monkeypatch, tmp_path):
    reset_gpu_config()
    monkeypatch.setenv("ROUTER_T1_ENABLED", "true")
    monkeypatch.setenv("ROUTER_T1_MARGIN", "0.15")
    reset_gpu_config()
    monkeypatch.setattr("agent.router_t1.CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr("agent.router_t1.EXAMPLES_PATH", Path(__file__).resolve().parent.parent / "agent/router_examples.json")
    invalidate_static_cache()
    yield
    invalidate_static_cache()


def test_margin_threshold_supervisor_route(monkeypatch):
    emb = BiasedEmbedder()
    idx = RouterTier1Index()
    idx.ensure_static(emb)
    hit, embed_ms = idx.classify("summarise everything in the project", emb)
    assert hit is not None and hit.label == "D"
    assert hit.margin > 0.1
    assert embed_ms >= 0

    ctx = RouteContext(
        active_project_id=1,
        active_chat_id=10,
        supervisor_chat_id=99,
        chats=[],
        tier1_enabled=True,
        tier0_enabled=True,
        embedder=emb,
    )
    d = try_tier1_route("summarise all chats please", ctx, store=None, embedder=emb, t0=__import__("time").perf_counter())
    assert d is not None
    assert d.tier == 1 and d.label == "D"
    assert d.target_chat_id == 99
    assert d.ms >= 0


def test_sticky_fallback_when_margin_low(monkeypatch):
    monkeypatch.setenv("ROUTER_T1_MARGIN", "0.95")
    reset_gpu_config()
    emb = BiasedEmbedder()
    ctx = RouteContext(
        active_project_id=1,
        active_chat_id=10,
        supervisor_chat_id=99,
        chats=[],
        tier1_enabled=True,
        tier0_enabled=True,
        embedder=emb,
    )
    d = try_tier1_route("ambiguous utterance with no hints", ctx, store=None, embedder=emb, t0=__import__("time").perf_counter())
    assert d is not None
    assert d.label == "C" and d.tier == 1
    assert d.payload.get("sticky") is True


def test_cache_roundtrip(tmp_path, monkeypatch):
    emb = BiasedEmbedder()
    static = [
        RouterExample("summarise project", "D"),
        RouterExample("make button blue", "C"),
    ]
    ex_hash = _hash_examples(static)
    monkeypatch.setattr(
        "agent.router_t1.load_static_examples",
        lambda: static,
    )
    idx = RouterTier1Index()
    idx.ensure_static(emb)
    cache_files = list((tmp_path / "cache").glob("router_t1_cache_*.json"))
    assert cache_files
    data = json.loads(cache_files[0].read_text())
    assert data["examples_hash"] == ex_hash
    assert len(data["rows"]) == 2

    idx2 = RouterTier1Index()
    idx2.ensure_static(emb)
    assert len(idx2._static) == 2
    assert idx2._embedder is not None


@pytest.fixture
def router_env(tmp_path):
    from agent.config import AgentConfig
    from agent.store import AgentStore
    from web.commands import VoiceCommandStore

    db = tmp_path / "agent.db"
    store = AgentStore(db)
    proj = store.get_project_by_slug("general")
    pid = int(proj["id"])
    sup_id = int(proj["supervisor_conversation_id"])
    a = store.create_conversation("Groceries", project_id=pid, kind="sub", parent_id=sup_id)
    cmds = VoiceCommandStore(tmp_path / "cmds.db")
    return store, cmds, pid, sup_id, int(a["id"])


def test_route_integration_tier1_after_tier0_miss(router_env):
    store, cmds, _pid, sup_id, a_id = router_env
    emb = BiasedEmbedder()
    chats = [{"id": a_id, "title": "Groceries", "aliases": []}]
    ctx = RouteContext(
        active_project_id=int(store.get_project_by_slug("general")["id"]),
        active_chat_id=a_id,
        supervisor_chat_id=sup_id,
        chats=chats,
        input_type="voice",
        command_store=cmds,
        tier0_enabled=True,
        tier1_enabled=True,
        embedder=emb,
    )
    d = route("panorama of current work across the team", ctx, store=store)
    assert d.tier == 1
    assert d.label == "D"
    assert d.target_chat_id == sup_id


def test_tier1_disabled_skips_embedder(router_env):  # noqa: F811
    store, cmds, _pid, sup_id, a_id = router_env
    emb = BiasedEmbedder()
    ctx = RouteContext(
        active_project_id=int(store.get_project_by_slug("general")["id"]),
        active_chat_id=a_id,
        supervisor_chat_id=sup_id,
        chats=[],
        input_type="voice",
        command_store=cmds,
        tier0_enabled=True,
        tier1_enabled=False,
        embedder=emb,
    )
    d = route("refactor the login handler to use async", ctx, store=store)
    assert d.tier == 0 and d.payload.get("sticky")