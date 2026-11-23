"""Layer 2 — orchestrator: Ollama with full conversation history decides to answer directly,
use a PC tool, or delegate to one or more workers; worker results are injected back as tool
messages and the model composes the final (spoken) reply. Up to ``max_rounds`` delegation rounds,
so a later worker can build on an earlier worker's output (context preserved across handoffs).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any

ProgressFn = Callable[[dict[str, Any]], None]

import ollama

from agent.delegation import Dispatcher, Task, TaskResult
from agent.models import get_manager, tool_calls_invalid
from agent.ollama_runtime import ollama_chat_kwargs
from config import Settings
from tools import router

logger = logging.getLogger(__name__)

ORCH_SYSTEM = """Per turn: `answer`, PC tool (open_app|run_command|set_volume), or `delegate`.
Workers: {workers}
Workspace/cursor/files → delegate code. After worker results: `answer` (1-3 sentences). Use context; no fake results."""

_ORCH_PC_TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "open_app",
        "description": "Launch whitelisted app.",
        "parameters": {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]}}},
    {"type": "function", "function": {
        "name": "run_command",
        "description": "Inspect this PC: disk, memory, uptime.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"},
            "args": {"type": "array", "items": {"type": "string"}, "default": []},
        }, "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "set_volume",
        "description": "Master volume 0-100.",
        "parameters": {"type": "object", "properties": {"percent": {"type": "integer", "minimum": 0, "maximum": 100}},
                       "required": ["percent"]}}},
]

FINAL_SYSTEM = """You are Sahir, a local voice assistant. Using the worker results below, answer the user's
request directly in 1-3 short spoken sentences, stating the key facts from the results (not just that
work was done). Mention failures honestly. No markdown. Use a short numbered list (1. 2.) when there are multiple points."""

_RESEARCH_RE = re.compile(
    r"\b("
    r"summari[sz]e|research|according to|"
    r"my (notes|docs|documents|files)|"
    r"meeting notes\b.*\b(my docs|my documents|my files|in my docs)\b"
    r")\b",
    re.I,
)
_CLICKUP_RE = re.compile(r"\bclick ?up\b", re.I)

# Block integration heuristics on code/infra/meta requests (not personal mail/calendar).
_INTEGRATION_HEURISTIC_BLOCK_RE = re.compile(
    r"\b("
    r"regex|regular expression|validate|validation|cron|gpu|cpu|ram|availability of|"
    r"python|javascript|typescript|function|class|script|implement|\.py\b|"
    r"stack trace|debug"
    r")\b",
    re.I,
)

_SUMMARIZE_VERB_RE = re.compile(r"\b(summari[sz]e|summary of|give me a summary of)\b", re.I)
_PERSONAL_MAIL_OBJECT_RE = re.compile(
    r"\b(my (email|inbox|mail)|unread (emails?|mail)|today'?s emails?)\b",
    re.I,
)
_RESEARCH_DOC_SUMMARIZE_RE = re.compile(
    r"\b(summari[sz]e|summary of|give me a summary of)\b.*\b("
    r"meeting notes|my (notes|docs|documents|files)|notes in my docs"
    r")\b",
    re.I,
)

_EMAIL_INTENT_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bmy (email|inbox|mail)\b", re.I),
    re.compile(r"\bunread (emails?|mail)\b", re.I),
    re.compile(r"\bcheck (my )?(email|inbox|mail)\b", re.I),
    re.compile(r"\b(draft|write|send)\s+(an?\s+)?(email|reply)\s+to\b", re.I),
    re.compile(r"\breply to\b.{0,80}\bemail\b", re.I),
    re.compile(r"\breply to\s+\w[\w'-]*'s email\b", re.I),
    re.compile(r"\bsearch (my )?(inbox|mail|email)\b", re.I),
    re.compile(r"\b(read|open)\s+(an?\s+)?(email|message)\b.*\b(inbox|from)\b", re.I),
    re.compile(r"\b(imap|smtp)\b", re.I),
    re.compile(
        r"\b(summari[sz]e|summary of|give me a summary of)\b.*\b("
        r"my (email|inbox|mail)|unread (emails?|mail)|today'?s emails?"
        r")\b",
        re.I,
    ),
)

_CALENDAR_INTENT_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bmy calendar\b", re.I),
    re.compile(r"\bwhat(?:'s| is) on\b.*\b(today|tomorrow|this week)\b", re.I),
    re.compile(r"\bwhat(?:'s| is) on (today|tomorrow|this week)\b", re.I),
    re.compile(r"\bam i free\b", re.I),
    re.compile(r"\b(check|am i)\s+(my )?(free|busy)\b", re.I),
    re.compile(
        r"\b(free|busy)\b.*\b(today|tomorrow|this week|at\s+\d{1,2}(:\d{2})?\s*(am|pm)?)\b",
        re.I,
    ),
    re.compile(r"\b(book|create|add)\s+(an?\s+)?(event|meeting|appointment)\b", re.I),
    re.compile(r"\bschedule\s+(an?\s+)?(event|meeting|appointment)\b", re.I),
    re.compile(r"\bmove my (meeting|appointment|event)\b", re.I),
    re.compile(r"\b(upcoming|next)\s+(meeting|appointment|event)s?\b", re.I),
    re.compile(r"\bcheck my (calendar|schedule)\b", re.I),
    re.compile(r"\blist\s+(my )?(calendar )?events\b", re.I),
    re.compile(r"\b(summari[sz]e|summary of|give me a summary of)\b.*\bmy calendar\b", re.I),
    re.compile(r"\bmy calendar\b.*\b(this week|today|tomorrow)\b", re.I),
)


def _blocks_integration_heuristic(text: str) -> bool:
    if _INTEGRATION_HEURISTIC_BLOCK_RE.search(text):
        return True
    return router._looks_like_code_task(text) or router._looks_like_cursor_cli_query(text)


def _email_summarize_intent(text: str) -> bool:
    if _RESEARCH_DOC_SUMMARIZE_RE.search(text):
        return False
    if _SUMMARIZE_VERB_RE.search(text) and _PERSONAL_MAIL_OBJECT_RE.search(text):
        return True
    return bool(
        _SUMMARIZE_VERB_RE.search(text)
        and re.search(r"\bmy (inbox|mail|email)\b", text, re.I)
    )


def heuristic_email_intent(text: str) -> bool:
    if _blocks_integration_heuristic(text):
        return False
    if _RESEARCH_DOC_SUMMARIZE_RE.search(text):
        return False
    if _email_summarize_intent(text):
        return True
    return any(p.search(text) for p in _EMAIL_INTENT_RES)


def heuristic_calendar_intent(text: str) -> bool:
    if _blocks_integration_heuristic(text):
        return False
    if _RESEARCH_DOC_SUMMARIZE_RE.search(text):
        return False
    return any(p.search(text) for p in _CALENDAR_INTENT_RES)


def heuristic_research_intent(text: str) -> bool:
    if heuristic_email_intent(text) or heuristic_calendar_intent(text):
        return False
    return bool(_RESEARCH_RE.search(text))


@dataclass
class OrchestrationResult:
    reply: str
    action: str
    action_details: dict[str, Any] = field(default_factory=dict)
    routed: bool = True
    tasks: list[dict[str, Any]] = field(default_factory=list)


class Orchestrator:
    def __init__(self, settings: Settings, dispatcher: Dispatcher, *, max_rounds: int = 3,
                 client: Any | None = None) -> None:
        self.settings = settings
        self.dispatcher = dispatcher
        self.max_rounds = max_rounds
        self._client = client or ollama.Client(host=settings.ollama_host)
        self._model: str | None = None  # model for the current run (set per turn)

    @property
    def model(self) -> str:
        return self._model or self.settings.ollama_model

    def _chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        return self._client.chat(model=self.model, messages=messages, **ollama_chat_kwargs(self.model, **kwargs))

    def _tools(self) -> list[dict[str, Any]]:
        types = list(self.dispatcher.workers)
        return [
            {"type": "function", "function": {
                "name": "answer", "description": "Chat, facts, or mental math (not run_command).",
                "parameters": {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"]}}},
            {"type": "function", "function": {
                "name": "delegate", "description": "Run a worker task.",
                "parameters": {"type": "object", "properties": {
                    "type": {"type": "string", "enum": types},
                    "description": {"type": "string"},
                }, "required": ["type", "description"]}}},
            *_ORCH_PC_TOOLS,
        ]

    def _system(self, context_block: str) -> str:
        workers = "; ".join(f"{w['type']}: {w['description']}" for w in self.dispatcher.catalog())
        s = ORCH_SYSTEM.format(workers=workers or "   (none)")
        return f"{s}\n\n{context_block}" if context_block else s

    def _heuristic_delegation(self, text: str) -> list[dict[str, Any]]:
        """Fallback when the model answers but the request clearly needs a worker."""
        calls: list[dict[str, Any]] = []
        if "email" in self.dispatcher.workers and heuristic_email_intent(text):
            entry: dict[str, Any] = {"type": "email", "description": text}
            if _email_summarize_intent(text):
                entry["email_action"] = "summarize"
            calls.append(entry)
        elif "calendar" in self.dispatcher.workers and heuristic_calendar_intent(text):
            calls.append({"type": "calendar", "description": text})
        elif "research" in self.dispatcher.workers and heuristic_research_intent(text):
            calls.append({"type": "research", "description": text})
        if not calls:
            from agent.design_worker import heuristic_design_intent

            if "design" in self.dispatcher.workers and heuristic_design_intent(text):
                calls.append({"type": "design", "description": text})
        if not calls:
            from agent.calc_worker import CalcWorker

            if "calc" in self.dispatcher.workers and CalcWorker.handles(text):
                calls.append({"type": "calc", "description": text})
        if "code" in self.dispatcher.workers and not calls:
            if router._looks_like_cursor_cli_query(text) or router._looks_like_code_task(text):
                calls.append({"type": "code", "description": text})
        if "clickup" in self.dispatcher.workers and _CLICKUP_RE.search(text):
            if calls:
                calls[-1]["track_in_clickup"] = True
            else:
                calls.append({"type": "clickup", "description": text, "clickup_action": "create_task"})
        return calls

    def _consumes(self, args: dict[str, Any]) -> bool:
        worker = self.dispatcher.workers.get(str(args.get("type")))
        return bool(getattr(worker, "consumes_results", False))

    def _make_task(self, args: dict[str, Any], base_ctx: dict[str, Any], previous: list[dict[str, Any]]) -> Task:
        ctx = dict(base_ctx)
        ctx["previous_results"] = list(previous)
        if args.get("track_in_clickup"):
            ctx["clickup"] = {"task_id": args.get("clickup_task_id")} if args.get("clickup_task_id") else True
        if args.get("type") == "clickup":
            ctx["action"] = args.get("clickup_action") or "create_task"
            if args.get("clickup_task_id"):
                ctx["task_id"] = args["clickup_task_id"]
        if args.get("type") == "email":
            ctx["action"] = args.get("email_action") or ctx.get("action")
        if args.get("type") == "calendar":
            ctx["action"] = args.get("calendar_action") or ctx.get("action")
            if args.get("when"):
                ctx["when"] = args["when"]
            if args.get("title"):
                ctx["title"] = args["title"]
        return Task.create(str(args.get("type")), str(args.get("description") or ""), context=ctx,
                           priority=args.get("priority") or "normal", callback="orchestrator")

    def run(self, text: str, *, history: list[dict[str, str]], context_block: str = "",
            base_context: dict[str, Any] | None = None, on_progress: ProgressFn | None = None) -> OrchestrationResult:
        mgr = get_manager()
        model, reason = mgr.initial_model(text) if mgr else (self.settings.ollama_model, None)
        self._model = model
        meta: dict[str, Any] = {"model": model, "requested_model": self.settings.ollama_model}
        if reason:
            meta["escalated"] = reason
        if on_progress:
            on_progress({"type": "progress", "stage": "orchestrator", "message": "Orchestrator planning…",
                         "model": model, "escalated": reason})
        try:
            res = self._run(text, history=history, context_block=context_block, base_context=base_context,
                            meta=meta, mgr=mgr, on_progress=on_progress)
        finally:
            self._model = None
        res.action_details = {**res.action_details, **meta}
        return res

    def _run(self, text: str, *, history: list[dict[str, str]], context_block: str,
             base_context: dict[str, Any] | None, meta: dict[str, Any], mgr: Any,
             on_progress: ProgressFn | None = None) -> OrchestrationResult:
        base_ctx = {"history": history[-12:], **(base_context or {})}
        messages: list[dict[str, Any]] = [{"role": "system", "content": self._system(context_block)},
                                          *history, {"role": "user", "content": text}]
        results: list[TaskResult] = []
        summaries: list[dict[str, Any]] = []
        task_log: list[dict[str, Any]] = []

        for round_no in range(self.max_rounds):
            try:
                tools = self._tools()
                resp = self._chat(messages, tools=tools, options={"temperature": 0.2, "num_predict": 300})
                msg0 = resp.get("message", {}) if isinstance(resp, dict) else resp.message.model_dump()
                bad = tool_calls_invalid(msg0, tools)
                if bad and mgr is not None and mgr.can_escalate(self.model):
                    logger.info("Escalating %s -> %s (%s)", self.model, mgr.fallback_model, bad)
                    mgr.notify_fallback_escalation(f"invalid_output:{bad}")
                    meta.update({"escalated": f"invalid_output:{bad}", "small_model": self.model,
                                 "model": mgr.fallback_model})
                    self._model = mgr.fallback_model
                    if on_progress:
                        on_progress({"type": "model", "message": f"Switching to fallback model {mgr.fallback_model}",
                                     "model": mgr.fallback_model, "reason": bad})
                    resp = self._chat(messages, tools=tools, options={"temperature": 0.2, "num_predict": 300})
            except Exception as exc:
                logger.exception("Orchestrator LLM call failed")
                return OrchestrationResult(reply=f"I could not reach the local model: {exc}", action="error",
                                           action_details={"error": str(exc)}, tasks=task_log)
            msg = resp.get("message", {}) if isinstance(resp, dict) else resp.message.model_dump()
            calls = msg.get("tool_calls") or []
            parsed = []
            for c in calls:
                fn = c.get("function", {}) if isinstance(c, dict) else c.function.model_dump()
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                parsed.append((fn.get("name", ""), args))

            if on_progress and parsed:
                on_progress({"type": "tool_calls", "round": round_no + 1,
                             "calls": [{"name": n, "args": a} for n, a in parsed]})

            if round_no == 0 and (not parsed or all(n == "answer" for n, _ in parsed)):
                forced = self._heuristic_delegation(text)
                if forced:
                    logger.info("Heuristic delegation: %s", [f["type"] for f in forced])
                    parsed = [("delegate", f) for f in forced]

            if not parsed:
                content = (msg.get("content") or "").strip()
                if results:
                    break
                return OrchestrationResult(reply=content or "Okay.", action="direct_answer", tasks=task_log)

            answer = next((a for n, a in parsed if n == "answer"), None)
            delegations = [a for n, a in parsed if n == "delegate"]
            pc_calls = [(n, a) for n, a in parsed if n in {"open_app", "run_command", "set_volume"}]

            if pc_calls and not delegations:
                n, a = pc_calls[0]
                if on_progress:
                    on_progress({"type": "progress", "stage": "tool", "message": f"Running PC tool: {n}", "tool": n})
                r = router._execute_tool_call({"function": {"name": n, "arguments": a}}, self.settings)
                return OrchestrationResult(reply=r.reply, action=r.action, action_details=r.action_details,
                                           tasks=task_log)
            if answer is not None and not delegations:
                if results:  # final answer after worker results: compose from the actual results
                    return self._result("", results, summaries, task_log, request=text)
                reply_text = str(answer.get("reply") or "Okay.")
                if round_no == 0 and "code" in self.dispatcher.workers:
                    if router._looks_like_cursor_cli_query(text) or (
                        router._reply_suggests_incomplete(reply_text)
                        and router._looks_like_code_task(text)
                    ):
                        logger.info("Overriding direct answer — delegating to code worker")
                        forced = [{"type": "code", "description": text}]
                        parsed = [("delegate", f) for f in forced]
                        delegations = forced
                        answer = None
                    else:
                        return OrchestrationResult(reply=reply_text, action="direct_answer", tasks=task_log)
                else:
                    return OrchestrationResult(reply=reply_text, action="direct_answer", tasks=task_log)
                if not delegations:
                    continue

            # Delegate: independent tasks in one round run in parallel; each later round sees earlier results.
            # Workers that consume other workers' output (e.g. clickup) run after the producers in the
            # same round so their context carries the fresh previous_results.
            delegations = [a for a in delegations if a.get("type")]
            producers = [a for a in delegations if not self._consumes(a)]
            consumers = [a for a in delegations if self._consumes(a)]
            tasks = [self._make_task(a, base_ctx, summaries) for a in producers]
            if on_progress and tasks:
                on_progress({"type": "delegate", "message": "Delegating to workers…",
                             "workers": [t.type for t in tasks]})
            round_results = self.dispatcher.dispatch_many(tasks) if tasks else []
            for a in consumers:
                fresh = summaries + [
                    {"task_id": t.id, "type": t.type, "status": r.status, "summary": _summary(r), "errors": r.errors}
                    for t, r in zip(tasks, round_results)
                ]
                t = self._make_task(a, base_ctx, fresh)
                tasks.append(t)
                round_results.append(self.dispatcher.dispatch(t))
            messages.append({"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "delegate", "arguments": t.to_dict() | {"context": None}}} for t in tasks]})
            for t, r in zip(tasks, round_results):
                results.append(r)
                s = {"task_id": t.id, "type": t.type, "status": r.status,
                     "summary": _summary(r), "errors": r.errors}
                summaries.append(s)
                task_log.append({"task": {k: v for k, v in t.to_dict().items() if k != "context"},
                                 "result": r.to_dict()})
                if on_progress:
                    on_progress({"type": "worker_result", "worker": t.type, "status": r.status,
                                 "task_id": t.id})
                messages.append({"role": "tool", "content": json.dumps(s, ensure_ascii=False)[:6000]})
            # Single-worker, non-ClickUp follow-up is unlikely; let the model decide next round.

        return self._result("", results, summaries, task_log, request=text)

    def _result(self, reply: str, results: list[TaskResult], summaries: list[dict[str, Any]],
                task_log: list[dict[str, Any]], request: str = "") -> OrchestrationResult:
        if not reply:
            reply = self._compose(summaries, request)
        ok = all(r.ok for r in results)
        return OrchestrationResult(
            reply=reply, action="delegated", tasks=task_log,
            action_details={"workers": [s["type"] for s in summaries], "success": ok,
                            "task_ids": [s["task_id"] for s in summaries], "results": summaries},
        )

    def _compose(self, summaries: list[dict[str, Any]], request: str = "") -> str:
        try:
            payload = f"User request: {request}\n\nWorker results:\n" + json.dumps(summaries, ensure_ascii=False)[:8000]
            resp = self._chat([
                {"role": "system", "content": FINAL_SYSTEM},
                {"role": "user", "content": payload}],
                options={"temperature": 0.3, "num_predict": 160})
            msg = resp.get("message", {}) if isinstance(resp, dict) else resp.message.model_dump()
            text = (msg.get("content") or "").strip()
            if text:
                return text
        except Exception as exc:
            logger.warning("Compose failed: %s", exc)
        return " ".join(f"{s['type']} task {s['status']}: {str(s['summary'])[:200]}" for s in summaries)


def _summary(r: TaskResult) -> str:
    out = r.output
    if isinstance(out, dict):
        return str(out.get("summary") or out.get("text") or out)[:3000]
    return str(out or "")[:3000]
