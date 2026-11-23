"""Side-effect execution policy (Wyoming confirm-only vs web execute)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.config import AgentConfig
from agent.execution_policy import CONFIRM_SPOKEN, POLICY_CONFIRM, POLICY_EXECUTE, side_effects_scope
from agent.factory import build_agent_stack
from agent.loop import Agent
from agent.numbered_actions import execute_numbered_action, get_pending_confirm
from agent.orchestrator import Orchestrator
from agent.route_exec import execute_s_command
from agent.chat_router import RouteContext, RouteDecision
from agent.store import AgentStore
from agent.supervisor import SupervisorService
from config import Settings
from tests.conftest import FakeEmbedder, FakeLLM
from tests.test_orchestrator import ScriptedClient, call, make


@pytest.fixture
def agent_with_orch(store: AgentStore) -> Agent:
    settings = Settings()
    agent = Agent(settings, AgentConfig(db_path=store.db_path), store, FakeEmbedder(), FakeLLM())
    from agent.delegation import Dispatcher

    d = Dispatcher()
    agent.orchestrator = Orchestrator(settings, d, client=ScriptedClient([call("run_command", command="uptime", args=[])]))
    return agent


def test_default_policy_executes_run_command(monkeypatch: pytest.MonkeyPatch, agent_with_orch: Agent) -> None:
    called: list[str] = []

    def fake_run(command, args, settings):
        called.append(command)
        return MagicMock(message="ok", success=True)

    monkeypatch.setattr("tools.router.run_command", fake_run)
    conv = agent_with_orch.store.create_conversation("t", project_id=agent_with_orch.store.general_project_id())
    with side_effects_scope(POLICY_EXECUTE, conversation_id=int(conv["id"])):
        r = agent_with_orch.orchestrator.run("uptime", history=[], base_context={"conversation_id": conv["id"]})
    assert called == ["uptime"]
    assert r.action == "run_command"


def test_confirm_policy_blocks_run_command(monkeypatch: pytest.MonkeyPatch, agent_with_orch: Agent) -> None:
    called: list[str] = []

    def fake_run(command, args, settings):
        called.append(command)
        return MagicMock(message="ok", success=True)

    monkeypatch.setattr("tools.router.run_command", fake_run)
    conv = agent_with_orch.store.create_conversation("t", project_id=agent_with_orch.store.general_project_id())
    with side_effects_scope(POLICY_CONFIRM, conversation_id=int(conv["id"])):
        r = agent_with_orch.orchestrator.run("uptime", history=[], base_context={"conversation_id": conv["id"]})
    assert called == []
    assert r.action == "confirm_pending"
    assert r.reply == CONFIRM_SPOKEN
    assert get_pending_confirm(int(conv["id"]))


def test_confirm_policy_blocks_clickup_delegate(monkeypatch: pytest.MonkeyPatch, store: AgentStore) -> None:
    o, workers, _ = make(
        [call("delegate", type="clickup", description="File task", clickup_action="create_task")],
        "clickup",
    )
    created: list[str] = []
    monkeypatch.setattr(
        "agent.clickup.execute_clickup_action",
        lambda *a, **k: created.append("x") or {"reply": "created"},
    )
    conv = store.create_conversation("t", project_id=store.general_project_id())
    with side_effects_scope(POLICY_CONFIRM, conversation_id=int(conv["id"])):
        r = o.run("create clickup task", history=[], base_context={"conversation_id": conv["id"]})
    assert r.action == "confirm_pending"
    assert not workers["clickup"].tasks
    assert not created
    assert get_pending_confirm(int(conv["id"]))


def test_confirm_policy_allows_research_delegate(store: AgentStore) -> None:
    o, workers, _ = make([call("delegate", type="research", description="Summarize notes"), {"message": {"content": "Done."}}], "research")
    conv = store.create_conversation("t", project_id=store.general_project_id())
    with side_effects_scope(POLICY_CONFIRM, conversation_id=int(conv["id"])):
        r = o.run("summarize notes", history=[], base_context={"conversation_id": conv["id"]})
    assert workers["research"].tasks
    assert r.action == "delegated"


def test_numbered_action_defers_run_command_under_confirm(store: AgentStore) -> None:
    settings = Settings()
    agent = Agent(settings, AgentConfig(db_path=store.db_path), store, FakeEmbedder(), FakeLLM())
    conv = store.create_conversation("t", project_id=store.general_project_id())
    called: list[int] = []

    def boom(*_a, **_k):
        called.append(1)
        raise AssertionError("must not run")

    import tools.pc_control as pc

    pc.run_command = boom  # type: ignore[method-assign]
    act = {"kind": "run_command", "command": "uptime", "args": []}
    with side_effects_scope(POLICY_CONFIRM, conversation_id=int(conv["id"])):
        r = execute_numbered_action(agent, act)
    assert r.action == "confirm_pending"
    assert r.reply == CONFIRM_SPOKEN
    assert not called
    assert get_pending_confirm(int(conv["id"]))


def test_supervisor_spawn_deferred_under_confirm(store: AgentStore) -> None:
    settings = Settings()
    sup = SupervisorService(store, FakeLLM(), settings)
    proj = store.create_project("Wyoming", "")
    with side_effects_scope(POLICY_CONFIRM, conversation_id=int(proj["supervisor_conversation_id"])):
        r = sup.execute_tool(
            int(proj["id"]),
            int(proj["supervisor_conversation_id"]),
            {"tool": "spawn_sub_chat", "title": "Edge case"},
        )
    assert r.action == "confirm_pending"
    assert r.reply == CONFIRM_SPOKEN
    subs = store.list_sub_chats(int(proj["id"]))
    assert not any((s.get("title") or "").lower() == "edge case" for s in subs)


def test_route_exec_run_command_deferred(store: AgentStore) -> None:
    settings = Settings()
    agent = Agent(settings, AgentConfig(db_path=store.db_path), store, FakeEmbedder(), FakeLLM())
    conv = store.create_conversation("t", project_id=store.general_project_id())
    ctx = RouteContext(
        active_chat_id=int(conv["id"]),
        active_project_id=store.general_project_id(),
        supervisor_chat_id=None,
        chats=[],
    )
    decision = RouteDecision(
        label="run_command",
        command="run_command",
        payload={"command": "uptime", "args": []},
        utterance="uptime",
    )
    with side_effects_scope(POLICY_CONFIRM, conversation_id=int(conv["id"])):
        reply = execute_s_command(agent, decision, ctx)
    assert reply.action == "confirm_pending"
    assert reply.reply == CONFIRM_SPOKEN