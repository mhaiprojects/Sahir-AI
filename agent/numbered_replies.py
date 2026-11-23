"""Numbered-list reply parsing, action attachment (from tool results only), and TTS formatting."""

from __future__ import annotations

import re
from typing import Any

from markdown_speech import strip_markdown_for_speech

_ITEM_RE = re.compile(r"^\s*(\d+)[.)]\s+", re.M)
_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
}
_SPEAK_ORDINALS = ("One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten")

NUMBERED_REPLY_HINT = (
    "When the answer has multiple points, use a short numbered list (1. 2. 3.) — one line per item."
)

DESTRUCTIVE_SUPERVISOR_TOOLS = frozenset({
    "archive_sub_chat", "move_sub_chat", "spawn_sub_chat", "inject_context",
})
CONFIRM_ACTION_KINDS = frozenset({
    "run_command", "open_app", "cursor", "delegate", "supervisor", "clickup",
})


def parse_numbered_text(text: str) -> list[dict[str, Any]]:
    """Parse '1.' / '1)' lists; a single paragraph becomes one item. Never invents actions."""
    raw = (text or "").strip()
    if not raw:
        return []
    matches = list(_ITEM_RE.finditer(raw))
    if not matches:
        return [{"n": 1, "text": raw, "action": None}]
    items: list[dict[str, Any]] = []
    for i, m in enumerate(matches):
        n = int(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        chunk = raw[start:end].strip()
        if chunk:
            items.append({"n": n, "text": chunk, "action": None})
    return items or [{"n": 1, "text": raw, "action": None}]


def format_numbered_display(items: list[dict[str, Any]]) -> str:
    return "\n".join(f"{it['n']}. {it['text']}" for it in items)


def has_explicit_numbered_list(text: str) -> bool:
    """True when the model output used at least two explicit ``1.`` / ``1)`` markers."""
    return len(list(_ITEM_RE.finditer((text or "").strip()))) >= 2


def show_numbered_list_ui(*, raw_reply: str, items: list[dict[str, Any]]) -> bool:
    return has_explicit_numbered_list(raw_reply) and len(items) >= 2


def tts_text_for_reply(*, raw_reply: str, items: list[dict[str, Any]]) -> str | None:
    if not show_numbered_list_ui(raw_reply=raw_reply, items=items):
        return None
    return format_tts(items)


def _ensure_spoken_sentence(text: str) -> str:
    spoken = strip_markdown_for_speech(text or "").strip()
    if not spoken:
        return ""
    if spoken[-1] not in ".!?":
        spoken += "."
    return spoken


def format_tts(items: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for it in items:
        idx = max(1, int(it.get("n") or 1)) - 1
        label = _SPEAK_ORDINALS[idx] if idx < len(_SPEAK_ORDINALS) else str(it.get("n"))
        body = _ensure_spoken_sentence(str(it.get("text") or ""))
        if body:
            parts.append(f"{label}: {body}")
    return " ".join(parts)


def action_requires_confirm(action: dict[str, Any] | None) -> bool:
    if not action:
        return False
    kind = str(action.get("kind") or "")
    if kind in CONFIRM_ACTION_KINDS:
        if kind == "supervisor":
            tool = str(action.get("tool") or "")
            return tool in DESTRUCTIVE_SUPERVISOR_TOOLS or tool not in ("answer",)
        return True
    return False


def action_chip_label(action: dict[str, Any] | None) -> str:
    if not action:
        return ""
    kind = action.get("kind") or "action"
    if kind == "run_command":
        cmd = action.get("command") or "command"
        args = action.get("args") or []
        return f"Run {cmd}" + (f" {' '.join(args)}" if args else "")
    if kind == "open_app":
        return f"Open {action.get('app_name') or 'app'}"
    if kind == "delegate":
        return f"Delegate to {action.get('type') or 'worker'}"
    if kind == "cursor":
        return "Cursor task"
    if kind == "supervisor":
        return f"Supervisor: {action.get('tool') or 'action'}"
    if kind == "clickup":
        sub = action.get("action") or action.get("clickup_action") or "create_task"
        if sub == "create_task":
            return f"Create task: {action.get('name') or 'task'}"
        if sub == "add_comment":
            return "Add ClickUp comment"
        if sub == "update_status":
            return f"Set status: {action.get('status') or 'complete'}"
        return f"ClickUp {str(sub).replace('_', ' ')}"
    return str(kind).replace("_", " ")


def _action_from_turn(action: str, details: dict[str, Any], tasks: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if action == "run_command" and details.get("command"):
        return {"kind": "run_command", "command": details["command"], "args": list(details.get("args") or [])}
    if action == "open_app" and details.get("app_name"):
        return {"kind": "open_app", "app_name": details["app_name"]}
    if action == "set_volume" and "percent" in details:
        return {"kind": "set_volume", "percent": details["percent"]}
    if action in {"delegated", "delegate"} and tasks:
        t0 = tasks[0].get("task") or tasks[0]
        return {"kind": "delegate", "type": t0.get("type"), "description": t0.get("description") or ""}
    if action == "delegate" and details.get("type"):
        return {"kind": "delegate", "type": details["type"], "description": details.get("description") or ""}
    if action.startswith("spawn_") or action in {
        "archive_sub_chat", "unarchive_sub_chat", "move_sub_chat", "switch_chat", "inject_context",
        "update_brief", "rename_sub_chat", "supervisor",
    }:
        tool = details.get("tool") or action
        payload = {k: v for k, v in details.items() if k not in ("tool",)}
        return {"kind": "supervisor", "tool": tool, **payload}
    if action == "cursor" or details.get("cursor"):
        return {"kind": "cursor", "prompt": details.get("prompt") or details.get("message") or ""}
    if action == "clickup" or details.get("clickup"):
        payload = {k: v for k, v in details.items() if v is not None and k != "clickup"}
        if details.get("clickup_action"):
            payload["action"] = details["clickup_action"]
        elif details.get("action") and details.get("action") not in ("clickup", "delegated"):
            payload.setdefault("action", details["action"])
        elif isinstance(details.get("results"), list):
            for row in details["results"]:
                if row.get("type") == "clickup" and isinstance(row.get("summary"), str):
                    m = re.search(r"https://app\.clickup\.com/\S+", row["summary"])
                    if m:
                        payload.setdefault("name", row.get("summary", "")[:80])
        if details.get("name"):
            payload.setdefault("name", details["name"])
        if details.get("url"):
            payload.setdefault("url", details["url"])
        if details.get("task_id"):
            payload.setdefault("task_id", details["task_id"])
        payload.setdefault("action", payload.get("action") or "create_task")
        return {"kind": "clickup", **payload}
    return None


def _clickup_action_from_tasks(tasks: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for ent in tasks or []:
        task = ent.get("task") or ent
        if task.get("type") != "clickup":
            continue
        res = ent.get("result") if isinstance(ent, dict) else {}
        out = (res or {}).get("output") if isinstance(res, dict) else {}
        if not isinstance(out, dict):
            continue
        return {
            "kind": "clickup",
            "action": out.get("action") or "create_task",
            "name": out.get("name") or task.get("description"),
            "task_id": out.get("task_id"),
            "url": out.get("url"),
            "description": task.get("description") or "",
        }
    return None


def attach_actions_from_turn(
    items: list[dict[str, Any]],
    *,
    action: str,
    action_details: dict[str, Any] | None,
    tasks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Attach at most one concrete action per item index (from orchestrator/supervisor output only)."""
    details = dict(action_details or {})
    act = _clickup_action_from_tasks(tasks) or _action_from_turn(action or "", details, tasks)
    if not act:
        if tasks and len(items) >= len(tasks):
            out = []
            for i, it in enumerate(items):
                if i < len(tasks):
                    t = tasks[i].get("task") or tasks[i]
                    out.append({**it, "action": {"kind": "delegate", "type": t.get("type"), "description": t.get("description") or ""}})
                else:
                    out.append(dict(it))
            return out
        return [dict(it) for it in items]
    out = [dict(it) for it in items]
    if out:
        out[0]["action"] = act
    return out


def build_numbered_reply(
    reply: str,
    *,
    action: str,
    action_details: dict[str, Any] | None = None,
    tasks: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]], bool]:
    raw = (reply or "").strip()
    items = parse_numbered_text(reply)
    items = attach_actions_from_turn(items, action=action, action_details=action_details, tasks=tasks)
    numbered_ui = show_numbered_list_ui(raw_reply=raw, items=items)
    if numbered_ui:
        display = format_numbered_display(items)
    elif len(items) == 1:
        display = items[0]["text"]
    else:
        display = format_numbered_display(items)
    return display, items, numbered_ui


def parse_do_action_index(text: str) -> int | None:
    t = (text or "").strip()
    if not t:
        return None
    low = t.lower().replace("'", "")
    patterns = [
        r"(?is)^(?:please\s+)?(?:do|run)\s+(?:action|number|item)\s+(\d+|[a-z]+)\s*$",
        r"(?is)^(?:please\s+)?do\s+the\s+(\d+)(?:st|nd|rd|th)?\s+one\s*$",
        r"(?is)^(?:please\s+)?do\s+the\s+(first|second|third|fourth|fifth)\s+one\s*$",
        r"(?is)^(?:please\s+)?run\s+item\s+(\d+|[a-z]+)\s*$",
    ]
    for pat in patterns:
        m = re.match(pat, t) or re.match(pat, low)
        if not m:
            continue
        token = m.group(1).lower()
        if token.isdigit():
            return int(token)
        return _ORDINAL_WORDS.get(token)
    return None


def parse_confirm_answer(text: str) -> str | None:
    low = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not low:
        return None
    words = low.split()
    if low in {"yes", "yeah", "yep", "confirm", "do it", "go ahead", "sure", "ok", "okay", "please do"}:
        return "yes"
    if low in {"no", "nope", "cancel", "never mind", "nevermind", "stop", "don't"}:
        return "no"
    if len(words) <= 4 and re.match(r"^yes\b", low):
        return "yes"
    if len(words) <= 4 and re.match(r"^(no|cancel)\b", low):
        return "no"
    return None


def confirm_prompt_for_action(action: dict[str, Any]) -> str:
    chip = action_chip_label(action)
    if action.get("kind") == "run_command":
        cmd = action.get("command") or ""
        args = " ".join(action.get("args") or [])
        spoken = f"{cmd} {args}".strip()
        return f"Run `{spoken}`? Say yes to confirm."
    return f"{chip}? Say yes to confirm."