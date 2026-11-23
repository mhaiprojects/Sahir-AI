"""Calculation worker: safe eval fast path + bwrap sandbox for larger jobs."""

from __future__ import annotations

import logging
import re

from agent.calc_eval import format_calc_worker_reply, format_number, try_evaluate_spoken
from agent.calc_sandbox import SandboxError, SandboxUnavailable, run_sandboxed_python
from agent.delegation import Task, WorkerError
from agent.llm import ChatLLM

logger = logging.getLogger(__name__)

_CALC_HINT = re.compile(
    r"(?is)\b(calculate|computation|numeric|simulate|statistics?|percent|gst|equation|formula)\b|"
    r"[0-9].*(?:\+|\-|\*|/|\^|times|percent)"
)

_CODE_SYSTEM = """You write short Python snippets for a sandboxed calculator.
Rules: only stdlib modules math, statistics, fractions, decimal, datetime, itertools, functools, collections.
No file I/O, network, subprocess, or imports beyond that list. Print the final answer to stdout.
Return only the Python source, no markdown fences."""


class CalcWorker:
    type = "calc"
    description = "Arithmetic and short Python stats/math (sandboxed when needed)."
    timeout_s = 30.0
    max_retries = 0

    def __init__(self, llm: ChatLLM) -> None:
        self.llm = llm

    def run(self, task: Task) -> dict:
        text = (task.context.get("query") or task.description or "").strip()
        if not text:
            raise WorkerError("empty calculation task")
        spoken = try_evaluate_spoken(text)
        if spoken is not None and not _needs_sandbox(text):
            lines = [f"Expression: {spoken.expression}", f"Result: {format_number(spoken.value)}"]
            for note in spoken.notes:
                if note not in spoken.expression:
                    lines.insert(1, note)
            display, meta = format_calc_worker_reply(lines=lines)
            return {
                "summary": display,
                "text": display,
                "value": spoken.value,
                "message_meta": meta,
            }
        code = self._generate_code(text, task)
        try:
            result = run_sandboxed_python(code)
        except SandboxUnavailable as exc:
            raise WorkerError(str(exc)) from exc
        except SandboxError as exc:
            raise WorkerError(f"sandbox failed: {exc}") from exc
        out = result.get("stdout") or "(no output)"
        lines = [f"Python (sandboxed):", f"Output: {out.strip()[:2000]}"]
        display, meta = format_calc_worker_reply(lines=lines, code=code, sandbox=True)
        meta["message_kind"] = "calc"
        return {"summary": display, "text": display, "stdout": out, "code": code, "message_meta": meta}

    def _generate_code(self, text: str, task: Task) -> str:
        prompt = f"Task: {task.description}\nUser request: {text}"
        resp = self.llm.chat(
            [{"role": "system", "content": _CODE_SYSTEM}, {"role": "user", "content": prompt}],
            options={"temperature": 0.1, "num_predict": 400},
        )
        code = (resp.get("content") or "").strip()
        code = re.sub(r"^```(?:python)?\s*", "", code)
        code = re.sub(r"\s*```$", "", code)
        if not code:
            raise WorkerError("model returned empty code")
        return code

    @staticmethod
    def handles(text: str) -> bool:
        return bool(_CALC_HINT.search(text))


def _needs_sandbox(text: str) -> bool:
    low = text.lower()
    if any(k in low for k in ("simulate", "monte carlo", "standard deviation", "regression", "histogram")):
        return True
    if re.search(r"(?is)\bfor\s+.+\s+in\s+range\b", text):
        return True
    return False