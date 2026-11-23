"""Design / diagram worker — Mermaid source generation + validation."""

from __future__ import annotations

import logging
import re

from agent.delegation import Task, WorkerError
from agent.llm import ChatLLM
from agent.mermaid_validate import normalize_mermaid_source, validate_mermaid

logger = logging.getLogger(__name__)

_DESIGN_EXCLUDE = re.compile(
    r"(?is)\b(python|typescript|javascript|implement|refactor|unit test|pytest|cursor|repo|git)\b"
)

_MERMAID_SYSTEM = """You produce valid Mermaid diagram source only.
Supported: flowchart, sequenceDiagram, mindmap, classDiagram, stateDiagram, erDiagram.
Put optional titles in YAML frontmatter (``---`` / ``title: …`` / ``---``), not a bare ``title …`` line in the diagram body. No markdown fences — output Mermaid source only."""

_REPAIR_SYSTEM = """Fix the Mermaid syntax errors described. Return only corrected Mermaid source, no fences."""


class DesignWorker:
    type = "design"
    description = "Mermaid diagrams: flowcharts, sequence, mind maps, architecture."
    timeout_s = 120.0
    max_retries = 0

    def __init__(self, llm: ChatLLM) -> None:
        self.llm = llm

    def run(self, task: Task) -> dict:
        text = (task.context.get("query") or task.description or "").strip()
        if not text:
            raise WorkerError("empty design task")
        if _DESIGN_EXCLUDE.search(text):
            raise WorkerError("request looks like a code task, not a diagram")
        source = normalize_mermaid_source(self._generate(text))
        check = validate_mermaid(source)
        if not check.ok:
            source = normalize_mermaid_source(self._repair(source, check.error, text))
            check = validate_mermaid(source)
        if not check.ok:
            raise WorkerError(f"diagram invalid after repair: {check.error}")
        title = check.title or _infer_title(text)
        steps = check.step_count
        tts = f"Here's the diagram: {title}, with {steps} step{'s' if steps != 1 else ''}."
        display = f"**{title}**\n\n```mermaid\n{source}\n```"
        meta = {
            "kind": "diagram",
            "mermaid": source,
            "diagram_title": title,
            "diagram_steps": steps,
            "diagram_type": check.diagram_type,
            "tts_text": tts,
        }
        return {
            "summary": f"Mermaid {check.diagram_type}: {title}",
            "text": display,
            "mermaid": source,
            "message_meta": meta,
        }

    def _generate(self, text: str) -> str:
        resp = self.llm.chat(
            [{"role": "system", "content": _MERMAID_SYSTEM}, {"role": "user", "content": text}],
            options={"temperature": 0.25, "num_predict": 900},
        )
        src = _strip_fences(resp.get("content") or "")
        if not src:
            raise WorkerError("model returned empty diagram")
        return src

    def _repair(self, source: str, error: str, request: str) -> str:
        prompt = f"Request: {request}\n\nDiagram:\n{source}\n\nValidation error: {error}"
        resp = self.llm.chat(
            [{"role": "system", "content": _REPAIR_SYSTEM}, {"role": "user", "content": prompt}],
            options={"temperature": 0.1, "num_predict": 900},
        )
        return _strip_fences(resp.get("content") or "")


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^```(?:mermaid)?\s*", "", t, flags=re.I)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _infer_title(text: str) -> str:
    t = re.sub(r"(?is)^(please\s+)?(draw|diagram|flowchart|sketch)\s+(of\s+)?", "", text).strip()
    return (t[:80] or "Diagram").strip().rstrip(".")


def heuristic_design_intent(text: str) -> bool:
    if _DESIGN_EXCLUDE.search(text):
        return False
    return bool(
        re.search(
            r"(?is)\b(draw|diagram|flowchart|sequence diagram|mind map|architecture diagram)\b",
            text,
        )
    )