"""Ollama intent router and tool execution."""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from typing import Any, Callable

import ollama

from config import Settings
from tools.cursor_cli import run_cursor_agent
from tools.pc_control import open_app, run_command, set_volume

logger = logging.getLogger(__name__)

ChatTurn = dict[str, str]

ROUTER_SYSTEM = """You are a desktop voice assistant router with access to the ongoing conversation.
Classify the user's latest request and call exactly one tool.

Rules:
- Use prior messages for context when routing and answering.
- Use local_chat for general knowledge with no PC or code actions.
- Use pc tools for opening apps, volume, or simple system info.
- Use run_cursor_agent for anything involving code, files, git, tests, refactoring, or the workspace.
- Use run_cursor_agent when the user asks about Cursor CLI itself (last task, history, sessions, or "ask cursor").
- If the user refers to earlier messages ("that", "it", "the file"), use conversation history.
- Keep local_chat answers to 1-2 short spoken sentences.
- For cursor tasks, pass a clear prompt including relevant context from the conversation.
"""

DIRECT_CHAT_SYSTEM = (
    "Reply in 1-2 short sentences suitable for speech. "
    "Use the conversation history for context when the user refers to earlier turns."
)

_CODE_TASK_RE = re.compile(
    r"\b("
    r"code|file|files|git|repo|repository|bug|fix|implement|refactor|test|tests|"
    r"function|class|script|python|javascript|typescript|cursor|workspace|commit|"
    r"compile|build|deploy|endpoint|module|package|import|syntax|lint|merge|branch|"
    r"pull request|error in|stack trace|debug|readme|config\.|\.py\b|\.js\b|\.ts\b"
    r")\b",
    re.IGNORECASE,
)

# Cursor CLI history / meta (typos like "cursror" included — common in speech-to-text).
_CURSOR_CLI_RE = re.compile(
    r"\b("
    r"cursor|cursror|curser|cursor\s*cli|cursor-agent|cursor\s+agent|"
    r"ask\s+cursor|tell\s+cursor|send\s+to\s+cursor|"
    r"last\s+(task|job|run|command)\b.*\b(cli|cursor|agent)|"
    r"(cli|cursor|agent)\b.*\blast\s+(task|job|run|command)|"
    r"agent\s+transcript|chats/\s*|\.cursor/"
    r")\b",
    re.IGNORECASE,
)

_INCOMPLETE_REPLY_PHRASES = (
    "i can't",
    "i cannot",
    "i don't have access",
    "i do not have access",
    "i'm not able",
    "i am not able",
    "i don't know how",
    "unable to",
    "beyond my capabilities",
    "i can't directly",
    "would need to",
    "you would need to",
    "i don't have the ability",
    "outside my",
    "i'm unable",
    "not something i can",
)


def _client(settings: Settings) -> ollama.Client:
    return ollama.Client(host=settings.ollama_host)


def _pick_model(text: str, settings: Settings) -> tuple[str, dict[str, Any]]:
    """Selected chat model for this turn (+ metadata), honouring forced/user-requested fallback."""
    from agent.models import get_manager

    mgr = get_manager()
    if mgr is None:
        return settings.ollama_model, {"model": settings.ollama_model}
    model, reason = mgr.initial_model(text)
    meta: dict[str, Any] = {"model": model}
    if reason:
        meta["escalated"] = reason
    return model, meta


