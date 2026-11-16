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
from agent.models import get_manager, think_kwargs, tool_calls_invalid
from config import Settings
from tools import router

logger = logging.getLogger(__name__)

ORCH_SYSTEM = """You are the orchestrator of a local voice assistant running on the user's PC.
For each user turn decide ONE of:
  1. answer directly (call `answer`) for chit-chat and general knowledge you are sure about;
  2. call a PC tool (open_app, run_command, set_volume) for simple desktop actions;
  3. call `delegate` to hand work to a specialised worker. Workers available:
{workers}
For questions about the Cursor CLI, workspace files, agent transcripts, or "ask cursor" requests,
delegate to the `code` worker — do not claim you lack access to the user's machine.
You may call `delegate` several times in one turn for independent sub-tasks. After workers
report back you will see their results; then either delegate a follow-up (e.g. file the research
result in ClickUp) or call `answer` with a short spoken reply (1-3 sentences) summarising the outcome.
Always use the conversation history and the context below. Never invent worker results."""

FINAL_SYSTEM = """You are Sahir, a local voice assistant. Using the worker results below, answer the user's
request directly in 1-3 short spoken sentences, stating the key facts from the results (not just that
work was done). Mention failures honestly. No markdown."""

_RESEARCH_RE = re.compile(r"\b(summari[sz]e|research|my (notes|docs|documents|files)|according to)\b", re.I)
_CLICKUP_RE = re.compile(r"\bclick ?up\b", re.I)


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
        return self._client.chat(model=self.model, messages=messages, **think_kwargs(self.model), **kwargs)

    def _tools(self) -> list[dict[str, Any]]:
        types = list(self.dispatcher.workers)
        pc_tools = [t for t in router._tools_schema() if t["function"]["name"] in {"open_app", "run_command", "set_volume"}]
        return [
            {"type": "function", "function": {
                "name": "answer", "description": "Reply to the user directly.",
                "parameters": {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"]}}},
            {"type": "function", "function": {
                "name": "delegate", "description": "Hand a task to a worker and wait for its result.",
                "parameters": {"type": "object", "properties": {
                    "type": {"type": "string", "enum": types},
                    "description": {"type": "string", "description": "Self-contained task description with all needed context."},
                    "priority": {"type": "string", "enum": ["urgent", "high", "normal", "low"]},
                    "track_in_clickup": {"type": "boolean", "description": "Also log this work in ClickUp."},
                    "clickup_action": {"type": "string", "enum": ["create_task", "update_status", "add_comment", "log_time"]},
                    "clickup_task_id": {"type": "string"},
                }, "required": ["type", "description"]}}},
            *pc_tools,
        ]

    def _system(self, context_block: str) -> str:
        workers = "\n".join(f"   - {w['type']}: {w['description']}" for w in self.dispatcher.catalog())
        s = ORCH_SYSTEM.format(workers=workers or "   (none)")
        return f"{s}\n\n{context_block}" if context_block else s

    def _heuristic_delegation(self, text: str) -> list[dict[str, Any]]:
        """Fallback when the model answers but the request clearly needs a worker."""
        calls: list[dict[str, Any]] = []
        if "research" in self.dispatcher.workers and _RESEARCH_RE.search(text):
            calls.append({"type": "research", "description": text})
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
