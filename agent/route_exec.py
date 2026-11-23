"""Execute chat_router S/D/C decisions (no LLM on Tier-0 commands)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from activity import publish
from agent.chat_router import RouteDecision, RouteContext
from agent.loop import AgentReply
from agent.store import AgentStore
from tools import pc_control
from tools.router import RouteResult

if TYPE_CHECKING:
    from agent.loop import Agent


def _publish_switch(store: AgentStore, conv_id: int | None) -> None:
    if not conv_id:
        return
    conv = store.get_conversation(conv_id)
    if not conv:
        return
    publish(
        "switch",
        "",
        source="router",
        conversation_id=conv_id,
        project_name=conv.get("project_name") or "General",
        chat_title=conv.get("title") or f"Chat #{conv_id}",
    )


def execute_s_command(
    agent: "Agent",
    decision: RouteDecision,
    ctx: RouteContext,
) -> AgentReply | RouteResult:
    cmd = decision.command or ""
    store = agent.store

    if cmd == "switch_chat":
        tid = decision.target_chat_id
        if not tid:
            return AgentReply(
                reply="I couldn't find that chat to switch to.",
                action="switch_chat",
                action_details={"success": False},
                routed=True,
                conversation_id=ctx.active_chat_id or 0,
                user_message_id=0,
                assistant_message_id=0,
                sources={},
            )
        _publish_switch(store, tid)
        conv = store.get_conversation(tid)
        name = conv.get("title") if conv else f"chat #{tid}"
        return AgentReply(
            reply=f"Switched to {name}.",
            action="switch_chat",
            action_details={"conversation_id": tid, "success": True},
            routed=True,
            conversation_id=tid,
            user_message_id=0,
            assistant_message_id=0,
            sources={},
        )

    if cmd == "new_chat":
        pid = ctx.active_project_id or store.general_project_id()
        proj = store.get_project(int(pid)) if pid else None
        sup = int(proj["supervisor_conversation_id"]) if proj and proj.get("supervisor_conversation_id") else None
        conv = store.create_conversation(project_id=pid, kind="sub", parent_id=sup)
        _publish_switch(store, conv["id"])
        return AgentReply(
            reply=f"Started new chat “{conv['title']}”.",
            action="new_chat",
            action_details={"conversation": conv},
            routed=True,
            conversation_id=int(conv["id"]),
            user_message_id=0,
            assistant_message_id=0,
            sources={},
        )

    if cmd == "run_command":
        command = str(decision.payload.get("command") or "")
        args = list(decision.payload.get("args") or [])
        result = pc_control.run_command(command, args, agent.settings)
        return RouteResult(
            reply=result.message,
            action="run_command",
            action_details={"command": command, "args": args, "success": result.success},
            routed=True,
        )

    if cmd in {"stop_speech", "cancel_request"}:
        return AgentReply(
            reply="Stopped.",
            action=cmd,
            action_details={"keyword": decision.payload.get("keyword")},
            routed=True,
            conversation_id=ctx.active_chat_id or 0,
            user_message_id=0,
            assistant_message_id=0,
            sources={},
        )

    if cmd == "pause_listening":
        return AgentReply(
            reply="Listening paused.",
            action="pause_listening",
            action_details={},
            routed=True,
            conversation_id=ctx.active_chat_id or 0,
            user_message_id=0,
            assistant_message_id=0,
            sources={},
        )

    if cmd == "resume_listening":
        return AgentReply(
            reply="Listening resumed.",
            action="resume_listening",
            action_details={},
            routed=True,
            conversation_id=ctx.active_chat_id or 0,
            user_message_id=0,
            assistant_message_id=0,
            sources={},
        )

    if cmd == "repeat_last":
        return AgentReply(
            reply="Okay, repeating the last reply.",
            action="repeat_last",
            action_details={},
            routed=True,
            conversation_id=ctx.active_chat_id or 0,
            user_message_id=0,
            assistant_message_id=0,
            sources={},
        )

    return AgentReply(
        reply=f"Unknown command {cmd!r}.",
        action="unknown_command",
        action_details={"command": cmd},
        routed=True,
        conversation_id=ctx.active_chat_id or 0,
        user_message_id=0,
        assistant_message_id=0,
        sources={},
    )


def target_conversation_id(decision: RouteDecision, ctx: RouteContext) -> int | None:
    if decision.label == "D":
        return decision.target_chat_id or ctx.supervisor_chat_id
    return decision.target_chat_id or ctx.active_chat_id