def _chat(client: Any, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
    from agent.ollama_runtime import ollama_chat_kwargs

    return client.chat(model=model, messages=messages, **ollama_chat_kwargs(model, **kwargs))


def _maybe_escalate(client: Any, model: str, messages: list[dict[str, Any]], response: Any,
                    meta: dict[str, Any], *, tools: list[dict[str, Any]], **kwargs: Any) -> tuple[str, Any]:
    """Retry with the fallback model when a small model returns invalid tool/action JSON."""
    from agent.models import get_manager, tool_calls_invalid

    mgr = get_manager()
    if mgr is None or not mgr.can_escalate(model):
        return model, response
    msg = response.get("message", {}) if isinstance(response, dict) else response.message.model_dump()
    bad = tool_calls_invalid(msg, tools)
    if not bad:
        return model, response
    logger.info("Router escalating %s -> %s (%s)", model, mgr.fallback_model, bad)
    mgr.notify_fallback_escalation(f"invalid_output:{bad}")
    meta.update({"escalated": f"invalid_output:{bad}", "small_model": model, "model": mgr.fallback_model})
    return mgr.fallback_model, _chat(client, mgr.fallback_model, messages, tools=tools, **kwargs)


def _normalize_history(history: list[ChatTurn] | None) -> list[ChatTurn]:
    if not history:
        return []
    cleaned: list[ChatTurn] = []
    for turn in history:
        role = str(turn.get("role", "")).strip()
        content = str(turn.get("content", "")).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        cleaned.append({"role": role, "content": content[:4000]})
    return cleaned[-20:]


def _build_messages(
    *,
    system: str,
    user_text: str,
    history: list[ChatTurn] | None = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for turn in _normalize_history(history):
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": user_text})
    return messages


def _with_context(system: str, context: str | None) -> str:
    """Append retrieved memory / RAG context to a system prompt."""
    if context and context.strip():
        return f"{system}\n\n{context.strip()}"
    return system


def _build_cursor_prompt(user_text: str, history: list[ChatTurn] | None = None) -> str:
    turns = _normalize_history(history)
    parts: list[str] = []
    if turns:
        parts.append("Conversation history:")
        for turn in turns:
            label = "User" if turn["role"] == "user" else "Assistant"
            parts.append(f"{label}: {turn['content']}")
        parts.append("")
    parts.append(f"Task: {user_text.strip()}")
    return "\n".join(parts)


def _looks_like_code_task(text: str) -> bool:
    return bool(_CODE_TASK_RE.search(text))


def _looks_like_cursor_cli_query(text: str) -> bool:
    return bool(_CURSOR_CLI_RE.search(text))


def _reply_suggests_incomplete(reply: str) -> bool:
    lower = reply.lower()
    return any(phrase in lower for phrase in _INCOMPLETE_REPLY_PHRASES)


def _cursor_available(settings: Settings) -> bool:
    return shutil.which(settings.cursor_agent_bin) is not None


def _run_cursor_task(
    prompt: str,
    settings: Settings,
    *,
    history: list[ChatTurn] | None = None,
    plan_mode: bool = False,
    on_long_task: Callable[[], None] | None = None,
    delegated_from: str | None = None,
) -> RouteResult:
    full_prompt = _build_cursor_prompt(prompt, history)
    if on_long_task:
        on_long_task()
    sandbox = settings.cursor_agent_sandbox or None
    if sandbox and sandbox not in ("enabled", "disabled"):
        sandbox = None
    cursor_result = run_cursor_agent(
        full_prompt,
        workspace=settings.cursor_workspace,
        model=settings.cursor_model,
        agent_bin=settings.cursor_agent_bin,
        api_key=settings.cursor_api_key,
        timeout_seconds=settings.cursor_timeout_seconds,
        plan_mode=plan_mode,
        force=settings.cursor_agent_force,
        sandbox=sandbox,
        auto_review=settings.cursor_agent_auto_review,
    )
    details: dict[str, Any] = {
        "prompt": prompt,
        "plan_mode": plan_mode,
        "success": cursor_result.returncode == 0,
        "exit_code": cursor_result.returncode,
    }
    if delegated_from:
        details["delegated_from"] = delegated_from
    return RouteResult(
        reply=cursor_result.text,
        action="run_cursor_agent",
        action_details=details,
        routed=True,
    )


def _maybe_delegate_to_cursor(
    text: str,
    result: RouteResult,
    settings: Settings,
    *,
    history: list[ChatTurn] | None = None,
    on_long_task: Callable[[], None] | None = None,
) -> RouteResult:
    if result.action == "run_cursor_agent":
        return result
    if not _cursor_available(settings):
        return result

    should_delegate = False
    delegated_from = result.action

    if result.action in {"local_chat", "fallback_chat", "direct_chat", "direct_answer"}:
        if _looks_like_cursor_cli_query(text):
            should_delegate = True
        elif _reply_suggests_incomplete(result.reply):
            should_delegate = True
        elif _looks_like_code_task(text) and result.action != "run_cursor_agent":
            should_delegate = True

    if not should_delegate:
        return result

    logger.info("Delegating to Cursor CLI after %s", delegated_from)
    cursor_result = _run_cursor_task(
        text,
        settings,
        history=history,
        on_long_task=on_long_task,
        delegated_from=delegated_from,
    )
    cursor_result.action_details["previous_reply"] = result.reply
    return cursor_result


def run_cursor_task_detailed(
    text: str,
    settings: Settings,
    *,
    history: list[ChatTurn] | None = None,
    plan_mode: bool = False,
    on_long_task: Callable[[], None] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    workspace: Any | None = None,
) -> RouteResult:
    """Force a task through Cursor CLI."""
    prompt = text.strip()
    if not prompt:
        return RouteResult(
            reply="I need a task description for Cursor.",
            action="run_cursor_agent",
            action_details={"success": False, "reason": "empty_prompt"},
            routed=True,
        )
    if not _cursor_available(settings):
        return RouteResult(
            reply="Cursor CLI is not available. Install cursor-agent and ensure it is on PATH.",
            action="run_cursor_agent",
            action_details={"success": False, "reason": "cursor_unavailable"},
            routed=True,
        )

    def _long() -> None:
        if on_progress:
            on_progress({"type": "progress", "stage": "cursor", "message": "Cursor CLI agent running…"})
        if on_long_task:
            on_long_task()

    if on_progress:
        on_progress({"type": "progress", "stage": "cursor", "message": "Starting Cursor CLI agent…"})
    ws = workspace if workspace is not None else settings.cursor_workspace

    def _cursor_settings() -> Settings:
        if ws == settings.cursor_workspace:
            return settings
        from dataclasses import replace

        return replace(settings, cursor_workspace=ws)

    return _run_cursor_task(
        prompt,
        _cursor_settings(),
        history=history,
        plan_mode=plan_mode,
        on_long_task=_long,
    )


def _tools_schema() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "local_chat",
                "description": "Answer a general question conversationally.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reply": {
                            "type": "string",
                            "description": "Short spoken answer, 1-2 sentences.",
                        }
                    },
                    "required": ["reply"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "open_app",
                "description": "Launch a whitelisted desktop application.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "app_name": {"type": "string"},
                    },
                    "required": ["app_name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": "Run a whitelisted read-only system command.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "default": [],
                        },
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "set_volume",
                "description": "Set master output volume percentage.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "percent": {"type": "integer", "minimum": 0, "maximum": 100},
                    },
                    "required": ["percent"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_cursor_agent",
                "description": "Execute a coding, repo, or workspace task via Cursor CLI.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                        "plan_mode": {"type": "boolean", "default": False},
                    },
                    "required": ["prompt"],
                },
            },
        },
    ]


