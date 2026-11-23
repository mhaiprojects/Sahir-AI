#!/usr/bin/env python3
"""Compare chat backend TTFT and tokens/s (Ollama vs OpenAI-compatible)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.bench_llm_metrics import (
    BENCH_MAX_TOKENS,
    BENCH_PROMPT,
    BENCH_TEMPERATURE,
    MEASURED_RUNS,
    MIN_COMPLETION_TOKENS,
    WARMUP_RUNS,
    StreamTimingResult,
    aggregate_stream_results,
    consume_ollama_stream,
    summarize_openai_from_events,
)
from agent.bench_llm_metrics import OpenAISseEvent
from config import Settings

DEFAULT_MODEL = os.getenv("BENCH_LLM_MODEL", "llama3.2:1b")
OUT_JSON = ROOT / "data" / "bench" / "llm_backend.json"
OUT_MD = ROOT / "docs" / "research" / "llama-cpp-backend.md"


def _bench_messages() -> list[dict[str, str]]:
    return [{"role": "user", "content": BENCH_PROMPT}]


def _bench_ollama(settings: Settings, model: str) -> dict | str:
    import ollama

    from agent.ollama_runtime import ollama_chat_kwargs

    try:
        client = ollama.Client(host=settings.ollama_host)
        client.show(model)
    except Exception as exc:
        return f"SKIP: {exc}"

    kw = ollama_chat_kwargs(
        model,
        options={"num_predict": BENCH_MAX_TOKENS, "temperature": BENCH_TEMPERATURE},
    )
    messages = _bench_messages()
    samples: list[StreamTimingResult] = []

    for run in range(WARMUP_RUNS + MEASURED_RUNS):
        def _chunks() -> Iterator[dict[str, Any]]:
            yield from client.chat(model=model, messages=messages, stream=True, **kw)

        result = consume_ollama_stream(_chunks())
        if run >= WARMUP_RUNS:
            samples.append(result)

    agg = aggregate_stream_results(samples)
    if not agg.get("valid"):
        return {
            "model": model,
            "valid": False,
            "invalid_reason": agg.get("invalid_reason", "benchmark invalid"),
            "samples": agg.get("samples", 0),
            "last_output_chars": len(samples[-1].output_text) if samples else 0,
        }
    return {
        "model": model,
        "valid": True,
        "prompt": BENCH_PROMPT,
        "max_tokens": BENCH_MAX_TOKENS,
        "warmup_runs": WARMUP_RUNS,
        **agg,
    }


def _bench_openai(settings: Settings) -> dict | str:
    from agent.openai_compat import OpenAIChat

    if settings.openai_base_url_error:
        return f"SKIP: {settings.openai_base_url_error}"
    chat = OpenAIChat(
        settings.openai_base_url,
        settings.openai_model or "default",
        api_key=settings.openai_api_key,
        timeout_s=settings.openai_timeout_seconds,
    )
    try:
        chat.list_models()
    except Exception as exc:
        return f"SKIP: {exc}"
    model = settings.openai_model or chat.model
    messages = _bench_messages()
    samples: list[StreamTimingResult] = []

    for run in range(WARMUP_RUNS + MEASURED_RUNS):
        result = _consume_openai_stream(chat, messages)
        if run >= WARMUP_RUNS:
            samples.append(result)

    agg = aggregate_stream_results(samples)
    if not agg.get("valid"):
        return {
            "model": model,
            "valid": False,
            "invalid_reason": agg.get("invalid_reason", "benchmark invalid"),
            **agg,
        }
    return {"model": model, "valid": True, **agg}


def _consume_openai_stream(chat: Any, messages: list[dict[str, str]]) -> StreamTimingResult:
    import time as _time

    from agent.bench_llm_metrics import BENCH_MAX_TOKENS, BENCH_TEMPERATURE

    payload: dict[str, Any] = {
        "model": chat.model,
        "messages": messages,
        "stream": True,
        "temperature": BENCH_TEMPERATURE,
        "max_tokens": BENCH_MAX_TOKENS,
    }
    t0 = _time.perf_counter()
    events: list[tuple[float, OpenAISseEvent]] = []
    resp = chat._request("POST", "/chat/completions", payload, stream=True)
    try:
        while True:
            raw = resp.readline()
            if not raw:
                break
            line = raw.decode(errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data_s = line[5:].strip()
            if data_s == "[DONE]":
                events.append((_time.perf_counter() - t0, OpenAISseEvent()))
                break
            try:
                chunk = json.loads(data_s)
            except json.JSONDecodeError:
                continue
            usage = chunk.get("usage")
            choices = chunk.get("choices") or []
            content = ""
            if choices:
                content = (choices[0].get("delta") or {}).get("content") or ""
            events.append(
                (_time.perf_counter() - t0, OpenAISseEvent(content=content, usage=usage))
            )
    finally:
        if hasattr(resp, "close"):
            resp.close()
    return summarize_openai_from_events(events)


def main() -> int:
    settings = Settings.from_env()
    model = DEFAULT_MODEL
    results: dict[str, Any] = {"generated_at": int(time.time()), "backends": {}}
    oll = _bench_ollama(settings, model)
    results["backends"]["ollama"] = oll if isinstance(oll, dict) else {"skip": oll}
    if os.getenv("LLM_BACKEND", settings.llm_backend).lower() == "openai":
        oai = _bench_openai(settings)
        results["backends"]["openai"] = oai if isinstance(oai, dict) else {"skip": oai}
    else:
        results["backends"]["openai"] = {"skip": "SKIP: LLM_BACKEND is not openai"}

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    _write_md(results, model)
    for name, row in results["backends"].items():
        if isinstance(row, dict) and row.get("skip"):
            print(f"{name}: {row['skip']}")
        elif isinstance(row, dict) and row.get("valid") is False:
            print(f"{name}: INVALID — {row.get('invalid_reason')}")
        elif isinstance(row, dict):
            tok_src = row.get("completion_tokens_source", "?")
            server_tps = row.get("server_tokens_per_s_p50")
            wall = row.get("wall_tokens_per_s_p50", 0)
            extra = f"server_tok/s={server_tps:.1f}" if server_tps else f"wall_tok/s={wall:.1f} ({tok_src})"
            print(
                f"{name}: ttft_p50={row.get('ttft_ms_p50', 0):.1f}ms "
                f"total_p50={row.get('total_ms_p50', 0):.0f}ms {extra} model={row.get('model')}"
            )
        else:
            print(f"{name}: {row}")
    return 0


def _write_md(results: dict, model: str) -> None:
    lines = [
        "# OpenAI-compatible LLM backend (llama-server)",
        "",
        "_Baseline from `scripts/bench_llm_backend.py` on this machine._",
        "",
        "## When it is worth switching",
        "",
        "- Measured Ollama scheduling/load overhead dominates TTFT for your chat model.",
        "- You need a runtime flag Ollama does not expose (e.g. fixed `-ngl`, custom KV, grammar).",
        "- You run **chat only** on llama-server while **Ollama keeps** `nomic-embed-text` and pinned `qwen3:0.6b` router tiers.",
        "",
        "## VRAM on 4 GB (RX 5500)",
        "",
        "Ollama already pins the router (~0.7 GB) and nomic on CPU. A second GPU server for chat",
        "competes for the ~2.5 GB left — **do not run both chat stacks on GPU without Michael's approval**.",
        "",
        "## Build llama-server (Vulkan, user install — no sudo)",
        "",
        "```bash",
        "git clone https://github.com/ggml-org/llama.cpp",
        "cd llama.cpp",
        "cmake -B build -DGGML_VULKAN=1",
        "cmake --build build -j",
        "```",
        "",
        "Example server (after you have a GGUF locally):",
        "",
        "```bash",
        "./build/bin/llama-server -m /path/to/model.gguf --port 8080 -ngl 99 -c 4096",
        "```",
        "",
        "Sahir: `LLM_BACKEND=openai`, `OPENAI_BASE_URL=http://127.0.0.1:8080/v1`, `OPENAI_MODEL=<id from /v1/models>`.",
        "",
        "## Benchmark procedure",
        "",
        "Uses one **warm-up** run and **5** measured streaming samples (~150-word prompt, "
        f"`max_tokens={BENCH_MAX_TOKENS}`, `temperature=0`). Runs are **invalid** if completion "
        f"is under ~{MIN_COMPLETION_TOKENS} tokens.",
        "",
        "```bash",
        "timeout 300 venv/bin/python scripts/bench_llm_backend.py",
        "```",
        "",
        f"Ollama model used for baseline: `{model}`.",
        "",
        "| Backend | TTFT p50 (ms) | Total p50 (ms) | tok/s p50 (server wall) |",
        "|---------|--------------:|---------------:|------------------------:|",
    ]
    for key in ("ollama", "openai"):
        row = results["backends"].get(key) or {}
        if row.get("skip"):
            lines.append(f"| {key} | — | — | {row['skip']} |")
        elif row.get("valid") is False:
            lines.append(f"| {key} | — | — | INVALID: {row.get('invalid_reason', '?')} |")
        else:
            tps = row.get("server_tokens_per_s_p50") or row.get("wall_tokens_per_s_p50", 0)
            label = "server" if row.get("server_tokens_per_s_p50") else row.get("completion_tokens_source", "wall")
            lines.append(
                f"| {key} | {row.get('ttft_ms_p50', 0):.1f} | {row.get('total_ms_p50', 0):.0f} | "
                f"{tps:.1f} ({label}) |"
            )
    oll = results["backends"].get("ollama") or {}
    if oll.get("valid"):
        lines.extend(
            [
                "",
                f"- Completion tokens p50: **{oll.get('completion_tokens_p50', 0):.0f}** "
                f"({oll.get('completion_tokens_source', '?')})",
                f"- TTFT p95: {oll.get('ttft_ms_p95', 0):.1f} ms",
            ]
        )
    lines.extend(["", f"Raw JSON: `{OUT_JSON.relative_to(ROOT)}`.", ""])
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())