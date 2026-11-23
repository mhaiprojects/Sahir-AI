"""Tier-0 calc routing."""

from __future__ import annotations

from agent.calc_eval import try_tier0_calc
from agent.chat_router import RouteContext, route
from agent.store import AgentStore


def test_tier0_route_calc(tmp_path):
    store = AgentStore(tmp_path / "agent.db")
    ctx = RouteContext(active_project_id=None, active_chat_id=None, supervisor_chat_id=None, chats=[])
    d = route("what is 17 times 23", ctx, store=store)
    assert d.command == "calc"
    assert try_tier0_calc("what is 17 times 23") is not None