@dataclass
class RouteResult:
    reply: str
    action: str = "none"
    action_details: dict[str, Any] = field(default_factory=dict)
    routed: bool = True


def route_utterance(
    text: str,
    settings: Settings,
    *,
    history: list[ChatTurn] | None = None,
    force_cursor: bool = False,
    on_long_task: Callable[[], None] | None = None,
) -> str:
    """Send transcript to Ollama and execute the selected tool."""
    return route_utterance_detailed(
        text,
        settings,
        history=history,
        force_cursor=force_cursor,
        on_long_task=on_long_task,
    ).reply


def route_utterance_detailed(
    text: str,
    settings: Settings,
    *,
    history: list[ChatTurn] | None = None,
    force_cursor: bool = False,
    on_long_task: Callable[[], None] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    context: str | None = None,
) -> RouteResult:
    """Route an utterance and return reply plus action metadata."""
    if force_cursor:
        return run_cursor_task_detailed(
            text,
            settings,
            history=history,
            on_long_task=on_long_task,
        )

    client = _client(settings)
    model, meta = _pick_model(text, settings)
    if on_progress:
        on_progress({"type": "model", "message": f"Routing with {model}", "model": model, **meta})
    messages = _build_messages(
        system=_with_context(ROUTER_SYSTEM, context),
        user_text=text,
        history=history,
    )
    try:
        response = _chat(client, model, messages, tools=_tools_schema(),
                         options={"num_predict": 256, "temperature": 0.2})
        model, response = _maybe_escalate(client, model, messages, response, meta,
                                          tools=_tools_schema(),
                                          options={"num_predict": 256, "temperature": 0.2})
        if on_progress and meta.get("escalated"):
            on_progress({"type": "model", "message": f"Switching to fallback model {model}",
                         "model": model, "reason": meta.get("escalated")})
    except Exception as exc:
        logger.exception("Ollama routing failed")
        return RouteResult(
            reply=(
                f"I could not reach Ollama at {settings.ollama_host}. "
                f"Start the service and pull {settings.ollama_model}. Error: {exc}"
            ),
            action="error",
            action_details={"error": str(exc)},
            routed=True,
        )

    message = response.get("message", {})
    tool_calls = message.get("tool_calls") or []

    if tool_calls:
        fn = tool_calls[0].get("function", {})
        if on_progress:
            on_progress({"type": "tool_calls", "calls": [{"name": fn.get("name", ""), "args": fn.get("arguments")}]})
        result = _execute_tool_call(
            tool_calls[0],
            settings,
            history=history,
            on_long_task=on_long_task,
        )
    else:
        content = (message.get("content") or "").strip()
        result = RouteResult(
            reply=content or "I did not understand that request.",
            action="fallback_chat",
            action_details={"source": "ollama_direct"},
            routed=True,
        )

    def _cursor_long() -> None:
        if on_progress:
            on_progress({"type": "progress", "stage": "cursor", "message": "Handing off to Cursor CLI agent…"})
        if on_long_task:
            on_long_task()

    result = _maybe_delegate_to_cursor(
        text,
        result,
        settings,
        history=history,
        on_long_task=_cursor_long,
    )
    result.action_details = {**result.action_details, **meta}
    return result


