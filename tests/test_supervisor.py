"""Supervisor tools, scoped recall, injection, and project Cursor workdir."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from activity import bus
from agent.loop import Agent
from agent.config import AgentConfig
from agent.store import AgentStore
from agent.supervisor import SupervisorService
from agent.supervisor_intent import is_roster_question, parse_directive, normalize_tool
from config import Settings
from tests.conftest import FakeEmbedder, FakeLLM
from agent import loop as agent_loop


@pytest.fixture
def supervisor_stack(tmp_path: Path, store: AgentStore, embedder: FakeEmbedder) -> tuple[SupervisorService, Agent]:
    llm = FakeLLM()
    settings = Settings()
    agent = Agent(settings, AgentConfig(db_path=tmp_path / "agent.db", auto_memory=False), store, embedder, llm)
    svc = SupervisorService(store, llm, settings)
    agent.supervisor = svc
    return svc, agent


def test_spawn_brief_rename_archive_move(supervisor_stack: tuple[SupervisorService, Agent]) -> None:
    svc, _ = supervisor_stack
    general = svc.store.get_project_by_slug("general")
    assert general
    pid = int(general["id"])
    sub = svc.spawn_sub_chat(pid, title="API refactor", brief="Track auth migration", aliases=["api"])
    assert sub["kind"] == "sub"
    assert sub["brief"] == "Track auth migration"
    svc.update_brief(sub["id"], "Updated brief")
    renamed = svc.rename_sub_chat(sub["id"], "API v2")
    assert renamed["title"] == "API v2"
    archived = svc.archive_sub_chat(sub["id"])
    assert archived["status"] == "archived"
    restored = svc.unarchive_sub_chat(sub["id"])
    assert restored["status"] == "active"
    proj2 = svc.store.create_project("Side project")
    moved = svc.move_sub_chat(sub["id"], int(proj2["id"]))
    assert moved["project_id"] == proj2["id"]


def test_supervisor_delete_still_blocked(store: AgentStore) -> None:
    general = store.get_project_by_slug("general")
    sup_id = int(general["supervisor_conversation_id"])
    with pytest.raises(ValueError, match="Supervisor"):
        store.delete_conversation(sup_id)


def test_scoped_recall_excludes_other_project(store: AgentStore, embedder: FakeEmbedder) -> None:
    g = store.get_project_by_slug("general")
    p2 = store.create_project("Other")
    v = embedder.embed_query("widget color preference")
    store.add_memory("global fact", embedding=v, project_id=None)
    store.add_memory("general only", embedding=v, project_id=int(g["id"]))
    store.add_memory("other project secret", embedding=v, project_id=int(p2["id"]))
    scoped = store.search_memories(v, k=10, project_id=int(g["id"]))
    contents = {m["content"] for m, _ in scoped}
    assert "global fact" in contents
    assert "general only" in contents
    assert "other project secret" not in contents


def test_injection_recorded_and_visible(supervisor_stack: tuple[SupervisorService, Agent]) -> None:
    svc, _ = supervisor_stack
    general = svc.store.get_project_by_slug("general")
    pid = int(general["id"])
    sub = svc.spawn_sub_chat(pid, title="Research", brief="")
    sup_id = int(general["supervisor_conversation_id"])
    res = svc.inject_context(sub["id"], "Auth uses OAuth2 with PKCE.", from_conversation_id=sup_id, reason="test")
    assert res["message_id"]
    msgs = svc.store.get_messages(sub["id"])
    inj = [m for m in msgs if m["role"] == "system" and m.get("meta", {}).get("injection")]
    assert len(inj) == 1
    assert "OAuth2" in inj[0]["content"]


def test_activity_events_on_spawn(supervisor_stack: tuple[SupervisorService, Agent], monkeypatch) -> None:
    svc, _ = supervisor_stack
    events: list[tuple[str, dict]] = []

    def capture(kind, text="", **kwargs):
        events.append((kind, kwargs))
        return bus.publish(kind, text, **kwargs)

    monkeypatch.setattr("agent.supervisor.publish", capture)
    general = svc.store.get_project_by_slug("general")
    svc.spawn_sub_chat(int(general["id"]), title="Flight", brief="")
    kinds = [k for k, _ in events]
    assert "spawn" in kinds
    assert "supervisor" in kinds


def test_project_workdir_for_cursor(tmp_path: Path, store: AgentStore, monkeypatch: pytest.MonkeyPatch) -> None:
    wd = tmp_path / "proj-ws"
    wd.mkdir()
    general = store.get_project_by_slug("general")
    store.update_project_settings(int(general["id"]), {"cursor_workdir": str(wd)})
    conv = store.create_conversation("Cursor chat", project_id=int(general["id"]))
    resolved = store.cursor_workspace_for_conversation(conv["id"], Path("/fallback"))
    assert resolved == wd

    calls: list[Path] = []

    def fake_run(prompt, settings, **kwargs):
        calls.append(kwargs.get("workspace") or settings.cursor_workspace)
        from tools.router import RouteResult

        return RouteResult(reply="ok", action="run_cursor_agent", action_details={}, routed=True)

    monkeypatch.setattr(agent_loop.router, "run_cursor_task_detailed", fake_run)
    agent = Agent(
        Settings(),
        AgentConfig(db_path=store.db_path, auto_memory=False),
        store,
        FakeEmbedder(),
        FakeLLM(),
    )
    agent.respond("fix the button", conversation_id=conv["id"], mode="cursor")
    assert calls and calls[0] == wd


def test_memory_extract_scoped_to_project(supervisor_stack: tuple[SupervisorService, Agent]) -> None:
    _, agent = supervisor_stack
    general = agent.store.get_project_by_slug("general")
    conv = agent.store.create_conversation("Tea", project_id=int(general["id"]))
    agent.store.add_message(conv["id"], "user", "I love chamomile")
    agent.store.add_message(conv["id"], "assistant", "Noted.")
    agent.memory.extract_from_messages(
        agent.store.get_messages(conv["id"]),
        conversation_id=conv["id"],
        project_id=int(general["id"]),
    )
    pending = [m for m in agent.store.list_memories(status="pending") if m.get("project_id") == int(general["id"])]
    assert pending


def test_parse_directive_api_refactor_spawn() -> None:
    msg = "Spin up a chat for the API refactor, brief: move endpoints to v2"
    d = parse_directive(msg)
    assert d and d["tool"] == "spawn_sub_chat"
    assert d["title"] == "API refactor"
    assert d["brief"] == "move endpoints to v2"


def test_supervisor_spawn_beats_llm_move(store: AgentStore) -> None:
    general = store.get_project_by_slug("general")
    stray = store.create_conversation("Can you hear me? Can you hear me?", project_id=int(general["id"]))
    p2 = store.create_project("Test project 2")
    sup_id = int(p2["supervisor_conversation_id"])
    llm = FakeLLM()
    llm.json_reply = json.dumps({
        "tool": "move_sub_chat",
        "conversation_id": stray["id"],
        "project_id": p2["id"],
    })
    svc = SupervisorService(store, llm, Settings())
    msg = "Spin up a chat for the API refactor, brief: move endpoints to v2"
    reply = svc.run_turn(msg, project_id=int(p2["id"]), supervisor_conv_id=sup_id, history=[])
    assert reply.action == "spawn_sub_chat"
    assert store.get_conversation(stray["id"])["project_id"] == int(general["id"])
    subs = store.list_sub_chats(int(p2["id"]))
    assert any(
        c["title"] == "API refactor" and "move endpoints to v2" in (c.get("brief") or "")
        for c in subs
    )
    assert llm.calls == []  # deterministic path — LLM not consulted


def test_what_chats_lists_roster(store: AgentStore) -> None:
    p2 = store.create_project("P2")
    pid = int(p2["id"])
    sup_id = int(p2["supervisor_conversation_id"])
    svc = SupervisorService(store, FakeLLM(), Settings())
    svc.spawn_sub_chat(pid, title="API refactor", brief="move endpoints to v2", supervisor_conv_id=sup_id)
    svc.spawn_sub_chat(pid, title="flight research", brief="", supervisor_conv_id=sup_id)
    llm = FakeLLM()
    llm.json_reply = json.dumps({
        "tool": "move_sub_chat",
        "conversation_id": 999,
        "project_id": pid,
    })
    svc.llm = llm
    assert is_roster_question("What chats do we have?")
    reply = svc.run_turn("What chats do we have?", project_id=pid, supervisor_conv_id=sup_id, history=[])
    assert reply.action == "list_chats"
    assert "API refactor" in reply.reply
    assert "flight research" in reply.reply
    assert llm.calls == []


def test_summarise_everything_includes_empty_chats(store: AgentStore) -> None:
    p2 = store.create_project("P2")
    pid = int(p2["id"])
    sup_id = int(p2["supervisor_conversation_id"])
    svc = SupervisorService(store, FakeLLM(), Settings())
    a = svc.spawn_sub_chat(pid, title="API refactor", brief="move endpoints to v2", supervisor_conv_id=sup_id)
    b = svc.spawn_sub_chat(pid, title="flight research", brief="", supervisor_conv_id=sup_id)
    reply = svc.run_turn("Summarise everything", project_id=pid, supervisor_conv_id=sup_id, history=[])
    assert reply.action == "summarise_all"
    assert "API refactor" in reply.reply
    assert "flight research" in reply.reply
    assert "0 chat" not in reply.reply.lower()
    assert "no messages" in reply.reply.lower()
    assert "move endpoints" in (store.get_conversation(a["id"]).get("brief") or "")
    assert "No messages yet" in (store.get_conversation(b["id"]).get("brief") or "")


def test_llm_wrong_tool_on_question_falls_back_to_answer(store: AgentStore) -> None:
    general = store.get_project_by_slug("general")
    stray = store.create_conversation("Can you hear me?", project_id=int(general["id"]))
    p2 = store.create_project("P2")
    sup_id = int(p2["supervisor_conversation_id"])
    svc = SupervisorService(store, FakeLLM(), Settings())
    svc.spawn_sub_chat(int(p2["id"]), title="Only sub", brief="x", supervisor_conv_id=sup_id)
    llm = svc.llm
    llm.json_reply = json.dumps({
        "tool": "archive_sub_chat",
        "conversation_id": stray["id"],
    })
    reply = svc.run_turn("How is this project going?", project_id=int(p2["id"]), supervisor_conv_id=sup_id, history=[])
    assert reply.action == "supervisor_answer"
    assert store.get_conversation(stray["id"])["project_id"] == int(general["id"])
    assert "Only sub" in reply.reply or "sub-chat" in reply.reply.lower()


def test_llm_move_foreign_chat_blocked(store: AgentStore) -> None:
    general = store.get_project_by_slug("general")
    stray = store.create_conversation("Can you hear me?", project_id=int(general["id"]))
    p2 = store.create_project("P2")
    sup_id = int(p2["supervisor_conversation_id"])
    llm = FakeLLM()
    llm.json_reply = json.dumps({
        "tool": "move_sub_chat",
        "conversation_id": stray["id"],
        "project_id": p2["id"],
    })
    svc = SupervisorService(store, llm, Settings())
    reply = svc.run_turn(
        "move Can you hear me to project P2",
        project_id=int(p2["id"]),
        supervisor_conv_id=sup_id,
        history=[],
    )
    assert reply.action == "supervisor_clarify"
    assert "this project" in reply.reply.lower()
    assert store.get_conversation(stray["id"])["project_id"] == int(general["id"])


def test_move_in_project_via_directive(store: AgentStore) -> None:
    p2 = store.create_project("Dest")
    pid = int(p2["id"])
    sup_id = int(p2["supervisor_conversation_id"])
    sub = store.create_conversation("API refactor", project_id=pid, kind="sub", parent_id=sup_id)
    dest = store.create_project("Other")
    svc = SupervisorService(store, FakeLLM(), Settings())
    reply = svc.run_turn(
        f"move API refactor to project {dest['name']}",
        project_id=pid,
        supervisor_conv_id=sup_id,
        history=[],
    )
    assert reply.action == "move_sub_chat"
    assert store.get_conversation(sub["id"])["project_id"] == int(dest["id"])


def test_normalize_tool_rejects_oob_conversation_id(store: AgentStore) -> None:
    general = store.get_project_by_slug("general")
    stray = store.create_conversation("Old", project_id=int(general["id"]))
    p2 = store.create_project("P2")
    tool = {"tool": "archive_sub_chat", "conversation_id": stray["id"]}
    norm, err = normalize_tool(store, int(p2["id"]), tool, "archive Old")
    assert norm is None and err and "this project" in err.lower()


def test_supervisor_run_turn_spawn_json(supervisor_stack: tuple[SupervisorService, Agent]) -> None:
    svc, agent = supervisor_stack
    general = svc.store.get_project_by_slug("general")
    sup_id = int(general["supervisor_conversation_id"])
    agent.llm.json_reply = json.dumps({
        "tool": "spawn_sub_chat",
        "title": "Docs pass",
        "brief": "Readme cleanup",
        "aliases": ["docs"],
        "mode": "agent",
    })
    reply = svc.run_turn(
        "start a docs chat",
        project_id=int(general["id"]),
        supervisor_conv_id=sup_id,
        history=[],
    )
    assert "Docs pass" in reply.reply
    subs = svc.store.list_sub_chats(int(general["id"]))
    assert any(c["title"] == "Docs pass" for c in subs)