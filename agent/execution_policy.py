"""Side-effect execution policy (web: execute; Wyoming: confirm-only)."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Iterator, Literal

from agent.numbered_actions import set_pending_confirm
from agent.numbered_replies import CONFIRM_ACTION_KINDS, DESTRUCTIVE_SUPERVISOR_TOOLS, action_requires_confirm
from tools.router import RouteResult

if TYPE_CHECKING:
    from agent.loop import Agent

SideEffectPolicy = Literal["execute", "confirm"]
POLICY_EXECUTE: SideEffectPolicy = "execute"
POLICY_CONFIRM: SideEffectPolicy = "confirm"
CONFIRM_SPOKEN = "Please confirm in the Sahir app."

_policy_var: ContextVar[SideEffectPolicy] = ContextVar("side_effects_policy", default=POLICY_EXECUTE)
_conversation_id_var: ContextVar[int | None] = ContextVar("agent_conversation_id", default=None)


def normalize_policy(value: str | None) -> SideEffectPolicy:
    if (value or "").strip().lower() == POLICY_CONFIRM:
        return POLICY_CONFIRM
    return POLICY_EXECUTE


def current_policy() -> SideEffectPolicy:
    return _policy_var.get()


@contextmanager
def side_effects_scope(policy: SideEffectPolicy, *, conversation_id: int | None = None) -> Iterator[None]:
    tok_p = _policy_var.set(policy)
    tok_c = _conversation_id_var.set(conversation_id)
    try:
        yield
    finally:
        _policy_var.reset(tok_p)
        _conversation_id_var.reset(tok_c)


def current_conversation_id() -> int | None:
    return _conversation_id_var.get()


def policy_requires_confirm() -> bool:
    return current_policy() == POLICY_CONFIRM


def action_dict_from_pc_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "run_command":
        return {"kind": "run_command", "command": str(args.get("command") or ""), "args": list(args.get("args") or [])}
    if name == "open_app":
        return {"kind": "open_app", "app_name": str(args.get("app_name") or "")}
    if name == "set_volume":
        return {"kind": "set_volume", "percent": int(args.get("percent") or 0)}
    return {"kind": name, **args}


def action_dict_from_delegation(args: dict[str, Any]) -> dict[str, Any]:
    wtype = str(args.get("type") or "")
    if wtype == "code" or wtype == "cursor":
        return {"kind": "cursor", "prompt": str(args.get("description") or "")}
    if wtype == "clickup":
        act = str(args.get("clickup_action") or "create_task")
        return {"kind": "clickup", "action": act, "name": str(args.get("description") or "")[:120]}
    if wtype == "email":
        act = str(args.get("email_action") or args.get("action") or "read")
        return {"kind": "email", "action": act}
    if wtype == "calendar":
        act = str(args.get("calendar_action") or args.get("action") or "list")
        return {"kind": "calendar", "action": act, "when": args.get("when"), "title": args.get("title")}
    if wtype == "delegate":
        return {"kind": "delegate", "description": str(args.get("description") or "")}
    return {"kind": wtype or "delegate", **{k: v for k, v in args.items() if k in ("description", "action")}}


def delegation_is_side_effect(args: dict[str, Any]) -> bool:
    wtype = str(args.get("type") or "")
    if wtype in ("code",):
        return True
    if wtype == "clickup":
        act = str(args.get("clickup_action") or "create_task")
        return act not in ("list", "status", "read")
    if wtype == "email":
        act = str(args.get("email_action") or args.get("action") or "read")
        return act in ("send_draft", "send")
    if wtype == "calendar":
        act = str(args.get("calendar_action") or args.get("action") or "list")
        return act in ("create_event", "update_event")
    return wtype in CONFIRM_ACTION_KINDS


def supervisor_tool_action(tool: dict[str, Any]) -> dict[str, Any]:
    name = str(tool.get("tool") or "answer")
    if name == "answer":
        return {"kind": "supervisor", "tool": "answer"}
    return {"kind": "supervisor", "tool": name, **{k: v for k, v in tool.items() if k != "tool"}}


def supervisor_tool_is_side_effect(tool: dict[str, Any]) -> bool:
    name = str(tool.get("tool") or "answer")
    if name == "answer":
        return False
    if name in DESTRUCTIVE_SUPERVISOR_TOOLS:
        return True
    act = supervisor_tool_action(tool)
    return action_requires_confirm(act)


def calendar_action_read_only(action: dict[str, Any]) -> bool:
    sub = str(action.get("action") or "")
    return sub in {"list", "availability", "status", "read", "summarize", "search"}


def email_action_read_only(action: dict[str, Any]) -> bool:
    sub = str(action.get("action") or "read")
    return sub not in ("send_draft", "send")


def clickup_action_read_only(action: dict[str, Any]) -> bool:
    sub = str(action.get("action") or "")
    return sub in {"list", "status", "read", ""}


def is_read_only_action(action: dict[str, Any]) -> bool:
    kind = str(action.get("kind") or "")
    if kind == "calendar":
        return calendar_action_read_only(action)
    if kind == "email":
        return email_action_read_only(action)
    if kind == "clickup":
        return clickup_action_read_only(action)
    if kind == "supervisor":
        return str(action.get("tool") or "") in ("answer", "list_chats", "summarise_all", "summarise_sub_chat")
    return not action_requires_confirm(action)


def blocked_under_policy(action: dict[str, Any]) -> bool:
    if not policy_requires_confirm():
        return False
    return not is_read_only_action(action) and (
        action_requires_confirm(action) or str(action.get("kind") or "") in CONFIRM_ACTION_KINDS
    )


def defer_side_effect(
    agent: "Agent",
    conversation_id: int,
    action: dict[str, Any],
    *,
    message_id: int = 0,
    label: str | None = None,
) -> RouteResult:
    """Record pending confirmation without executing the action."""
    text = (label or str(action.get("description") or action.get("prompt") or "Requested action")).strip()
    item = {"n": 1, "text": text[:240], "action": action}
    set_pending_confirm(conversation_id, item=item, message_id=message_id)
    return RouteResult(
        reply=CONFIRM_SPOKEN,
        routed=True,
        action="confirm_pending",
        action_details={"pending": True, "deferred_action": action},
    )