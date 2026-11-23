#!/usr/bin/env python3
"""Benchmark live (partial) STT backends: Moonshine vs faster-whisper tiny."""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_JSON = ROOT / "data" / "bench" / "live_stt.json"
OUT_MD = ROOT / "docs" / "research" / "live-stt-moonshine.md"

UTTERANCES = (
    ("hello world", "Hello world."),
    ("two plus two", "What is two plus two?"),
    ("quick brown fox", "The quick brown fox jumps over the lazy dog."),
)


def _wer(ref: str, hyp: str) -> float:
    ref_w = ref.lower().split()
    hyp_w = hyp.lower().split()
    if not ref_w:
        return 0.0 if not hyp_w else 1.0
    d = [[0] * (len(hyp_w) + 1) for _ in range(len(ref_w) + 1)]
    for i in range(len(ref_w) + 1):
        d[i][0] = i
    for j in range(len(hyp_w) + 1):
        d[0][j] = j
    for i in range(1, len(ref_w) + 1):
        for j in range(1, len(hyp_w) + 1):
            cost = 0 if ref_w[i - 1] == hyp_w[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
    return d[len(ref_w)][len(hyp_w)] / len(ref_w)


@dataclass
class BenchRow:
    backend: str
    utterance: str
    latencies_ms: list[float]
    wers: list[float]
    last_text: str


def _synth_pcm(text: str, pipeline) -> np.ndarray:
    audio, sr = pipeline.synthesize_float(text)
    if sr != 16000 and audio.size:
        dst_len = int(len(audio) * 16000 / sr)
        src_x = np.linspace(0, 1, num=len(audio), endpoint=False)
        dst_x = np.linspace(0, 1, num=dst_len, endpoint=False)
        audio = np.interp(dst_x, src_x, audio).astype(np.float32)
    return audio.astype(np.float32)


def _partial_latencies(backend, audio: np.ndarray, *, step_s: float = 0.35) -> tuple[list[float], str]:
    step = int(16000 * step_s)
    lats: list[float] = []
    text = ""
    for end in range(step, len(audio) + 1, step):
        chunk = audio[:end]
        if len(chunk) < step // 2:
            continue
        t0 = time.perf_counter()
        text = backend.transcribe_partial(chunk)
        lats.append((time.perf_counter() - t0) * 1000)
    return lats, text


def bench_backend(name: str, backend, pipeline) -> list[BenchRow]:
    rows: list[BenchRow] = []
    for key, spoken in UTTERANCES:
        pcm = _synth_pcm(spoken, pipeline)
        if pcm.size < 8000:
            continue
        lats, hyp = _partial_latencies(backend, pcm)
        rows.append(
            BenchRow(
                backend=name,
                utterance=key,
                latencies_ms=lats,
                wers=[_wer(spoken, hyp)],
                last_text=hyp,
            )
        )
    return rows


def main() -> int:
    from audio_pipeline import SpeechPipeline
    from config import Settings
    from stt_live import MoonshineLiveBackend, WhisperLiveBackend, moonshine_model_ready

    settings = Settings.from_env()
    pipeline = SpeechPipeline(settings)
    backends: list[tuple[str, object]] = []
    if moonshine_model_ready():
        backends.append(("moonshine", MoonshineLiveBackend(settings)))
    os.environ["WEB_LIVE_STT_BACKEND"] = "whisper"
    backends.append(("whisper", WhisperLiveBackend(pipeline)))

    all_rows: list[BenchRow] = []
    for name, backend in backends:
        all_rows.extend(bench_backend(name, backend, pipeline))

    summary: dict[str, object] = {"backends": {}, "rows": []}
    for row in all_rows:
        summary["rows"].append(
            {
                "backend": row.backend,
                "utterance": row.utterance,
                "latency_ms_p50": statistics.median(row.latencies_ms) if row.latencies_ms else None,
                "latency_ms_p95": (
                    sorted(row.latencies_ms)[int(0.95 * (len(row.latencies_ms) - 1))]
                    if len(row.latencies_ms) > 1
                    else (row.latencies_ms[0] if row.latencies_ms else None)
                ),
                "wer": row.wers[0] if row.wers else None,
                "hypothesis": row.last_text,
            }
        )
        b = summary["backends"].setdefault(row.backend, {"latencies_ms": [], "wers": []})
        b["latencies_ms"].extend(row.latencies_ms)
        b["wers"].extend(row.wers)

    for name, agg in summary["backends"].items():
        lats = agg["latencies_ms"]
        wers = agg["wers"]
        agg["latency_ms_p50"] = statistics.median(lats) if lats else None
        agg["latency_ms_p95"] = sorted(lats)[int(0.95 * (len(lats) - 1))] if len(lats) > 1 else lats[0] if lats else None
        agg["wer_mean"] = statistics.mean(wers) if wers else None

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    moon = summary["backends"].get("moonshine", {})
    wh = summary["backends"].get("whisper", {})
    default = "moonshine" if moonshine_model_ready() else "whisper"
    rec = default
    if moon and wh:
        if (moon.get("wer_mean") or 1) > (wh.get("wer_mean") or 0) + 0.05:
            rec = "whisper"
        elif (moon.get("latency_ms_p95") or 1e9) > (wh.get("latency_ms_p95") or 0) * 1.25:
            rec = "whisper"

    lines = [
        "# Live STT: Moonshine vs Whisper (partial tier)",
        "",
        f"_Generated by `scripts/bench_live_stt.py` on this machine._",
        "",
        "## Summary",
        "",
        f"| Backend | p50 latency (ms) | p95 latency (ms) | mean WER |",
        f"|---------|------------------:|-----------------:|---------:|",
    ]
    for name in ("moonshine", "whisper"):
        agg = summary["backends"].get(name)
        if not agg:
            continue
        lines.append(
            f"| {name} | {agg.get('latency_ms_p50', '—'):.1f} | {agg.get('latency_ms_p95', '—'):.1f} | {agg.get('wer_mean', '—'):.3f} |"
            if isinstance(agg.get("latency_ms_p50"), (int, float))
            else f"| {name} | — | — | — |"
        )
    lines.extend(
        [
            "",
            f"**Recommended default (this run):** `{rec}` (runtime still uses env/auto via `WEB_LIVE_STT_BACKEND`).",
            "",
            "Raw JSON: `data/bench/live_stt.json`.",
            "",
            "Moonshine archive: see `models/README.md`. Final transcript tier remains faster-whisper (`WHISPER_FINAL_MODEL`).",
        ]
    )
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(OUT_JSON), "markdown": str(OUT_MD), "recommended": rec}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())