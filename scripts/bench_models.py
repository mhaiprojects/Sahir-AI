#!/usr/bin/env python3
"""Benchmark Ollama chat models for the voice assistant.

For each model it measures:
  * cold load time (all chat models ejected first; embedding models are left alone)
  * time to first token and tokens/second for a short spoken reply (streamed)
  * end-to-end decision latency: wall time of the real orchestrator decision call
    (same system prompt, tool schema and options as agent/orchestrator.py). For a
    direct answer this call already contains the spoken reply text.
  * action-selection reliability against scripts/bench_prompts.json: % correct action and
    % valid tool/action JSON (agent.models.tool_calls_invalid). "effective" accuracy also
    applies the orchestrator's production keyword heuristic.

No tools are executed: the model's decision is only inspected.

Usage:
  venv/bin/python scripts/bench_models.py                      # all installed chat models
  venv/bin/python scripts/bench_models.py --models qwen3:0.6b,llama3.1:8b --quick
Writes data/bench/<timestamp>.json, latest.json and latest.md.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ollama  # noqa: E402

from agent.clickup import ClickUpWorker  # noqa: E402
from agent.models import ModelManager, _EMBED_RE, think_kwargs, tool_calls_invalid  # noqa: E402
from agent.orchestrator import Orchestrator  # noqa: E402
from agent.workers import CodeWorker, ResearchWorker  # noqa: E402
from config import Settings  # noqa: E402

REPLY_SYSTEM = ("You are a helpful local voice assistant. Answer concisely in 1-2 short sentences "
                "suitable for speech.")
REPLY_PROMPTS = [
    "Explain what a solar panel does.",
    "Give me a fun fact about octopuses.",
    "What should I cook for a quick dinner?",
    "Why is the sky blue?",
    "Suggest a name for a black cat.",
]


class _StubDispatcher:
    """Exposes the real worker catalog without instantiating workers."""

    def __init__(self) -> None:
        self.workers = {w.type: w for w in (ResearchWorker, CodeWorker, ClickUpWorker)}

    def catalog(self) -> list[dict[str, str]]:
        return [{"type": w.type, "description": w.description} for w in self.workers.values()]


def _msg(resp: Any) -> dict[str, Any]:
    return resp.get("message", {}) if isinstance(resp, dict) else resp.message.model_dump()


def _decisions(msg: dict[str, Any]) -> list[str]:
    out = []
    for c in msg.get("tool_calls") or []:
        fn = c.get("function", {})
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        name = fn.get("name", "")
        out.append(f"delegate:{args.get('type')}" if name == "delegate" else name)
    return out or ["answer"]  # plain text content is treated as a direct answer by the orchestrator


def _correct(expect: str, decisions: list[str]) -> bool:
    if expect == "answer":
        return all(d == "answer" for d in decisions)
    return expect in decisions


def _pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def _stats(xs: list[float]) -> dict[str, float]:
    if not xs:
        return {}
    xs = sorted(xs)
    return {"mean": round(statistics.fmean(xs), 1), "p50": round(xs[len(xs) // 2], 1),
            "p90": round(xs[min(len(xs) - 1, int(len(xs) * 0.9))], 1), "max": round(xs[-1], 1)}


def eject_all(mgr: ModelManager) -> None:
    for m in mgr.running():
        name = m.get("name", "")
        if not _EMBED_RE.search(name):
            mgr.eject(name)
    for _ in range(30):
        if not [m for m in mgr.running() if not _EMBED_RE.search(m.get("name", ""))]:
            return
        time.sleep(0.5)


def bench_model(model: str, client: ollama.Client, mgr: ModelManager, orch: Orchestrator,
                fixture: list[dict[str, str]], reply_prompts: list[str], progress: Any) -> dict[str, Any]:
    eject_all(mgr)
    t0 = time.monotonic()
    client.generate(model=model, prompt="", keep_alive="30m")
    load_s = time.monotonic() - t0
    ps = next((m for m in mgr.running() if m.get("name") == model), {})
    size, vram = int(ps.get("size") or 0), int(ps.get("size_vram") or 0)

    # Streaming reply metrics
    ttft, tps, reply_ms = [], [], []
    for p in reply_prompts:
        start = time.monotonic()
        first = None
        last: Any = None
        for part in client.chat(model=model, stream=True, options={"temperature": 0.5, "num_predict": 80},
                                messages=[{"role": "system", "content": REPLY_SYSTEM},
                                          {"role": "user", "content": p}], **think_kwargs(model)):
            if first is None and (part.get("message", {}) or {}).get("content"):
                first = time.monotonic()
            last = part
        end = time.monotonic()
        if first is not None:
            ttft.append((first - start) * 1000)
        reply_ms.append((end - start) * 1000)
        ec, ed = (last.get("eval_count") or 0), (last.get("eval_duration") or 0)
        if ec and ed:
            tps.append(ec / (ed / 1e9))
        progress(f"{model}: reply {len(reply_ms)}/{len(reply_prompts)}")

    # Orchestrator decisions
    tools = orch._tools()
    system = orch._system("")
    rows = []
    for i, item in enumerate(fixture, 1):
        start = time.monotonic()
        err = None
        try:
            resp = client.chat(model=model, messages=[{"role": "system", "content": system},
                                                      {"role": "user", "content": item["text"]}],
                               tools=tools, options={"temperature": 0.2, "num_predict": 300}, **think_kwargs(model))
            msg = _msg(resp)
        except Exception as exc:  # a model that rejects tools counts as invalid
            msg, err = {}, str(exc)
        ms = (time.monotonic() - start) * 1000
        invalid = err or tool_calls_invalid(msg, tools)
        decisions = _decisions(msg) if not err else ["error"]
        effective = decisions
        if decisions and all(d == "answer" for d in decisions):
            forced = orch._heuristic_delegation(item["text"])
            if forced:
                effective = [f"delegate:{f['type']}" for f in forced]
        rows.append({"text": item["text"], "expect": item["expect"], "decisions": decisions,
                     "effective": effective, "valid": not invalid, "invalid_reason": invalid,
                     "correct": _correct(item["expect"], decisions) and not invalid,
                     "effective_correct": _correct(item["expect"], effective), "ms": round(ms),
                     "content": (msg.get("content") or "")[:160]})
        progress(f"{model}: decision {i}/{len(fixture)}")

    n = len(rows)
    return {
        "model": model, "load_s": round(load_s, 2), "size": size, "size_vram": vram,
        "gpu_percent": round(100 * vram / size) if size else 0,
        "ttft_ms": _stats(ttft), "tokens_per_s": _stats(tps), "reply_ms": _stats(reply_ms),
        "decision_ms": _stats([r["ms"] for r in rows]),
        "valid_json_pct": _pct(sum(r["valid"] for r in rows), n),
        "correct_pct": _pct(sum(r["correct"] for r in rows), n),
        "effective_correct_pct": _pct(sum(r["effective_correct"] for r in rows), n),
        "n_prompts": n, "rows": rows,
    }


def to_markdown(run: dict[str, Any]) -> str:
    lines = [f"# Model benchmark — {run['started']}", "",
             f"Prompts: {run['n_prompts']} decisions, {run['n_reply_prompts']} streamed replies. "
             "Times in ms unless noted; p50 shown.", "",
             "| Model | Load s | GPU % | TTFT | tok/s | Reply | Decision p50 | Decision p90 | Valid JSON % | Correct % | Correct w/ heuristic % |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in run["results"]:
        if "error" in r:
            lines.append(f"| {r['model']} | error: {r['error'][:60]} |||||||||")
            continue
        lines.append(f"| {r['model']} | {r['load_s']} | {r['gpu_percent']} | {r['ttft_ms'].get('p50', '-')} | "
                     f"{r['tokens_per_s'].get('p50', '-')} | {r['reply_ms'].get('p50', '-')} | "
                     f"{r['decision_ms'].get('p50', '-')} | {r['decision_ms'].get('p90', '-')} | "
                     f"{r['valid_json_pct']} | {r['correct_pct']} | {r['effective_correct_pct']} |")
    lines += ["", "## Misses", ""]
    for r in run["results"]:
        for row in r.get("rows", []):
            if not row["correct"]:
                lines.append(f"- **{r['model']}** — \"{row['text']}\" expected `{row['expect']}`, got "
                             f"`{','.join(row['decisions'])}`" + (f" (invalid: {row['invalid_reason']})" if not row["valid"] else ""))
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default="", help="comma-separated model tags (default: all installed chat models)")
    ap.add_argument("--out", default=str(ROOT / "data" / "bench"))
    ap.add_argument("--fixture", default=str(ROOT / "scripts" / "bench_prompts.json"))
    ap.add_argument("--quick", action="store_true", help="8 decision prompts, 2 replies")
    args = ap.parse_args()

    settings = Settings.from_env()
    mgr = ModelManager(settings, state_path=Path(args.out) / ".bench_state_unused.json")
    client = ollama.Client(host=settings.ollama_host)
    orch = Orchestrator(settings, _StubDispatcher(), client=client)
    fixture = json.loads(Path(args.fixture).read_text())
    reply_prompts = REPLY_PROMPTS
    if args.quick:
        fixture, reply_prompts = fixture[::3], REPLY_PROMPTS[:2]
    models = [m for m in args.models.split(",") if m] or mgr.chat_model_names()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    before = [m.get("name") for m in mgr.running()]

    def progress(msg: str) -> None:
        print(msg, flush=True)
        (out / "progress.json").write_text(json.dumps({"message": msg, "at": time.time()}))

    run = {"started": time.strftime("%Y-%m-%d %H:%M:%S %z"), "models": models, "n_prompts": len(fixture),
           "n_reply_prompts": len(reply_prompts), "results": []}
    for model in models:
        try:
            run["results"].append(bench_model(model, client, mgr, orch, fixture, reply_prompts, progress))
        except Exception as exc:
            run["results"].append({"model": model, "error": str(exc)})
            progress(f"{model}: error {exc}")
    eject_all(mgr)
    # Restore the previously loaded active model so the assistant stays warm.
    for name in before:
        if name and not _EMBED_RE.search(name):
            try:
                mgr.load(name)
            except Exception:
                pass
    run["finished"] = time.strftime("%Y-%m-%d %H:%M:%S %z")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (out / f"{stamp}.json").write_text(json.dumps(run, indent=2))
    (out / "latest.json").write_text(json.dumps(run, indent=2))
    (out / "latest.md").write_text(to_markdown(run))
    (out / f"{stamp}.md").write_text(to_markdown(run))
    progress("done")
    print(to_markdown(run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
