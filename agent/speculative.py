"""Side-effect-free speculative sticky-default work for voice (pre-warm / in-memory chat draft)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from agent.chat_router import RouteDecision
from agent.dispatcher import effective_mode

if TYPE_CHECKING:
    from agent.loop import Agent, AgentReply

logger = logging.getLogger(__name__)

CheckFn = Callable[[], bool]


@dataclass
class SpeculativeDraft:
    generation: int
    partial: str
    reply: str
    conversation_id: int | None
    mode: str


def _norm_prefix(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def trivial_extension(partial: str, final: str) -> bool:
    a, b = _norm_prefix(partial), _norm_prefix(final)
    if not a:
        return False
    return b.startswith(a) or b == a


def can_reuse_draft(
    draft: SpeculativeDraft | None,
    *,
    generation_at_capture: int,
    decision: RouteDecision,
    final_text: str,
    conversation_id: int | None,
) -> bool:
    if not draft or draft.generation != generation_at_capture:
        return False
    if decision.label != "C":
        return False
    if draft.mode != "chat":
        return False
    target = decision.target_chat_id or conversation_id
    if draft.conversation_id and target and int(draft.conversation_id) != int(target):
        return False
    return trivial_extension(draft.partial, final_text)


def run_speculative(
    agent: "Agent",
    *,
    partial: str,
    conversation_id: int | None,
    generation: int,
    still_valid: CheckFn,
    on_draft: Callable[[SpeculativeDraft], None],
) -> None:
    """No DB writes, no tools, no request_lock. Pre-warm or plain-chat draft only."""
    from activity import publish

    if not still_valid():
        return
    mode = effective_mode(agent.store, conversation_id)
    publish(
        "step",
        f"speculative {mode}: {len(partial.split())} words",
        source="speculative",
        level="debug",
    )
    if mode in {"agent", "route", "cursor"}:
        try:
            agent.retrieve(partial)
        except Exception as exc:
            logger.debug("speculative prewarm failed: %s", exc)
        return
    if mode != "chat":
        return
    if not still_valid():
        return
    try:
        from tools import router

        block = agent.retrieve(partial).to_block() or None
        if not still_valid():
            return
        hist = agent.history(conversation_id) if conversation_id else []
        result = router.chat_text_only_detailed(
            partial, agent.settings, history=hist[-6:] if hist else [], context=block,
        )
        if not still_valid():
            return
        on_draft(
            SpeculativeDraft(
                generation=generation,
                partial=partial,
                reply=result.reply or "",
                conversation_id=conversation_id,
                mode=mode,
            )
        )
    except Exception as exc:
        logger.debug("speculative draft skipped: %s", exc)


def commit_chat_draft(agent: "Agent", *, conversation_id: int | None, user_text: str, reply: str) -> "AgentReply":
    """Persist exactly one user + one assistant message (reuse in-memory draft)."""
    from agent.loop import AgentReply

    conv = agent.resolve_conversation(conversation_id, source="voice", auto_session=True)
    cid = int(conv["id"])
    user_id = agent.store.add_message(
        cid, "user", user_text, {"input_type": "voice", "speculative_reuse": True},
    )
    assistant_id = agent.store.add_message(
        cid, "assistant", reply or "(no reply)",
        {"action": "chat_text", "action_details": {"speculative_reuse": True}, "sources": {}},
    )
    return AgentReply(
        reply=reply,
        action="chat_text",
        action_details={"speculative_reuse": True},
        routed=False,
        conversation_id=cid,
        user_message_id=user_id,
        assistant_message_id=assistant_id,
        sources={},
    )

