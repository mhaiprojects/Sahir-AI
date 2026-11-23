#!/usr/bin/env python3
"""Print prompt token counts for orchestrator, supervisor, and Tier-2 router.

Uses Ollama ``prompt_eval_count`` (chat with num_predict=1) when reachable; otherwise char/4 estimate.
Orchestrator measurement includes system text + tool schemas (first planning call, empty context).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ollama  # noqa: E402

from agent.clickup import ClickUpWorker  # noqa: E402
from agent.models import think_kwargs  # noqa: E402
from agent.orchestrator import Orchestrator  # noqa: E402
from agent.prompt_metrics import (  # noqa: E402
    orchestrator_bundle,
    supervisor_bundle,
    tier2_bundle,
)
from agent.workers import CodeWorker, ResearchWorker  # noqa: E402
from config import Settings  # noqa: E402


class _StubDispatcher:
    def __init__(self) -> None:
        self.workers = {w.type: w for w in (ResearchWorker, CodeWorker, ClickUpWorker)}

    def catalog(self) -> list[dict[str, str]]:
        return [{"type": w.type, "description": w.description} for w in self.workers.values()]


def _ollama_prompt_tokens(
    client: ollama.Client,
    model: str,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        resp = client.chat(
            model=model,
            messages=messages,
            tools=tools,
            options={"num_predict": 1, "temperature": 0},
            **think_kwargs(model),
        )
    except Exception as exc:
        return {"error": str(exc), "ms": round((time.monotonic() - t0) * 1000)}
    pe = int(resp.get("prompt_eval_count") or 0)
    ev = int(resp.get("eval_count") or 0)
    return {
        "prompt_eval_count": pe,
        "eval_count": ev,
        "ms": round((time.monotonic() - t0) * 1000),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="", help="Ollama chat model (default: Settings.ollama_model)")
    ap.add_argument("--json", action="store_true", help="Print machine-readable JSON only")
    args = ap.parse_args()

    settings = Settings.from_env()
    model = args.model or settings.ollama_model
    client = ollama.Client(host=settings.ollama_host)
    orch = Orchestrator(settings, _StubDispatcher(), client=client)

    ob = orchestrator_bundle(orch)
    sb = supervisor_bundle()
    tb = tier2_bundle()

    orch_msgs = [{"role": "system", "content": ob["system"]}, {"role": "user", "content": "hi"}]
    sup_msgs = [{"role": "system", "content": sb["system"]}, {"role": "user", "content": "list chats"}]
    t2_msgs = tb["messages"]

    out: dict[str, Any] = {"model": model, "estimate": {}, "ollama": {}}
    out["estimate"] = {
        "orchestrator": ob["estimate_tokens"],
        "supervisor": sb["estimate_tokens"],
        "tier2_router": tb["estimate_tokens"],
    }

    for key, messages, tools in (
        ("orchestrator", orch_msgs, ob["tools"]),
        ("supervisor", sup_msgs, None),
        ("tier2_router", t2_msgs, None),
    ):
        out["ollama"][key] = _ollama_prompt_tokens(client, model, messages, tools=tools)

    if args.json:
        print(json.dumps(out, indent=2))
        return 0

    print(f"Model: {model}\n")
    for name in ("orchestrator", "supervisor", "tier2_router"):
        est = out["estimate"][name]
        o = out["ollama"].get(name, {})
        pe = o.get("prompt_eval_count", "—")
        ms = o.get("ms", "—")
        err = o.get("error")
        line = f"  {name:16}  estimate={est:4}  prompt_eval_count={pe!s:>6}  first_call_ms={ms}"
        if err:
            line += f"  (ollama error: {err})"
        print(line)
    print(f"\nOrchestrator: system {ob['system_chars']} chars, tools JSON {ob['tools_chars']} chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())