def _execute_tool_call(
    tool_call: dict[str, Any],
    settings: Settings,
    *,
    history: list[ChatTurn] | None = None,
    on_long_task: Callable[[], None] | None = None,
) -> RouteResult:
    fn = tool_call.get("function", {})
    name = fn.get("name", "")
    raw_args = fn.get("arguments", "{}")
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except json.JSONDecodeError:
        args = {}

    if name == "local_chat":
        reply = str(args.get("reply", "Okay."))
        return RouteResult(
            reply=reply,
            action="local_chat",
            action_details={"reply": reply},
            routed=True,
        )

    if name == "open_app":
        app_name = str(args.get("app_name", ""))
        result = open_app(app_name, settings)
        return RouteResult(
            reply=result.message,
            action="open_app",
            action_details={"app_name": app_name, "success": result.success},
            routed=True,
        )

    if name == "run_command":
        command = str(args.get("command", ""))
        cmd_args = list(args.get("args") or [])
        result = run_command(command, cmd_args, settings)
        return RouteResult(
            reply=result.message,
            action="run_command",
            action_details={
                "command": command,
                "args": cmd_args,
                "success": result.success,
            },
            routed=True,
        )

    if name == "set_volume":
        percent = int(args.get("percent", 50))
        result = set_volume(percent)
        return RouteResult(
            reply=result.message,
            action="set_volume",
            action_details={"percent": percent, "success": result.success},
            routed=True,
        )

    if name == "run_cursor_agent":
        prompt = str(args.get("prompt", "")).strip()
        if not prompt:
            return RouteResult(
                reply="I need a task description for Cursor.",
                action="run_cursor_agent",
                action_details={"success": False, "reason": "empty_prompt"},
                routed=True,
            )
        plan_mode = bool(args.get("plan_mode", False))
        return _run_cursor_task(
            prompt,
            settings,
            history=history,
            plan_mode=plan_mode,
            on_long_task=on_long_task,
        )

    return RouteResult(
        reply=f"Unknown tool {name!r}.",
        action="unknown_tool",
        action_details={"tool": name, "args": args},
        routed=True,
    )


def chat_text_only(
    text: str,
    settings: Settings,
    *,
    history: list[ChatTurn] | None = None,
) -> str:
    """Simple local chat without tool routing (for --text testing)."""
    return chat_text_only_detailed(text, settings, history=history).reply


def chat_text_only_detailed(
    text: str,
    settings: Settings,
    *,
    history: list[ChatTurn] | None = None,
    context: str | None = None,
) -> RouteResult:
    """Simple local chat with action metadata."""
    client = _client(settings)
    model, meta = _pick_model(text, settings)
    try:
        response = _chat(
            client,
            model,
            _build_messages(
                system=_with_context(DIRECT_CHAT_SYSTEM, context),
                user_text=text,
                history=history,
            ),
            options={"num_predict": 128, "temperature": 0.5},
        )
    except Exception as exc:
        return RouteResult(
            reply=f"Ollama error: {exc}",
            action="error",
            action_details={"error": str(exc)},
            routed=False,
        )
    reply = (response.get("message", {}).get("content") or "").strip()
    result = RouteResult(
        reply=reply,
        action="direct_chat",
        action_details={"model": model, **meta},
        routed=False,
    )
    return _maybe_delegate_to_cursor(text, result, settings, history=history)
