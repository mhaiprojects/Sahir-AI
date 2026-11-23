"""Unit tests for LLM streaming benchmark metrics."""

from __future__ import annotations

from agent.bench_llm_metrics import (
    OpenAISseEvent,
    aggregate_stream_results,
    estimate_tokens,
    summarize_ollama_from_events,
    summarize_openai_from_events,
)


def test_estimate_tokens_nonempty() -> None:
    text = " ".join(["word"] * 80)
    assert estimate_tokens(text) >= 50


def test_ollama_stream_timing_and_server_tok_s() -> None:
    """TTFT at first content; total at done; server tok/s from eval_duration."""
    events = [
        (0.020, {"message": {"content": "The"}, "done": False}),
        (0.040, {"message": {"content": " voice"}, "done": False}),
        (0.200, {"message": {"content": " assistant"}, "done": False}),
        (
            2.000,
            {
                "message": {"content": ""},
                "done": True,
                "eval_count": 120,
                "eval_duration": 1_800_000_000,
                "prompt_eval_duration": 50_000_000,
            },
        ),
    ]
    r = summarize_ollama_from_events(events)
    assert r.ttft_ms == 20.0
    assert r.total_ms == 2000.0
    assert r.completion_tokens == 120
    assert r.completion_tokens_source == "server"
    assert r.server_tokens_per_s is not None
    assert abs(r.server_tokens_per_s - 120 / 1.8) < 0.1
    assert r.valid


def test_invalid_when_too_few_tokens() -> None:
    events = [
        (0.01, {"message": {"content": "hi"}, "done": False}),
        (0.05, {"message": {"content": ""}, "done": True, "eval_count": 3, "eval_duration": 10_000_000}),
    ]
    r = summarize_ollama_from_events(events)
    assert not r.valid
    agg = aggregate_stream_results([r, r])
    assert agg["valid"] is False


def test_openai_stream_uses_usage_when_present() -> None:
    events = [
        (0.03, OpenAISseEvent(content="A" * 200)),
        (1.5, OpenAISseEvent(usage={"completion_tokens": 90})),
    ]
    r = summarize_openai_from_events(events)
    assert r.ttft_ms == 30.0
    assert r.total_ms == 1500.0
    assert r.completion_tokens == 90
    assert r.completion_tokens_source == "server"
    assert r.valid


def test_openai_estimated_tokens_labeled() -> None:
    long_text = " ".join(["token"] * 60)
    events = [(0.02, OpenAISseEvent(content=long_text)), (1.0, OpenAISseEvent())]
    r = summarize_openai_from_events(events)
    assert r.completion_tokens_source == "estimated"
    assert r.completion_tokens >= 50
    assert r.valid


def test_aggregate_p50_p95() -> None:
    from agent.bench_llm_metrics import StreamTimingResult

    samples = [
        StreamTimingResult(10, 1000, "x", 80, "server", 80, 75, 5, True),
        StreamTimingResult(20, 2000, "x", 85, "server", 42.5, 40, 5, True),
    ]
    agg = aggregate_stream_results(samples)
    assert agg["valid"]
    assert agg["ttft_ms_p50"] == 15.0
    assert agg["server_tokens_per_s_p50"] > 0