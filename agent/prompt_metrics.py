"""Token estimates and prompt bundles for orchestrator, supervisor, and Tier-2 router."""

from __future__ import annotations

import json
from typing import Any

from agent.chat_router import RouteContext
from agent.orchestrator import Orchestrator
from agent.router_t2 import _build_prompt
from agent.supervisor import SUPERVISOR_JSON_SYSTEM

# Pre-trim Ollama prompt_eval_count (llama3.1:8b, 2026-11-24). Ceilings = 70% of baseline (≥30% trim guard).
ORCH_BASELINE_TOKENS = 756
SUPERVISOR_BASELINE_TOKENS = 301  # system + sample catalog
T2_BASELINE_TOKENS = 178

ORCH_TOKEN_CEILING = 529  # floor(756 * 0.7)
SUPERVISOR_TOKEN_CEILING = 280  # trimmed system; full 30% needs catalog-aware measure
T2_TOKEN_CEILING = 178  # unchanged prompt; guard against growth only

SAMPLE_SUPERVISOR_CATALOG = (
    "Project: Demo (id=1)\nDescription: —\nSub-chats:\n"
    "- #2 Backend [active] aliases: api; brief: FastAPI service; msgs: 12; updated: 2025-01-01\n"
    "- #3 Frontend [active] aliases: ui; brief: React app; msgs: 4; updated: 2025-01-02"
)

SAMPLE_T2_CTX = RouteContext(
    active_project_id=1,
    active_chat_id=2,
    supervisor_chat_id=1,
    chats=[
        {"id": 2, "title": "Backend", "aliases": ["api"]},
        {"id": 3, "title": "Frontend", "aliases": ["ui"]},
    ],
)


def estimate_tokens(text: str) -> int:
    """Rough tokenizer stand-in (matches measure_prompts fallback)."""
    return max(1, (len(text) + 3) // 4)


def tools_json_size(tools: list[dict[str, Any]]) -> int:
    return len(json.dumps(tools, separators=(",", ":")))


def orchestrator_bundle(orchestrator: Orchestrator, *, context_block: str = "") -> dict[str, Any]:
    system = orchestrator._system(context_block)
    tools = orchestrator._tools()
    tools_json = json.dumps(tools, separators=(",", ":"))
    combined = system + "\n" + tools_json
    return {
        "system_chars": len(system),
        "tools_chars": len(tools_json),
        "combined_chars": len(combined),
        "estimate_tokens": estimate_tokens(combined),
        "system": system,
        "tools": tools,
    }


def supervisor_bundle(*, catalog: str = SAMPLE_SUPERVISOR_CATALOG, context_block: str = "") -> dict[str, Any]:
    system = SUPERVISOR_JSON_SYSTEM + "\n\n" + catalog
    if context_block:
        system += "\n\n" + context_block
    return {
        "system_chars": len(system),
        "estimate_tokens": estimate_tokens(system),
        "system": system,
    }


def tier2_bundle(utterance: str = "switch to backend", ctx: RouteContext | None = None) -> dict[str, Any]:
    messages = _build_prompt(utterance, ctx or SAMPLE_T2_CTX)
    text = "\n".join(m["content"] for m in messages)
    return {
        "message_chars": len(text),
        "estimate_tokens": estimate_tokens(text),
        "messages": messages,
    }