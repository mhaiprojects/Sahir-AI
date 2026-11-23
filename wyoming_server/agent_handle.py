"""Wyoming handle path: dedicated project/chat and side-effect guardrails."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Callable

from agent.numbered_actions import get_pending_confirm
from agent.numbered_replies import action_requires_confirm

if TYPE_CHECKING:
    from agent.loop import Agent, AgentReply
    from agent.store import AgentStore

logger = logging.getLogger(__name__)

WYOMING_CONFIRM_SPOKEN = "Please confirm in the Sahir app."


def wyoming_side_effect_refusal(reply: "AgentReply", store: "AgentStore") -> str | None:
    """Return spoken refusal when the turn needs in-app confirmation."""
    if (reply.action or "") == "confirm_pending":
        return WYOMING_CONFIRM_SPOKEN
    details: dict[str, Any] = dict(reply.action_details or {})
    if not details.get("kind") and reply.action:
        details = {"kind": reply.action, **details}
    if action_requires_confirm(details):
        return WYOMING_CONFIRM_SPOKEN
    if reply.conversation_id and get_pending_confirm(int(reply.conversation_id)):
        return WYOMING_CONFIRM_SPOKEN
    return None


def ensure_wyoming_conversation(
    store: "AgentStore",
    *,
    project_name: str,
    conversation_title: str,
) -> int:
    project = next(
        (p for p in store.list_projects(status="active") if (p.get("name") or "").lower() == project_name.lower()),
        None,
    )
    if project is None:
        project = store.create_project(project_name, "Home Assistant / Wyoming satellite")
    project_id = int(project["id"])
    chats = [
        c
        for c in store.list_conversations(project_id=project_id, status="active", limit=50)
        if (c.get("kind") or "chat") == "chat"
        and (c.get("title") or "").strip().lower() == conversation_title.lower()
    ]
    if chats:
        return int(chats[0]["id"])
    conv = store.create_conversation(
        conversation_title,
        source="wyoming",
        project_id=project_id,
        kind="chat",
        mode="agent",
    )
    return int(conv["id"])


def run_wyoming_handle(
    agent: "Agent",
    text: str,
    *,
    project_name: str,
    conversation_title: str,
    respond_fn: Callable[..., "AgentReply"] | None = None,
) -> tuple[bool, str]:
    """Run agent mode for a transcript; refuse side effects without confirmation."""
    text = (text or "").strip()
    if not text:
        return False, ""
    conv_id = ensure_wyoming_conversation(
        agent.store, project_name=project_name, conversation_title=conversation_title
    )
    respond = respond_fn or agent.respond
    reply = respond(
        text,
        conversation_id=conv_id,
        mode="agent",
        source="wyoming",
        input_type="voice",
        auto_session=True,
        side_effects="confirm",
        extra_meta={"wyoming": True, "input_type": "voice", "side_effects": "confirm"},
    )
    if (reply.action or "") == "confirm_pending" or get_pending_confirm(conv_id):
        return True, WYOMING_CONFIRM_SPOKEN
    refusal = wyoming_side_effect_refusal(reply, agent.store)
    if refusal:
        logger.info("Wyoming handle safety refusal for conv %s action=%s", conv_id, reply.action)
        return True, refusal
    spoken = (reply.tts_text or reply.reply or "").strip()
    if not spoken:
        spoken = "Done."
    return True, spoken