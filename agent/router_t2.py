"""Router Tier 2: pinned small-model one-letter S/D/C classify."""

from __future__ import annotations

import concurrent.futures
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from agent.chat_router import RouteContext, RouteDecision, STICKY_CONFIDENCE, resolve_target_chat
from agent.gpu_config import get_gpu_config
from agent.models import think_kwargs
from agent.router_model import router_classify_guard
from agent.router_t1 import Tier1Hit

logger = logging.getLogger(__name__)

_OVERRIDE_CHAT: ChatFn | None = None


class ChatFn(Protocol):
    def __call__(self, *, model: str, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]: ...


def _default_chat(*, model: str, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
    if _OVERRIDE_CHAT is not None:
        return _OVERRIDE_CHAT(model=model, messages=messages, **kwargs)
    import ollama
    from config import Settings

    client = ollama.Client(host=Settings.from_env().ollama_host)
    return client.chat(model=model, messages=messages, **kwargs)


FEW_SHOT = """Reply with ONE character only: S, D, or C.
S = voice command (switch/open chat, stop, new chat, pause)
D = supervisor / whole project (spawn chats, summarise all, list chats, cross-chat status)
C = message for the current active sub-chat (coding, questions, task work)

Examples:
User: summarise everything across chats -> D
User: spin up a chat for billing -> D
User: switch to the backend chat -> S
User: stop speaking -> S
User: add unit tests for login -> C
User: make the header sticky -> C
User: what is the capital of France -> C
"""


def _chat_names(ctx: RouteContext) -> str:
    names: list[str] = []
    active = None
    for c in ctx.chats:
        t = (c.get("title") or f"chat#{c.get('id')}").strip()
        if ctx.active_chat_id and int(c.get("id") or 0) == int(ctx.active_chat_id):
            active = t
        aliases = ", ".join(c.get("aliases") or []) or "—"
        names.append(f"- {t} (aliases: {aliases})")
    active_title = active or "(none)"
    return f"Active chat: {active_title}\nProject chats:\n" + ("\n".join(names) if names else "(none)")


def _build_prompt(utterance: str, ctx: RouteContext) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": FEW_SHOT + "\n" + _chat_names(ctx)},
        {"role": "user", "content": utterance.strip()[:800]},
    ]


def _parse_letter(content: str) -> str | None:
    if not content:
        return None
    m = re.search(r"[SDC]", content.upper())
    if not m:
        return None
    return m.group(0)


def _logprob_confidence(resp: dict[str, Any]) -> float | None:
    """Use Ollama logprobs when present; else None."""
    msg = resp.get("message") or {}
    lp = msg.get("logprobs") or resp.get("logprobs")
    if not lp:
        return None
    try:
        entries = lp if isinstance(lp, list) else lp.get("content") or []
        scores: dict[str, float] = {}
        for item in entries:
            tok = str(item.get("token", "")).upper()
            lab = tok[0] if tok and tok[0] in "SDC" else None
            if not lab:
                continue
            logp = float(item.get("logprob", item.get("logprob", -99)))
            scores[lab] = max(scores.get(lab, -99), logp)
        if not scores:
            return None
        ranked = sorted(scores.values(), reverse=True)
        if len(ranked) < 2:
            return 0.9
        import math

        return 1.0 / (1.0 + math.exp(-(ranked[0] - ranked[1])))
    except (TypeError, ValueError, KeyError):
        return None


@dataclass
class Tier2Hit:
    label: str
    confident: bool
    ms: float
    raw: str = ""
    confidence: float = STICKY_CONFIDENCE


