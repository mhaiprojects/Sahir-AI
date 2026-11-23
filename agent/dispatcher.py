"""Dispatch user input by per-chat mode (distinct from tools/router.py intent routing)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from agent.loop import MODES, AgentReply
from agent.store import AgentStore
from tools.router import RouteResult

if TYPE_CHECKING:
    from agent.loop import Agent


def default_chat_mode() -> str:
    return (os.getenv("DEFAULT_CHAT_MODE", "agent").strip() or "agent")


def storage_mode_to_agent(mode: str | None) -> str:
    """Map DB / UI mode strings to agent.loop ``MODES``."""
    m = (mode or default_chat_mode()).strip().lower()
    if m == "stream":
        return "chat"
    if m == "tools":
        return "route"
    if m in MODES:
        return m
    return default_chat_mode() if default_chat_mode() in MODES else "agent"


def agent_mode_to_ui(mode: str | None) -> str:
    m = (mode or "agent").strip().lower()
    if m == "chat":
        return "stream"
    if m == "route":
        return "route"
    return m if m in {"agent", "route", "stream", "cursor"} else "agent"


def effective_mode(store: AgentStore, conversation_id: int | None) -> str:
    """Resolved agent-loop mode for a conversation (per-chat, else default for new chats)."""
    if conversation_id:
        stored = store.get_conversation_mode(conversation_id)
        if stored:
            return storage_mode_to_agent(stored)
    return storage_mode_to_agent(default_chat_mode())


def agent_reply_as_route(r: AgentReply) -> RouteResult:
    return RouteResult(
        reply=r.reply,
        routed=r.routed,
        action=r.action,
        action_details=r.action_details,
    )


def dispatch_voice_or_text(
    agent: "Agent",
    text: str,
    *,
    conversation_id: int | None,
    force_cursor: bool = False,
    history: list[dict[str, str]] | None = None,
    extra_meta: dict | None = None,
) -> tuple[AgentReply | RouteResult, str]:
    """Return (result, agent_loop_mode). Uses orchestrator for ``agent`` mode (JW-8 fix)."""
    if force_cursor:
        mode = "cursor"
    else:
        mode = effective_mode(agent.store, conversation_id)

    if mode == "agent":
        reply = agent.respond(
            text,
            conversation_id=conversation_id,
            mode="agent",
            source="web",
            input_type=extra_meta.get("input_type", "text") if extra_meta else "text",
            auto_session=True,
            extra_meta=extra_meta,
        )
        return reply, mode

    if mode == "cursor":
        from tools import router

        result = router.run_cursor_task_detailed(text, agent.settings, history=history or [])
        return result, mode

    if mode == "route":
        from tools import router

        ctx = agent.retrieve(text).to_block() or None
        result = router.route_utterance_detailed(
            text, agent.settings, history=history or [], context=ctx,
        )
        return result, mode

    from tools import router

    ctx = agent.retrieve(text).to_block() or None
    result = router.chat_text_only_detailed(text, agent.settings, history=history or [], context=ctx)
    return result, mode