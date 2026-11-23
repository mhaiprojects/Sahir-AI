"""Execute numbered-list item actions and pending voice/text confirmations."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

from agent.numbered_replies import action_requires_confirm, confirm_prompt_for_action
from agent.store import AgentStore
from tools import pc_control
from tools.router import RouteResult

if TYPE_CHECKING:
    from agent.loop import Agent


_pending_lock = threading.Lock()
_pending_by_conv: dict[int, dict[str, Any]] = {}
PENDING_CONFIRM_TTL_S = 120.0


def _expire_pending_locked() -> None:
    now = time.monotonic()
    stale = [cid for cid, ent in _pending_by_conv.items() if now > float(ent.get("expires_at") or 0)]
    for cid in stale:
        _pending_by_conv.pop(cid, None)


def set_pending_confirm(conversation_id: int, *, item: dict[str, Any], message_id: int) -> str:
    prompt = confirm_prompt_for_action(item.get("action") or {})
    with _pending_lock:
        _expire_pending_locked()
        _pending_by_conv[int(conversation_id)] = {
            "item": item,
            "message_id": message_id,
            "prompt": prompt,
            "expires_at": time.monotonic() + PENDING_CONFIRM_TTL_S,
        }
    return prompt


def pop_pending_confirm(conversation_id: int) -> dict[str, Any] | None:
    with _pending_lock:
        _expire_pending_locked()
        return _pending_by_conv.pop(int(conversation_id), None)


def get_pending_confirm(conversation_id: int) -> dict[str, Any] | None:
    with _pending_lock:
        _expire_pending_locked()
        ent = _pending_by_conv.get(int(conversation_id))
        if ent and time.monotonic() > float(ent.get("expires_at") or 0):
            _pending_by_conv.pop(int(conversation_id), None)
            return None
        return ent


def clear_pending_confirm(conversation_id: int) -> None:
    with _pending_lock:
        _pending_by_conv.pop(int(conversation_id), None)


def latest_numbered_items(store: AgentStore, conversation_id: int) -> tuple[list[dict[str, Any]], int | None]:
    for m in reversed(store.get_messages(conversation_id, limit=40)):
        if m.get("role") != "assistant":
            continue
        meta = m.get("meta") or {}
        items = meta.get("numbered_items")
        if items:
            return list(items), int(m["id"])
    return [], None


def pick_item(items: list[dict[str, Any]], index: int) -> dict[str, Any] | None:
    if not items or index < 1:
        return None
    for it in items:
        if int(it.get("n") or 0) == index:
            return it
    if 1 <= index <= len(items):
        return items[index - 1]
    return None


def execute_numbered_action(agent: "Agent", action: dict[str, Any]) -> RouteResult:
    kind = str(action.get("kind") or "")
    if kind == "run_command":
        cmd = str(action.get("command") or "")
        args = [str(a) for a in (action.get("args") or [])]
        result = pc_control.run_command(cmd, args, agent.settings)
        return RouteResult(
            reply=result.message,
            routed=True,
            action="run_command",
            action_details={"command": cmd, "args": args, "success": result.success},
        )
    if kind == "open_app":
        from tools import pc_control as pc

        name = str(action.get("app_name") or "")
        result = pc.open_app(name, agent.settings)
        return RouteResult(reply=result.message, routed=True, action="open_app",
                           action_details={"app_name": name, "success": result.success})
    if kind == "set_volume":
        from tools import pc_control as pc

        pct = int(action.get("percent") or 0)
        result = pc.set_volume(pct, agent.settings)
        return RouteResult(reply=result.message, routed=True, action="set_volume", action_details={"percent": pct})
    if kind == "delegate":
        reply = agent.respond(
            str(action.get("description") or "Run delegated task"),
            conversation_id=None,
            mode="agent",
            source="numbered_action",
            input_type="text",
            auto_session=True,
        )
        from agent.dispatcher import agent_reply_as_route

        return agent_reply_as_route(reply)
    if kind == "cursor":
        from tools import router

        return router.run_cursor_task_detailed(
            str(action.get("prompt") or ""),
            agent.settings,
            history=[],
        )
    if kind == "clickup":
        from agent.clickup import ClickUpClient, ClickUpError, execute_clickup_action

        client = ClickUpClient.from_settings(agent.settings)
        try:
            out = execute_clickup_action(client, action)
            return RouteResult(
                reply=str(out.get("reply") or out.get("summary") or "ClickUp action completed."),
                routed=True,
                action="clickup",
                action_details={k: v for k, v in out.items() if k not in ("reply", "summary")},
            )
        except ClickUpError as exc:
            return RouteResult(reply=str(exc), routed=True, action="error", action_details={"clickup": True})

    if kind == "supervisor":
        pid = agent.store.general_project_id()
        proj = agent.store.get_project(pid) if pid else None
        sup_id = int(proj["supervisor_conversation_id"]) if proj and proj.get("supervisor_conversation_id") else None
        utterance = str(action.get("description") or action.get("brief") or action.get("title") or action.get("tool") or "")
        if not utterance or not sup_id:
            return RouteResult(reply="Supervisor action unavailable.", routed=False, action="error", action_details={})
        reply = agent.respond(
            utterance,
            conversation_id=sup_id,
            mode="agent",
            source="numbered_action",
            input_type="text",
            auto_session=True,
        )
        from agent.dispatcher import agent_reply_as_route

        return agent_reply_as_route(reply)
    return RouteResult(
        reply=f"Unsupported action kind {kind!r}.",
        routed=False,
        action="error",
        action_details={"kind": kind},
    )


def begin_do_action(agent: "Agent", conversation_id: int, index: int) -> RouteResult:
    items, msg_id = latest_numbered_items(agent.store, conversation_id)
    item = pick_item(items, index)
    if not item:
        return RouteResult(
            reply=f"I don't see item {index} in the latest numbered reply.",
            routed=True,
            action="do_action",
            action_details={"index": index, "found": False},
        )
    act = item.get("action")
    if not act:
        return RouteResult(
            reply=f"Item {index} has no action to run.",
            routed=True,
            action="do_action",
            action_details={"index": index, "found": True, "has_action": False},
        )
    if action_requires_confirm(act):
        prompt = set_pending_confirm(conversation_id, item=item, message_id=msg_id or 0)
        return RouteResult(reply=prompt, routed=True, action="confirm_pending",
                           action_details={"index": index, "pending": True})
    result = execute_numbered_action(agent, act)
    result.action_details = {**(result.action_details or {}), "numbered_index": index, "from_message_id": msg_id}
    return result


def resolve_pending_confirm(agent: "Agent", conversation_id: int, answer: str) -> RouteResult | None:
    pending = get_pending_confirm(conversation_id)
    if not pending:
        return None
    if answer == "no":
        clear_pending_confirm(conversation_id)
        return RouteResult(reply="Cancelled.", routed=True, action="confirm_cancelled", action_details={})
    if answer != "yes":
        return None
    clear_pending_confirm(conversation_id)
    act = (pending.get("item") or {}).get("action")
    if not act:
        return RouteResult(reply="Nothing to run.", routed=True, action="error", action_details={})
    result = execute_numbered_action(agent, act)
    result.action_details = {
        **(result.action_details or {}),
        "confirmed": True,
        "from_message_id": pending.get("message_id"),
    }
    return result