def classify_tier2(
    utterance: str,
    ctx: RouteContext,
    *,
    t1: Tier1Hit | None = None,
    chat_fn: ChatFn | None = None,
    timeout_ms: float | None = None,
) -> Tier2Hit | None:
    cfg = get_gpu_config()
    if not cfg.router_t2_enabled:
        return None
    model = cfg.router_t2_fallback_model if cfg.router_t2_use_fallback else cfg.router_model
    chat = chat_fn or _default_chat
    timeout = (timeout_ms if timeout_ms is not None else cfg.router_t2_timeout_ms) / 1000.0
    opts = {"temperature": 0, "num_predict": 2, "num_ctx": cfg.router_num_ctx}
    t0 = time.perf_counter()

    def call() -> dict[str, Any]:
        kw = {**think_kwargs(model), "options": opts}
        with router_classify_guard():
            return chat(model=model, messages=_build_prompt(utterance, ctx), **kw)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(call)
            resp = fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        return Tier2Hit(label="C", confident=False, ms=(time.perf_counter() - t0) * 1000, raw="timeout")
    except Exception as exc:
        logger.warning("Tier 2 classify failed: %s", exc)
        return Tier2Hit(label="C", confident=False, ms=(time.perf_counter() - t0) * 1000, raw=str(exc))

    ms = (time.perf_counter() - t0) * 1000
    content = (resp.get("message") or {}).get("content", "") or ""
    letter = _parse_letter(content)
    if not letter:
        return Tier2Hit(label="C", confident=False, ms=ms, raw=content[:20])

    lp_conf = _logprob_confidence(resp)
    agree_t1 = t1 is not None and t1.label == letter and letter in {"S", "D"}
    margin_ok = lp_conf is not None and lp_conf >= cfg.router_t2_confidence
    heuristic = agree_t1 and (t1.margin or 0) >= cfg.router_t1_margin * 0.5
    confident = letter == "C" or margin_ok or (heuristic and letter in {"S", "D"})
    conf = lp_conf if lp_conf is not None else (0.85 if agree_t1 else STICKY_CONFIDENCE)
    return Tier2Hit(label=letter, confident=bool(confident), ms=ms, raw=content.strip()[:8], confidence=conf)


def try_tier2_route(
    utterance: str,
    ctx: RouteContext,
    *,
    store: Any,
    t1: Tier1Hit | None,
    t0: float,
    timing: dict[str, float],
    chat_fn: ChatFn | None = None,
) -> RouteDecision | None:
    hit = classify_tier2(utterance, ctx, t1=t1, chat_fn=chat_fn)
    if hit is None:
        return None
    timing["tier2_ms"] = hit.ms
    total_ms = (time.perf_counter() - t0) * 1000
    timing["total_ms"] = total_ms
    if not hit.confident or hit.label == "C":
        return RouteDecision(
            label="C",
            tier=2,
            confidence=hit.confidence,
            target_chat_id=ctx.active_chat_id,
            utterance=utterance,
            ms=total_ms,
            payload={"tier2": True, "sticky": not hit.confident, "raw": hit.raw},
            timing=dict(timing),
        )
    if hit.label == "D" and ctx.supervisor_chat_id:
        return RouteDecision(
            label="D",
            tier=2,
            confidence=hit.confidence,
            target_chat_id=ctx.supervisor_chat_id,
            utterance=utterance,
            ms=total_ms,
            payload={"tier2": True, "raw": hit.raw},
            timing=dict(timing),
        )
    if hit.label == "S":
        low = utterance.lower()
        cmd = "switch_chat" if any(w in low for w in ("switch", "open", "go to")) else None
        if "new chat" in low or "start a chat" in low:
            cmd = "new_chat"
        if "stop" in low or "quiet" in low:
            cmd = "stop_speech"
        if not cmd:
            return RouteDecision(
                label="C",
                tier=2,
                confidence=STICKY_CONFIDENCE,
                target_chat_id=ctx.active_chat_id,
                utterance=utterance,
                ms=total_ms,
                payload={"tier2": True, "sticky": True},
                timing=dict(timing),
            )
        tid = ctx.active_chat_id
        if cmd == "switch_chat" and store:
            tid = resolve_target_chat(store, ctx, utterance) or tid
        return RouteDecision(
            label="S",
            tier=2,
            confidence=hit.confidence,
            target_chat_id=tid,
            command=cmd,
            utterance=utterance,
            ms=total_ms,
            payload={"tier2": True, "raw": hit.raw},
            timing=dict(timing),
        )
    return None


def set_chat_client_for_tests(chat_fn: ChatFn | None) -> None:
    global _OVERRIDE_CHAT
    _OVERRIDE_CHAT = chat_fn