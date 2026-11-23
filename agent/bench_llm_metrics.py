"""Streaming LLM benchmark timing and token accounting (Ollama + OpenAI SSE)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Iterator

BENCH_PROMPT = (
    "Write a coherent paragraph of about 150 words describing how a small local voice "
    "assistant routes user speech through wake word detection, speech-to-text, and a "
    "language model before speaking the reply. Use complete sentences only."
)
BENCH_MAX_TOKENS = 200
BENCH_TEMPERATURE = 0.0
MIN_COMPLETION_TOKENS = 50
WARMUP_RUNS = 1
MEASURED_RUNS = 5


def estimate_tokens(text: str) -> int:
    """Rough token count (~4 chars or whitespace splits)."""
    text = (text or "").strip()
    if not text:
        return 0
    words = re.findall(r"\S+", text)
    by_words = len(words)
    by_chars = max(1, len(text) // 4)
    return max(by_words, by_chars)


def _percentile(vals: list[float], pct: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * pct
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def p50(vals: list[float]) -> float:
    return _percentile(vals, 0.5)


def p95(vals: list[float]) -> float:
    return _percentile(vals, 0.95)


@dataclass
class StreamTimingResult:
    ttft_ms: float
    total_ms: float
    output_text: str
    completion_tokens: int
    completion_tokens_source: str  # server | estimated
    wall_tokens_per_s: float
    server_tokens_per_s: float | None
    prompt_eval_ms: float | None
    valid: bool
    invalid_reason: str | None = None


def summarize_ollama_from_events(
    events: list[tuple[float, dict[str, Any]]],
    *,
    min_tokens: int = MIN_COMPLETION_TOKENS,
) -> StreamTimingResult:
    """Replay timed Ollama stream chunks (t seconds since stream start)."""
    text_parts: list[str] = []
    final: dict[str, Any] = {}
    first_t: float | None = None
    end_t = events[-1][0] if events else 0.0
    for t, part in events:
        content = (part.get("message") or {}).get("content") or ""
        if content and first_t is None:
            first_t = t
        if content:
            text_parts.append(content)
        if part.get("done"):
            final = part
            end_t = t
    text = "".join(text_parts)
    ttft_ms = (first_t if first_t is not None else end_t) * 1000
    total_ms = end_t * 1000
    eval_count = final.get("eval_count")
    eval_duration_ns = int(final.get("eval_duration") or 0)
    prompt_eval_ns = int(final.get("prompt_eval_duration") or 0)
    if eval_count is not None and int(eval_count) > 0:
        tokens = int(eval_count)
        src = "server"
    else:
        tokens = estimate_tokens(text)
        src = "estimated"
    wall_tps = tokens / (total_ms / 1000) if total_ms > 0 else 0.0
    server_tps = None
    if eval_count and eval_duration_ns > 0:
        server_tps = int(eval_count) / (eval_duration_ns / 1e9)
    prompt_ms = (prompt_eval_ns / 1e6) if prompt_eval_ns > 0 else None
    invalid_reason = None
    if tokens < min_tokens:
        invalid_reason = f"completion_tokens {tokens} < {min_tokens}"
    return StreamTimingResult(
        ttft_ms=ttft_ms,
        total_ms=total_ms,
        output_text=text,
        completion_tokens=tokens,
        completion_tokens_source=src,
        wall_tokens_per_s=wall_tps,
        server_tokens_per_s=server_tps,
        prompt_eval_ms=prompt_ms,
        valid=invalid_reason is None,
        invalid_reason=invalid_reason,
    )


def consume_ollama_stream(chunks: Iterator[dict[str, Any]]) -> StreamTimingResult:
    t0 = time.perf_counter()
    events: list[tuple[float, dict[str, Any]]] = []
    for part in chunks:
        events.append((time.perf_counter() - t0, part))
    return summarize_ollama_from_events(events)


@dataclass
class OpenAISseEvent:
    content: str = ""
    usage: dict[str, Any] | None = None


def summarize_openai_from_events(
    events: list[tuple[float, OpenAISseEvent]],
    *,
    min_tokens: int = MIN_COMPLETION_TOKENS,
) -> StreamTimingResult:
    text_parts: list[str] = []
    usage: dict[str, Any] | None = None
    first_t: float | None = None
    end_t = events[-1][0] if events else 0.0
    for t, ev in events:
        if ev.content and first_t is None:
            first_t = t
        if ev.content:
            text_parts.append(ev.content)
        if ev.usage:
            usage = ev.usage
        end_t = t
    text = "".join(text_parts)
    ttft_ms = (first_t if first_t is not None else end_t) * 1000
    total_ms = end_t * 1000
    completion = (usage or {}).get("completion_tokens")
    if completion is not None and int(completion) > 0:
        tokens = int(completion)
        src = "server"
    else:
        tokens = estimate_tokens(text)
        src = "estimated"
    wall_tps = tokens / (total_ms / 1000) if total_ms > 0 else 0.0
    invalid_reason = None
    if tokens < min_tokens:
        invalid_reason = f"completion_tokens {tokens} < {min_tokens}"
    return StreamTimingResult(
        ttft_ms=ttft_ms,
        total_ms=total_ms,
        output_text=text,
        completion_tokens=tokens,
        completion_tokens_source=src,
        wall_tokens_per_s=wall_tps,
        server_tokens_per_s=None,
        prompt_eval_ms=None,
        valid=invalid_reason is None,
        invalid_reason=invalid_reason,
    )


def aggregate_stream_results(samples: list[StreamTimingResult]) -> dict[str, Any]:
    valid = [s for s in samples if s.valid]
    invalid = [s for s in samples if not s.valid]
    if not valid:
        reasons = [s.invalid_reason for s in samples if s.invalid_reason]
        return {
            "valid": False,
            "invalid_reason": reasons[0] if reasons else "no valid samples",
            "samples": len(samples),
            "invalid_samples": len(invalid),
        }
    ttfts = [s.ttft_ms for s in valid]
    totals = [s.total_ms for s in valid]
    wall_tps = [s.wall_tokens_per_s for s in valid]
    server_tps = [s.server_tokens_per_s for s in valid if s.server_tokens_per_s is not None]
    tokens = [s.completion_tokens for s in valid]
    token_sources = {s.completion_tokens_source for s in valid}
    out: dict[str, Any] = {
        "valid": True,
        "samples": len(samples),
        "valid_samples": len(valid),
        "invalid_samples": len(invalid),
        "ttft_ms_p50": p50(ttfts),
        "ttft_ms_p95": p95(ttfts),
        "total_ms_p50": p50(totals),
        "total_ms_p95": p95(totals),
        "wall_tokens_per_s_p50": p50(wall_tps),
        "wall_tokens_per_s_p95": p95(wall_tps),
        "completion_tokens_p50": p50([float(x) for x in tokens]),
        "completion_tokens_source": "server" if token_sources == {"server"} else "estimated",
    }
    if token_sources != {"server"}:
        out["completion_tokens_source"] = (
            "mixed" if len(token_sources) > 1 else next(iter(token_sources), "estimated")
        )
    if server_tps:
        out["server_tokens_per_s_p50"] = p50(server_tps)
        out["server_tokens_per_s_p95"] = p95(server_tps)
    prompt = [s.prompt_eval_ms for s in valid if s.prompt_eval_ms is not None]
    if prompt:
        out["prompt_eval_ms_p50"] = p50(prompt)
    return out