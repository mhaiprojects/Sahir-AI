"""Dispatch user input by per-chat mode (distinct from tools/router.py intent routing)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from agent.chat_router import (
    RouteDecision,
    build_route_context,
    route,
    typed_should_route_tier0,
)
from agent.gpu_config import get_gpu_config
from agent.loop import MODES, AgentReply
from agent.route_exec import execute_s_command, target_conversation_id
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


def _route_context(
    agent: "Agent",
    text: str,
    conversation_id: int | None,
    *,
    input_type: str,
    command_store: object | None,
    legacy_route: bool,
):
    ctx = build_route_context(agent.store, conversation_id)
    ctx.input_type = input_type
    ctx.command_store = command_store
    if input_type == "voice":
        ctx.tier0_enabled = True
    elif legacy_route:
        ctx.tier0_enabled = False
        ctx.allow_run_command = True
    elif typed_should_route_tier0(text):
        ctx.tier0_enabled = True
    else:
        ctx.tier0_enabled = False
    cfg = get_gpu_config()
    ctx.embedder = agent.embedder
    ctx.tier1_enabled = bool(cfg.router_t1_enabled and ctx.tier0_enabled)
    return ctx


def route_fields(decision: RouteDecision) -> dict[str, object]:
    return {
        "route_label": decision.label,
        "route_tier": decision.tier,
        "route_confidence": decision.confidence,
        "route_ms": decision.ms,
        "route_target_chat_id": decision.target_chat_id,
        "route_command": decision.command,
    }


def plan_route(
    agent: "Agent",
    text: str,
    *,
    conversation_id: int | None,
    input_type: str,
    command_store: object | None,
    legacy_route: bool,
) -> tuple[RouteDecision, object]:
    ctx = _route_context(
        agent, text, conversation_id, input_type=input_type,
        command_store=command_store, legacy_route=legacy_route,
    )
    decision = route(text, ctx, store=agent.store)
    return decision, ctx


def _agent_loop_mode(ui_mode: str) -> str:
    m = (ui_mode or "agent").strip().lower()
    if m == "stream":
        return "chat"
    return m if m in MODES else storage_mode_to_agent(m)


def execute_route_decision(
    agent: "Agent",
    text: str,
    decision: RouteDecision,
    ctx: object,
    *,
    input_type: str = "voice",
    force_cursor: bool = False,
    history: list[dict[str, str]] | None = None,
    extra_meta: dict | None = None,
    ui_mode: str | None = None,
) -> tuple[AgentReply | RouteResult, str]:
    """Run S commands or dispatch C/D to the right chat."""
    if decision.label == "S" and decision.command:
        raw = execute_s_command(agent, decision, ctx)  # type: ignore[arg-type]
        mode = effective_mode(agent.store, getattr(raw, "conversation_id", None))
        if isinstance(raw, RouteResult):
            return raw, "route"
        if raw.conversation_id:
            mode = effective_mode(agent.store, raw.conversation_id)
        return raw, mode

    conv_id = target_conversation_id(decision, ctx)  # type: ignore[arg-type]
    utterance = (decision.utterance or text).strip()
    meta = {**(extra_meta or {}), "input_type": input_type, "route_label": decision.label}

    if ui_mode is not None:
        loop_mode = "cursor" if force_cursor else _agent_loop_mode(ui_mode)
        reply = agent.respond(
            utterance,
            conversation_id=conv_id,
            mode=loop_mode,
            source="web",
            input_type=input_type,
            auto_session=True,
            extra_meta=meta,
        )
        return reply, loop_mode

    raw, mode = dispatch_voice_or_text(
        agent,
        utterance,
        conversation_id=conv_id,
        force_cursor=force_cursor,
        history=history,
        extra_meta=meta,
    )
    return raw, mode


def dispatch_routed(
    agent: "Agent",
    text: str,
    *,
    conversation_id: int | None,
    input_type: str = "voice",
    command_store: object | None = None,
    legacy_route: bool = False,
    force_cursor: bool = False,
    history: list[dict[str, str]] | None = None,
    extra_meta: dict | None = None,
) -> tuple[AgentReply | RouteResult, str, RouteDecision]:
    """Utterance router → S (no model), D (supervisor), or C (active chat by mode)."""
    decision, ctx = plan_route(
        agent, text, conversation_id=conversation_id, input_type=input_type,
        command_store=command_store, legacy_route=legacy_route,
    )
    raw, mode = execute_route_decision(
        agent, text, decision, ctx,
        input_type=input_type, force_cursor=force_cursor, history=history, extra_meta=extra_meta,
    )
    return raw, mode, decision


def dispatch_agent_chat(
    agent: "Agent",
    text: str,
    *,
    conversation_id: int | None,
    mode: str,
    command_store: object | None = None,
    force_cursor: bool = False,
) -> tuple[AgentReply | RouteResult, RouteDecision]:
    """Typed composer: Tier 0 when ``/sup``, ``/switch``, ``@``; else sticky C."""
    decision, ctx = plan_route(
        agent, text, conversation_id=conversation_id, input_type="text",
        command_store=command_store, legacy_route=False,
    )
    raw, _loop = execute_route_decision(
        agent, text, decision, ctx,
        input_type="text", force_cursor=force_cursor, ui_mode=mode,
    )
    return raw, decision


def legacy_route_tier0(
    agent: "Agent",
    text: str,
    *,
    conversation_id: int | None,
    command_store: object | None,
) -> RouteResult | None:
    """Tier-0 S commands on legacy ``/api/chat`` when ``route=True`` (e.g. ``run uptime``)."""
    ctx = _route_context(
        agent, text, conversation_id, input_type="text",
        command_store=command_store, legacy_route=True,
    )
    decision = route(text, ctx, store=agent.store)
    if decision.label == "S" and decision.command:
        raw = execute_s_command(agent, decision, ctx)
        if isinstance(raw, RouteResult):
            return raw
        return agent_reply_as_route(raw)
    return None