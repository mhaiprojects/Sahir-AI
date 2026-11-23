#!/usr/bin/env python3
"""Replay labelled utterances through Tiers 0–2; report accuracy + latency."""

from __future__ import annotations

import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.chat_router import RouteContext, build_route_context, route
from agent.config import AgentConfig
from agent.llm import OllamaEmbedder
from agent.router_t1 import RouterTier1Index, invalidate_static_cache, load_holdout_examples, load_static_examples
from agent.store import AgentStore
from config import Settings


def ollama_ps(host: str) -> str:
    try:
        with urllib.request.urlopen(f"{host.rstrip('/')}/api/ps", timeout=3) as resp:
            data = json.loads(resp.read().decode())
        bits = []
        for m in data.get("models") or []:
            name = m.get("name", "?")
            size = m.get("size_vram", 0)
            total = max(size, 1)
            pct = round(100 * size / total)
            bits.append(f"{name}: {pct}% GPU approx")
        return "; ".join(bits) if bits else "no models loaded"
    except Exception as exc:
        return f"unavailable ({exc})"


def main() -> int:
    settings = Settings.from_env()
    cfg = AgentConfig.from_env()
    emb = OllamaEmbedder(settings.ollama_host, cfg.embed_model)
    RouterTier1Index().ensure_static(emb)
    store = AgentStore(cfg.db_path)
    sup = store.get_project_by_slug("general")
    sup_id = int(sup["supervisor_conversation_id"]) if sup else None
    ctx = build_route_context(store, sup_id)
    ctx.input_type = "voice"
    ctx.tier0_enabled = True
    ctx.tier1_enabled = True
    ctx.tier2_enabled = True
    ctx.embedder = emb

    rows = [(e.text, e.label) for e in load_static_examples()]
    rows += load_holdout_examples()
    tier_counts = {0: 0, 1: 0, 2: 0, "sticky0": 0}
    latencies: list[float] = []
    correct = 0

    for text, gold in rows:
        t0 = time.perf_counter()
        d = route(text, ctx, store=store, max_tier=2)
        latencies.append((time.perf_counter() - t0) * 1000)
        tier_counts[d.tier] = tier_counts.get(d.tier, 0) + 1
        effective = d.label if d.tier > 0 or d.label != "C" else "C"
        if d.payload.get("sticky") and d.label == "C":
            tier_counts["sticky0"] += 1
        if effective == gold:
            correct += 1

    latencies.sort()
    p50 = statistics.median(latencies)
    p90 = latencies[int(len(latencies) * 0.9)] if latencies else 0
    acc = correct / len(rows) if rows else 0
    ps = ollama_ps(settings.ollama_host)

    out = ROOT / "docs/research/router-calibration.md"
    existing = out.read_text(encoding="utf-8") if out.is_file() else ""
    block = f"""
## Tier 2 bench (full router stack)

Run: `scripts/bench_router.py` on {time.strftime("%Y-%m-%d %H:%M %Z")}.

| Metric | Value |
|---|---|
| Utterances | {len(rows)} |
| End-to-end accuracy (effective label) | {acc * 100:.1f}% |
| Latency p50 | {p50:.1f} ms |
| Latency p90 | {p90:.1f} ms |
| Tier usage | {json.dumps(tier_counts)} |
| Ollama `/api/ps` | {ps} |

Notes: Tier 2 uses `ROUTER_MODEL` with one-letter classify; embeddings stay on CPU when `EMBED_ON_CPU=true`.
"""
    if "## Tier 2 bench" in existing:
        head, _, _ = existing.partition("## Tier 2 bench")
        out.write_text(head.rstrip() + "\n" + block, encoding="utf-8")
    else:
        out.write_text(existing.rstrip() + "\n" + block, encoding="utf-8")
    print(f"accuracy={acc*100:.1f}% p50={p50:.1f}ms p90={p90:.1f}ms")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())