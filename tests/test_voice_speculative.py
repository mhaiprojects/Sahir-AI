"""Speculative voice path: no side effects, cancel, single persist on final."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from agent.config import AgentConfig
from agent.loop import Agent
from agent.speculative import (
    SpeculativeDraft,
    can_reuse_draft,
    commit_chat_draft,
    run_speculative,
)
from agent.chat_router import RouteDecision, STICKY_CONFIDENCE
from tests.conftest import FakeEmbedder, FakeLLM


@pytest.fixture
def agent_stack(tmp_path):
    from agent.store import AgentStore
    from config import Settings

    cfg = AgentConfig(db_path=tmp_path / "agent.db", auto_memory=False)
    store = AgentStore(cfg.db_path)
    conv = store.create_conversation("Test", mode="chat")
    agent = Agent(Settings(), cfg, store, FakeEmbedder(), FakeLLM(reply="Draft reply."))
    return agent, conv


def test_speculative_prewarm_no_tool_call(agent_stack, monkeypatch):
    agent, conv = agent_stack
    calls = {"route": 0, "cursor": 0, "chat": 0, "respond": 0}
    monkeypatch.setattr(agent, "retrieve", MagicMock(return_value=type("C", (), {"to_block": lambda self: ""})()))
    monkeypatch.setattr(
        "tools.router.chat_text_only_detailed",
        lambda *a, **k: (calls.__setitem__("chat", calls["chat"] + 1), type("R", (), {"reply": "x"})())[1],
    )
    monkeypatch.setattr(agent, "respond", lambda *a, **k: (calls.__setitem__("respond", calls["respond"] + 1), None)[1])

    gen = [1]

    def still_valid() -> bool:
        return True

    slot: list = []

    def on_draft(d: SpeculativeDraft) -> None:
        slot.append(d)

    store_conv = conv["id"]
    agent.store.set_conversation_mode(store_conv, "agent")
    run_speculative(agent, partial="hello world", conversation_id=store_conv, generation=1, still_valid=still_valid, on_draft=on_draft)
    assert calls["chat"] == 0
    assert calls["respond"] == 0
    assert slot == []
    agent.store.set_conversation_mode(store_conv, "chat")
    run_speculative(agent, partial="hello world again", conversation_id=store_conv, generation=1, still_valid=still_valid, on_draft=on_draft)
    assert calls["chat"] == 1
    assert len(slot) == 1


def test_cancel_stops_draft(agent_stack, monkeypatch):
    agent, conv = agent_stack
    agent.store.set_conversation_mode(conv["id"], "chat")

    def slow_chat(*a, **k):
        time.sleep(0.15)
        return type("R", (), {"reply": "late"})()

    monkeypatch.setattr("tools.router.chat_text_only_detailed", slow_chat)
    gen_ref = [1]
    slot: list = []

    def still_valid() -> bool:
        return gen_ref[0] == 1

    def on_draft(d: SpeculativeDraft) -> None:
        slot.append(d)

    t = threading.Thread(
        target=lambda: run_speculative(
            agent, partial="one two three four five", conversation_id=conv["id"],
            generation=1, still_valid=still_valid, on_draft=on_draft,
        ),
        daemon=True,
    )
    t.start()
    time.sleep(0.02)
    gen_ref[0] = 2
    t.join(timeout=1)
    assert slot == []


def test_draft_discarded_when_final_not_c(agent_stack):
    agent, conv = agent_stack
    draft = SpeculativeDraft(1, "one two three", "draft", conv["id"], "chat")
    dec = RouteDecision(label="D", tier=2, confidence=0.9, target_chat_id=99, utterance="summarise all")
    assert not can_reuse_draft(draft, generation_at_capture=1, decision=dec, final_text="summarise all", conversation_id=conv["id"])


def test_commit_once(agent_stack):
    agent, conv = agent_stack
    cid = conv["id"]
    before = len(agent.store.get_messages(cid))
    reply = commit_chat_draft(agent, conversation_id=cid, user_text="final text here", reply="Draft reply.")
    after = len(agent.store.get_messages(cid))
    assert after - before == 2
    assert reply.user_message_id and reply.assistant_message_id
    msgs = agent.store.get_messages(cid)
    assert sum(1 for m in msgs if m["role"] == "user") == 1
    assert sum(1 for m in msgs if m["role"] == "assistant") == 1


def test_reuse_when_extension_matches(agent_stack):
    agent, conv = agent_stack
    draft = SpeculativeDraft(1, "one two three four", "ok", conv["id"], "chat")
    dec = RouteDecision(
        label="C", tier=1, confidence=STICKY_CONFIDENCE, target_chat_id=conv["id"], utterance="one two three four five",
    )
    assert can_reuse_draft(
        draft, generation_at_capture=1, decision=dec, final_text="one two three four five", conversation_id=conv["id"],
    )


def test_speculative_then_final_single_message_pair(agent_stack, monkeypatch):
    """Stable partial draft + matching final route C => one user and one assistant only."""
    agent, conv = agent_stack
    cid = conv["id"]
    agent.store.set_conversation_mode(cid, "chat")
    execute = MagicMock()
    monkeypatch.setattr("agent.dispatcher.execute_route_decision", execute)
    partial = "one two three four five"
    final = f"{partial} six"
    slot: list[SpeculativeDraft] = []

    def on_draft(d: SpeculativeDraft) -> None:
        slot.append(d)

    run_speculative(
        agent,
        partial=partial,
        conversation_id=cid,
        generation=7,
        still_valid=lambda: True,
        on_draft=on_draft,
    )
    assert len(slot) == 1
    before = len(agent.store.get_messages(cid))
    dec = RouteDecision(
        label="C", tier=1, confidence=STICKY_CONFIDENCE, target_chat_id=cid, utterance=final,
    )
    draft_gen = 7
    assert can_reuse_draft(slot[0], generation_at_capture=draft_gen, decision=dec, final_text=final, conversation_id=cid)
    commit_chat_draft(agent, conversation_id=cid, user_text=final, reply=slot[0].reply)
    assert len(agent.store.get_messages(cid)) - before == 2
    execute.assert_not_called()


def test_speculative_no_db_writes(agent_stack, monkeypatch):
    agent, conv = agent_stack
    agent.store.set_conversation_mode(conv["id"], "chat")
    writes: list[str] = []

    def track_add(*a, **k):
        writes.append("add_message")
        raise AssertionError("speculative must not persist messages")

    monkeypatch.setattr(agent.store, "add_message", track_add)
    slot: list[SpeculativeDraft] = []
    run_speculative(
        agent,
        partial="alpha beta gamma delta epsilon",
        conversation_id=conv["id"],
        generation=1,
        still_valid=lambda: True,
        on_draft=slot.append,
    )
    assert writes == []
    assert len(slot) == 1