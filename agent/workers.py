"""Layer 4 — workers. Each does one thing well and returns a JSON-able output dict."""

from __future__ import annotations

import logging
import shutil
from typing import Any

from agent.delegation import Task, WorkerError
from agent.llm import ChatLLM
from agent.rag import RagService
from config import Settings
from tools.cursor_cli import run_cursor_agent

logger = logging.getLogger(__name__)

RESEARCH_SYSTEM = """You are a research summarizer working for a voice assistant orchestrator.
Using ONLY the provided local document excerpts, remembered facts, earlier worker results and
conversation context, write a concise, accurate summary that answers the task.
Format: 2-4 sentence summary, then up to 5 short bullet points of key facts, citing file names in
[brackets] where used. If the material does not contain the answer, say so plainly."""


def _context_text(ctx: dict[str, Any]) -> str:
    parts: list[str] = []
    hist = ctx.get("history") or []
    if hist:
        parts.append("Conversation so far:")
        parts += [f"{'User' if h['role'] == 'user' else 'Assistant'}: {h['content'][:600]}" for h in hist[-8:]]
    mems = ctx.get("memories") or []
    if mems:
        parts.append("Known facts about the user:")
        parts += [f"- {m}" for m in mems]
    prev = ctx.get("previous_results") or []
    if prev:
        parts.append("Results from earlier workers in this request:")
        for p in prev:
            parts.append(f"- ({p.get('type')}) {str(p.get('summary') or p.get('output'))[:1500]}")
    return "\n".join(parts)


class ResearchWorker:
    """Answers a question from local RAG documents (+memory/context) with a cited summary."""

    type = "research"
    description = "Local docs/notes (RAG); summarize or 'my files say'."
    timeout_s = 180.0
    max_retries = 1

    def __init__(self, rag: RagService, llm: ChatLLM, k: int = 6) -> None:
        self.rag = rag
        self.llm = llm
        self.k = k

    def run(self, task: Task) -> dict[str, Any]:
        query = task.context.get("query") or task.description
        hits = self.rag.search(query, k=self.k, min_score=0.2)
        excerpts = "\n\n".join(f"[{h.filename} #{h.idx}] {h.content[:1500]}" for h in hits)
        prompt = (f"Task: {task.description}\n\n{_context_text(task.context)}\n\n"
                  f"Document excerpts:\n{excerpts or '(no matching local documents)'}")
        resp = self.llm.chat(
            [{"role": "system", "content": RESEARCH_SYSTEM}, {"role": "user", "content": prompt}],
            options={"temperature": 0.2, "num_predict": 450},
        )
        summary = (resp.get("content") or "").strip()
        if not summary:
            raise RuntimeError("LLM returned an empty summary")
        return {
            "summary": summary,
            "sources": [{"filename": h.filename, "idx": h.idx, "score": round(h.score, 3)} for h in hits],
        }


class CodeWorker:
    """Delegates coding / repo tasks to the Cursor CLI (cursor-agent) in the configured workspace."""

    type = "code"
    description = "Cursor CLI: code, repo, workspace files."
    max_retries = 0

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.timeout_s = float(settings.cursor_timeout_seconds) + 30

    def run(self, task: Task) -> dict[str, Any]:
        if shutil.which(self.settings.cursor_agent_bin) is None:
            raise WorkerError("cursor-agent is not installed / not on PATH")
        ctx_text = _context_text(task.context)
        prompt = f"{ctx_text}\n\nTask: {task.description}" if ctx_text else task.description
        res = run_cursor_agent(
            prompt,
            workspace=task.context.get("workspace") or self.settings.cursor_workspace,
            model=self.settings.cursor_model,
            agent_bin=self.settings.cursor_agent_bin,
            api_key=self.settings.cursor_api_key,
            timeout_seconds=self.settings.cursor_timeout_seconds,
            plan_mode=bool(task.context.get("plan_mode", False)),
        )
        if res.returncode != 0:
            raise WorkerError(f"cursor-agent exit {res.returncode}: {res.text[:300]}")
        return {"text": res.text, "summary": res.text[:1500], "exit_code": res.returncode}
