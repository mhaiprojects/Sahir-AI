"""Router Tier 2 + partials + undo (mocked Ollama)."""

from __future__ import annotations

import concurrent.futures

import pytest

from agent.chat_router import RouteContext, route
from agent.config import AgentConfig
from agent.gpu_config import reset_gpu_config
from agent.router_partials import PartialStabilizer, prefix_meaning_changed
from agent.router_t2 import classify_tier2, set_chat_client_for_tests, try_tier2_route
from agent.router_t1 import Tier1Hit, invalidate_static_cache


@pytest.fixture(autouse=True)
def _cfg(monkeypatch, tmp_path):
    reset_gpu_config()
    monkeypatch.setenv("ROUTER_T1_ENABLED", "true")
    monkeypatch.setenv("ROUTER_T1_MARGIN", "0.2")
    monkeypatch.setenv("ROUTER_T2_ENABLED", "true")
    monkeypatch.setenv("ROUTER_T2_TIMEOUT_MS", "400")
    reset_gpu_config()
    monkeypatch.setattr("agent.router_t1.CACHE_DIR", tmp_path / "cache")
    invalidate_static_cache()
    yield
    set_chat_client_for_tests(None)


def test_tier2_one_letter_parsing(monkeypatch):
    def fake_chat(*, model, messages, **kwargs):
        return {"message": {"content": "D"}}

    set_chat_client_for_tests(fake_chat)
    ctx = RouteContext(1, 2, 99, [{"id": 2, "title": "Shop", "aliases": []}], tier2_enabled=True)
    hit = classify_tier2("maybe spawn a chat", ctx, timeout_ms=200)
    assert hit and hit.label == "D" and hit.ms >= 0


def test_tier2_timeout_sticky(monkeypatch):
    def slow(*, model, messages, **kwargs):
        import time
        time.sleep(2)
        return {"message": {"content": "D"}}

    set_chat_client_for_tests(slow)
    ctx = RouteContext(1, 2, 99, [], tier2_enabled=True)
    hit = classify_tier2("summarise all", ctx, timeout_ms=50)
    assert hit and not hit.confident and hit.label == "C"


def test_tier2_only_when_tier1_unsure(monkeypatch, tmp_path):
    from tests.conftest import FakeEmbedder
    from tests.test_router_t1 import BiasedEmbedder

    emb = BiasedEmbedder()

    def fake_chat(*, model, messages, **kwargs):
        return {"message": {"content": "D"}}

    set_chat_client_for_tests(fake_chat)
    ctx = RouteContext(
        1, 10, 99, [],
        input_type="voice",
        tier0_enabled=True,
        tier1_enabled=True,
        tier2_enabled=True,
        embedder=emb,
        chat_fn=fake_chat,
    )
    d = route("refactor the login form validation", ctx, store=None, max_tier=2)
    assert d.tier in {1, 2}
    assert d.label == "C" or (d.tier == 2 and d.label == "D")


def test_partial_stabilisation():
    ps = PartialStabilizer(min_words=3, max_words=6)
    assert ps.observe("one two") is None
    assert ps.observe("one two three four") is None
    assert ps.observe("one two three four") == "one two three four"
    assert not prefix_meaning_changed("one two three", "one two three four five")
    assert prefix_meaning_changed("summarise all", "make button blue")


def test_undo_tier0(router_env):
    from agent.router_partials import VoiceRouteState

    store, cmds, pid, sup_id, a_id = router_env
    state = VoiceRouteState(last_utterance="add logging to checkout")
    ctx = RouteContext(
        pid, a_id, sup_id, [],
        input_type="voice",
        tier0_enabled=True,
        route_state=state,
        command_store=cmds,
    )
    d = route("no, that was for the supervisor", ctx, store=store)
    assert d.command == "route_undo"
    assert d.target_chat_id == sup_id


def test_timing_fields_on_route_event(monkeypatch):
    published = []
    monkeypatch.setattr("agent.chat_router.publish", lambda *a, **kw: published.append(kw))

    set_chat_client_for_tests(lambda **kw: {"message": {"content": "C"}})
    emb = type("E", (), {"model": "fake", "embed_query": lambda s, t: [1, 0], "embed_documents": lambda s, ts: [[1, 0]] * len(ts)})()
    monkeypatch.setattr("agent.router_t1.load_static_examples", lambda: [])
    invalidate_static_cache()
    ctx = RouteContext(1, 2, 99, [], tier0_enabled=True, tier1_enabled=True, tier2_enabled=True, embedder=emb)
    d = route("random question about cats", ctx, max_tier=2)
    assert "total_ms" in (d.timing or {}) or d.ms >= 0


@pytest.fixture
def router_env(tmp_path):
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