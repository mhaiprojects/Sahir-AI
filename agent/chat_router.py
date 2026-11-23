"""Utterance router Tier 0 + sticky default (architecture §4, §6.2). Not tools/router.py."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from activity import publish
from agent.store import AgentStore
from agent.supervisor_intent import parse_directive, resolve_chat_in_project

if TYPE_CHECKING:
    from agent.router_t1 import RouterEmbedder

STICKY_CONFIDENCE = 0.35
LABELS = frozenset({"S", "D", "C"})

_SWITCH = re.compile(
    r"(?is)^(?:please\s+)?(?:switch|open|go)\s+(?:to\s+)?(?:the\s+)?(?:chat\s+)?(.+)$"
)
_GO_BACK = re.compile(r"(?is)^(?:please\s+)?go\s+back\s+to\s+(?:the\s+)?(?:chat\s+)?(.+)$")
_SUPERVISOR_GO = re.compile(r"(?is)^(?:please\s+)?go\s+to\s+the\s+supervisor\s*\.?$")
_CLOSE = re.compile(r"(?is)^(?:please\s+)?close\s+(?:the\s+)?(?:chat|conversation)\s*\.?$")
_NEW_CHAT = re.compile(r"(?is)^(?:please\s+)?(?:new|start)\s+(?:a\s+)?(?:new\s+)?chat\s*\.?$")
_SUP_PREFIX = re.compile(r"(?is)^(?:supervisor|sahir)\s*[,:\s]+\s*(.+)$")
_RELAY = re.compile(r"(?is)^(?:please\s+)?(?:tell|in|to)\s+(?:the\s+)?(.+?)\s+chat\s*[,:\s]+\s*(.+)$")
_RUN_CMD = re.compile(r"(?is)^run\s+(.+)$")
_STOP_SPEAK = re.compile(r"(?is)^(?:please\s+)?(?:stop\s+(?:speaking|talking)|be\s+quiet)\s*\.?$")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _strip_article(s: str) -> str:
    return re.sub(r"^the\s+", "", (s or "").strip(), flags=re.I).strip()


@dataclass
class RouteContext:
    active_project_id: int | None
    active_chat_id: int | None
    supervisor_chat_id: int | None
    chats: list[dict[str, Any]]
    input_type: str = "voice"  # voice | text
    command_store: Any | None = None
    tier0_enabled: bool = True
    allow_run_command: bool = False  # legacy /api/chat route=True for typed ``run …``
    embedder: Any | None = None
    tier1_enabled: bool = False


@dataclass
class RouteDecision:
    label: str
    tier: int = 0
    confidence: float = STICKY_CONFIDENCE
    ms: float = 0.0
    target_chat_id: int | None = None
    command: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    utterance: str = ""


def build_route_context(store: AgentStore, active_chat_id: int | None) -> RouteContext:
    conv = store.get_conversation(active_chat_id) if active_chat_id else None
    if not conv:
        general = store.get_project_by_slug("general")
        pid = int(general["id"]) if general else None
        sup = int(general["supervisor_conversation_id"]) if general and general.get("supervisor_conversation_id") else None
        active = None
    else:
        pid = int(conv["project_id"]) if conv.get("project_id") else None
        active = int(conv["id"])
        proj = store.get_project(pid) if pid else None
        sup = int(proj["supervisor_conversation_id"]) if proj and proj.get("supervisor_conversation_id") else None
    chats: list[dict[str, Any]] = []
    if pid:
        for c in store.list_sub_chats(pid, include_archived=False):
            chats.append({"id": c["id"], "title": c.get("title"), "aliases": c.get("aliases") or []})
    return RouteContext(
        active_project_id=pid,
        active_chat_id=active,
        supervisor_chat_id=sup,
        chats=chats,
    )


def typed_should_route_tier0(text: str) -> bool:
    t = text.strip()
    low = t.lower()
    if low.startswith("/sup") or low.startswith("/switch"):
        return True
    if t.startswith("@"):
        return True
    if parse_directive(t):
        return True
    return False


def resolve_target_chat(store: AgentStore, ctx: RouteContext, hint: str) -> int | None:
    if not hint or ctx.active_project_id is None:
        return None
    h = _strip_article(hint)
    if _norm(h) in {"supervisor", "sahir"}:
        return ctx.supervisor_chat_id
    conv = resolve_chat_in_project(store, ctx.active_project_id, h)
    return int(conv["id"]) if conv else None


def _sticky(utterance: str, ctx: RouteContext) -> RouteDecision:
    return RouteDecision(
        label="C",
        tier=0,
        confidence=STICKY_CONFIDENCE,
        target_chat_id=ctx.active_chat_id,
        utterance=utterance,
        payload={"sticky": True},
    )


def route(utterance: str, ctx: RouteContext, *, store: AgentStore | None = None) -> RouteDecision:
    """Tier 0 + sticky default. Publishes a ``route`` activity event."""
    t0 = time.perf_counter()
    text = (utterance or "").strip()
    if not text:
        d = _sticky("", ctx)
        d.ms = (time.perf_counter() - t0) * 1000
        _publish_route(d)
        return d

    tier0 = ctx.tier0_enabled
    if ctx.input_type == "text" and not tier0 and not typed_should_route_tier0(text):
        if ctx.allow_run_command:
            m = _RUN_CMD.match(text)
            if m:
                tail = m.group(1).strip()
                parts = tail.split()
                cmd, args = (parts[0], parts[1:]) if parts else ("", [])
                d = RouteDecision(
                    label="S", tier=0, confidence=0.98, command="run_command",
                    utterance=text, payload={"command": cmd, "args": args},
                )
                d.ms = (time.perf_counter() - t0) * 1000
                _publish_route(d)
                return d
        d = _sticky(text, ctx)
        d.ms = (time.perf_counter() - t0) * 1000
        _publish_route(d)
        return d

    low = text.lower()
    if low.startswith("/sup"):
        rest = text.split(maxsplit=1)[1].strip() if len(text.split()) > 1 else ""
        d = RouteDecision(
            label="D", tier=0, confidence=0.99,
            target_chat_id=ctx.supervisor_chat_id, utterance=rest,
            payload={"via": "/sup"},
        )
        d.ms = (time.perf_counter() - t0) * 1000
        _publish_route(d)
        return d
    if low.startswith("/switch"):
        rest = text.split(maxsplit=1)[1].strip() if len(text.split()) > 1 else ""
        tid = resolve_target_chat(store, ctx, rest) if store else None
        d = RouteDecision(
            label="S", tier=0, confidence=0.99, command="switch_chat",
            target_chat_id=tid, utterance=text,
            payload={"target_hint": rest},
        )
        d.ms = (time.perf_counter() - t0) * 1000
        _publish_route(d)
        return d
    if text.startswith("@"):
        m = re.match(r"^@(\S+)\s*(.*)$", text, re.S)
        if m:
            alias, rest = m.group(1), m.group(2).strip()
            tid = resolve_target_chat(store, ctx, alias) if store else None
            d = RouteDecision(
                label="C", tier=0, confidence=0.95,
                target_chat_id=tid or ctx.active_chat_id,
                utterance=rest or text,
                payload={"mention": alias},
            )
            d.ms = (time.perf_counter() - t0) * 1000
            _publish_route(d)
            return d

    if tier0 and ctx.command_store is not None:
        matched = ctx.command_store.match(text)
        if matched:
            d = RouteDecision(
                label="S", tier=0, confidence=1.0,
                command=matched.action,
                utterance=text,
                payload={"keyword": matched.keyword, "voice_command_id": matched.id},
            )
            d.ms = (time.perf_counter() - t0) * 1000
            _publish_route(d)
            return d

    if tier0:
        m = _RUN_CMD.match(text)
        if m:
            tail = m.group(1).strip()
            parts = tail.split()
            cmd, args = (parts[0], parts[1:]) if parts else ("", [])
            d = RouteDecision(
                label="S", tier=0, confidence=0.98, command="run_command",
                utterance=text, payload={"command": cmd, "args": args},
            )
            d.ms = (time.perf_counter() - t0) * 1000
            _publish_route(d)
            return d

        if _STOP_SPEAK.match(text):
            d = RouteDecision(label="S", tier=0, confidence=0.98, command="stop_speech", utterance=text)
            d.ms = (time.perf_counter() - t0) * 1000
            _publish_route(d)
            return d

        m = _SUP_PREFIX.match(text)
        if m:
            d = RouteDecision(
                label="D", tier=0, confidence=0.98,
                target_chat_id=ctx.supervisor_chat_id,
                utterance=m.group(1).strip(),
                payload={"via": "supervisor_prefix"},
            )
            d.ms = (time.perf_counter() - t0) * 1000
            _publish_route(d)
            return d

        m = _RELAY.match(text)
        if m and store:
            tid = resolve_target_chat(store, ctx, m.group(1))
            d = RouteDecision(
                label="C", tier=0, confidence=0.92,
                target_chat_id=tid or ctx.active_chat_id,
                utterance=m.group(2).strip(),
                payload={"relay": True, "target_hint": m.group(1)},
            )
            d.ms = (time.perf_counter() - t0) * 1000
            _publish_route(d)
            return d

        if _SUPERVISOR_GO.match(text):
            d = RouteDecision(
                label="S", tier=0, confidence=0.95, command="switch_chat",
                target_chat_id=ctx.supervisor_chat_id, utterance=text,
            )
            d.ms = (time.perf_counter() - t0) * 1000
            _publish_route(d)
            return d

        if _CLOSE.match(text):
            d = RouteDecision(
                label="S", tier=0, confidence=0.95, command="switch_chat",
                target_chat_id=ctx.supervisor_chat_id, utterance=text,
                payload={"close": True},
            )
            d.ms = (time.perf_counter() - t0) * 1000
            _publish_route(d)
            return d

        if _NEW_CHAT.match(text):
            d = RouteDecision(label="S", tier=0, confidence=0.95, command="new_chat", utterance=text)
            d.ms = (time.perf_counter() - t0) * 1000
            _publish_route(d)
            return d

        m = _GO_BACK.match(text)
        if m and store:
            tid = resolve_target_chat(store, ctx, m.group(1))
            if tid:
                d = RouteDecision(
                    label="S", tier=0, confidence=0.93, command="switch_chat",
                    target_chat_id=tid, utterance=text,
                )
                d.ms = (time.perf_counter() - t0) * 1000
                _publish_route(d)
                return d

        m = _SWITCH.match(text)
        if m and store:
            tid = resolve_target_chat(store, ctx, m.group(1))
            if tid:
                d = RouteDecision(
                    label="S", tier=0, confidence=0.93, command="switch_chat",
                    target_chat_id=tid, utterance=text,
                )
                d.ms = (time.perf_counter() - t0) * 1000
                _publish_route(d)
                return d

        if ctx.supervisor_chat_id and parse_directive(text):
            d = RouteDecision(
                label="D", tier=0, confidence=0.94,
                target_chat_id=ctx.supervisor_chat_id,
                utterance=text,
                payload={"via": "supervisor_directive"},
            )
            d.ms = (time.perf_counter() - t0) * 1000
            _publish_route(d)
            return d

    if ctx.tier1_enabled and ctx.embedder is not None:
        from agent.router_t1 import try_tier1_route

        t1 = try_tier1_route(text, ctx, store=store, embedder=ctx.embedder, t0=t0)
        if t1 is not None:
            _publish_route(t1)
            return t1

    d = _sticky(text, ctx)
    d.ms = (time.perf_counter() - t0) * 1000
    _publish_route(d)
    return d


def _publish_route(d: RouteDecision) -> None:
    publish(
        "route",
        "",
        source="router",
        label=d.label,
        tier=d.tier,
        confidence=round(d.confidence, 3),
        ms=round(d.ms, 3),
        target_chat_id=d.target_chat_id,
        command=d.command,
    )