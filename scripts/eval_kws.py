#!/usr/bin/env python3
"""Evaluate sherpa-onnx KWS: bundled reference clips, Piper/Kokoro wake phrases, RTF."""

from __future__ import annotations

import json
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import Settings
from wake_kws import (
    KwsWakeSpotter,
    build_kws_spotter,
    kws_model_dir,
    kws_model_ready,
    parse_wake_phrases,
    pcm_to_float32_mono,
    resample_float_mono,
)

OUT_JSON = ROOT / "data" / "bench" / "kws_eval.json"
OUT_MD = ROOT / "docs" / "research" / "kws-wake-phrases.md"

NEGATIVE_UTTERANCES = (
    "The quick brown fox jumps over the lazy dog.",
    "Please summarize the meeting notes from yesterday afternoon.",
    "Switch to the supervisor and describe every active chat.",
    "What is two plus two?",
    "Stop speaking and pause the music.",
    "Open the settings page and check the microphone status.",
    "Tell me about Australian animals in one paragraph.",
    "Add unit tests for the login endpoint.",
    "Send the latest report to the team by email.",
    "The weather in Melbourne is fine today.",
    "Play some relaxing music while I work.",
    "How do I configure HTTPS for the web interface?",
    "After early nightfall the yellow lamps would light up here and there the squalid quarter of the brothels.",
    "God as a direct consequence of the sin which man thus punished had given her a lovely child.",
    "The supervisor architecture document lists memory goals and policy enforcement.",
    "Streaming keyword spotting should keep up with live microphone audio in real time.",
)


WAKE_POSITIVE_VARIANTS = (
    "hey sahir",
    "Hey Sahir",
    "hey sahir listen",
    "okay sahir",
    "Okay Sahir",
    "okay sahir now",
    "hey sahir please",
)


def _resample_to_16k(audio: np.ndarray, sr: int) -> np.ndarray:
    return resample_float_mono(pcm_to_float32_mono(audio), sr, 16_000)


def _load_wav_16k(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1:
            raise ValueError(f"expected mono wav: {path}")
        sr = wf.getframerate()
        pcm = wf.readframes(wf.getnframes())
    audio = np.frombuffer(pcm, dtype=np.int16)
    return _resample_to_16k(audio, sr)


def _reference_spotter(
    keywords_file: Path,
    *,
    threshold: float,
    score: float,
    num_threads: int = 1,
) -> object:
    import sherpa_onnx

    d = kws_model_dir()
    return sherpa_onnx.KeywordSpotter(
        tokens=str(d / "tokens.txt"),
        encoder=str(d / "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        decoder=str(d / "decoder-epoch-12-avg-2-chunk-16-left-64.onnx"),
        joiner=str(d / "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        keywords_file=str(keywords_file),
        num_threads=num_threads,
        keywords_score=score,
        keywords_threshold=threshold,
        provider="cpu",
    )


def _reference_detect(spotter: object, audio: np.ndarray) -> str:
    chunk = 1600
    tail = np.zeros(int(0.66 * 16_000), dtype=np.float32)
    stream = spotter.create_stream()
    for sample in (audio, tail):
        for i in range(0, len(sample), chunk):
            stream.accept_waveform(16_000, sample[i : i + chunk])
            while spotter.is_ready(stream):
                spotter.decode_stream(stream)
            hit = (spotter.get_result(stream) or "").strip()
            if hit:
                spotter.reset_stream(stream)
                return hit
    stream.input_finished()
    while spotter.is_ready(stream):
        spotter.decode_stream(stream)
    return (spotter.get_result(stream) or "").strip()


def _synth_piper(pipeline, text: str) -> np.ndarray:
    audio, sr = pipeline.synthesize_float(text)
    return _resample_to_16k(audio, sr)


def _synth_kokoro(settings: Settings, text: str) -> np.ndarray | None:
    try:
        from tts_backends import KokoroTTS

        tts = KokoroTTS(settings.kokoro_model_dir, speaker_id=settings.kokoro_speaker_id)
        audio, sr = tts.synthesize(text)
        return _resample_to_16k(audio, sr)
    except Exception:
        return None


def main() -> int:
    if not kws_model_ready():
        print("SKIP: KWS model missing (run scripts/fetch_kws.py)")
        return 0

    settings = Settings.from_env()
    from audio_pipeline import SpeechPipeline

    pipeline = SpeechPipeline(settings)
    model_dir = kws_model_dir()
    phrases = parse_wake_phrases(settings.wake_phrases)
    spotter = build_kws_spotter(settings)

    th = settings.wake_kws_threshold
    sc = settings.wake_kws_score
    ref = _reference_spotter(model_dir / "keywords.txt", threshold=th, score=sc)

    bundled: dict[str, object] = {
        "default_keywords_no_hits_on_bundled_wavs": True,
        "test_keywords_expected_hits": 0,
        "test_keywords_expected_total": 2,
        "wavs": {},
    }
    test_kw = model_dir / "test_wavs" / "test_keywords.txt"
    ref_test = _reference_spotter(test_kw, threshold=th, score=sc) if test_kw.is_file() else None
    expected_test_hits = {"0.wav": "LIGHT UP", "1.wav": "LOVELY CHILD"}
    for wav_name in ("0.wav", "1.wav"):
        wav_path = model_dir / "test_wavs" / wav_name
        if not wav_path.is_file():
            continue
        audio = _load_wav_16k(wav_path)
        hit_default = _reference_detect(ref, audio)
        hit_test = _reference_detect(ref_test, audio) if ref_test else ""
        bundled["wavs"][wav_name] = {
            "default_keywords": hit_default,
            "test_keywords": hit_test,
            "test_keywords_expected": expected_test_hits.get(wav_name, ""),
        }
        if hit_default:
            bundled["default_keywords_no_hits_on_bundled_wavs"] = False
        exp = expected_test_hits.get(wav_name, "")
        if exp and hit_test and exp.upper() in hit_test.upper():
            bundled["test_keywords_expected_hits"] = int(bundled["test_keywords_expected_hits"]) + 1

    ref_phrases_path = model_dir / "keywords_raw.txt"
    ref_phrases = (
        [ln.strip() for ln in ref_phrases_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if ref_phrases_path.is_file()
        else ["HEY SIRI", "HI GOOGLE", "ALEXA"]
    )
    ref_pos_hits: list[str] = []
    for phrase in ref_phrases:
        audio = _synth_piper(pipeline, phrase.lower())
        if _reference_detect(ref, audio):
            ref_pos_hits.append(phrase)
    ref_pos_rate = len(ref_pos_hits) / max(len(ref_phrases), 1)

    pos_clips: list[tuple[str, str, np.ndarray]] = []
    for text in WAKE_POSITIVE_VARIANTS:
        if any(p.lower() in text.lower() for p in phrases):
            audio = _synth_piper(pipeline, text)
            pos_clips.append(("piper", text, audio))
    kokoro_voice = False
    for text in WAKE_POSITIVE_VARIANTS:
        if not any(p.lower() in text.lower() for p in phrases):
            continue
        audio = _synth_kokoro(settings, text)
        if audio is not None and audio.size:
            pos_clips.append(("kokoro", text, audio))
            kokoro_voice = True

    neg_clips: list[tuple[str, np.ndarray]] = []
    for u in NEGATIVE_UTTERANCES:
        neg_clips.append((u[:48], _synth_piper(pipeline, u)))
    for p in phrases:
        neg_clips.append((f"near-{p}", _synth_piper(pipeline, f"well {p} how are you")))

    t_wall0 = time.perf_counter()
    t_cpu0 = time.process_time()
    pos_hits: list[str] = []
    for _voice, text, audio in pos_clips:
        if spotter.detect_in_clip(audio):
            pos_hits.append(text)
    false_accepts: list[str] = []
    for label, audio in neg_clips:
        if spotter.detect_in_clip(audio):
            false_accepts.append(label)
    cpu_infer = time.process_time() - t_cpu0
    wall_infer = time.perf_counter() - t_wall0

    audio_s = sum(len(a) for _, _, a in pos_clips) + sum(len(a) for _, a in neg_clips)
    audio_s /= 16_000
    wall_rtf = wall_infer / max(audio_s, 0.001)
    cpu_per_s = cpu_infer / max(audio_s, 0.001)

    pos_rate = len(pos_hits) / max(len(pos_clips), 1)
    neg_rate = len(false_accepts) / max(len(neg_clips), 1)

    if wall_rtf > 0.25:
        perf_note = "infer RTF high for streaming gate — investigate CPU load"
    elif wall_rtf <= 0.1:
        perf_note = "infer RTF acceptable for server-side gate"
    else:
        perf_note = "infer RTF moderate; keep gate off unless needed"

    if ref_pos_rate >= 0.3 and pos_rate < 0.3:
        recommend = (
            "Wrapper and model are OK on bundled reference keywords; "
            "custom wake phrases need real-microphone tuning (Piper/Kokoro are weak positives)."
        )
    elif pos_rate >= 0.8 and neg_rate <= 0.05:
        recommend = "Usable with current threshold on synthetic speech; confirm on a real mic."
    elif pos_rate < 0.5:
        recommend = (
            "Custom phrases rarely fire on Piper/Kokoro here; tune WAKE_KWS_THRESHOLD/SCORE "
            "and validate with a real microphone — not necessarily a broken KWS stack."
        )
    else:
        recommend = "Partial synthetic detection; validate on device microphone before enabling the gate."

    result = {
        "phrases": phrases,
        "bundled": bundled,
        "reference_keywords_piper_rate": ref_pos_rate,
        "reference_keywords_piper_hits": ref_pos_hits,
        "positive_clips": len(pos_clips),
        "positive_detection_rate": pos_rate,
        "positive_hits": pos_hits,
        "false_accept_rate": neg_rate,
        "false_accepts": false_accepts,
        "negative_clips": len(neg_clips),
        "audio_seconds_infer": round(audio_s, 2),
        "wall_seconds_infer": round(wall_infer, 4),
        "cpu_seconds_infer": round(cpu_infer, 4),
        "wall_rtf": round(wall_rtf, 4),
        "cpu_seconds_per_audio_second": round(cpu_per_s, 4),
        "kokoro_second_voice": kokoro_voice,
        "recommendation": recommend,
        "perf_note": perf_note,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    wav_lines = "\n".join(
        f"  - `{name}` default={info['default_keywords']!r} "
        f"test_keywords={info['test_keywords']!r} (expected {info.get('test_keywords_expected')!r})"
        for name, info in bundled.get("wavs", {}).items()
    )
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(
        "\n".join(
            [
                "# KWS wake phrases (sherpa-onnx)",
                "",
                "_From `scripts/eval_kws.py` on this machine (Piper + optional Kokoro synthesis)._",
                "",
                f"- Model: gigaspeech zipformer KWS (see `models/README.md`)",
                f"- Custom phrases: `{', '.join(phrases)}`",
                "",
                "## Bundled model sanity",
                f"- Default keywords on `test_wavs/`: no spurious hits = {bundled['default_keywords_no_hits_on_bundled_wavs']}",
                f"- `test_keywords.txt` on bundled speech: **{bundled['test_keywords_expected_hits']}/{bundled['test_keywords_expected_total']}** expected hits",
                wav_lines,
                f"- Piper vs bundled `keywords_raw.txt`: **{ref_pos_rate:.0%}** ({len(ref_pos_hits)}/{len(ref_phrases)})",
                "",
                "## Custom wake phrases (synthetic)",
                f"- Voices: Piper{' + Kokoro' if kokoro_voice else ' only (Kokoro unavailable)'}",
                f"- Positive detection: **{pos_rate:.0%}** ({len(pos_hits)}/{len(pos_clips)})",
                f"- False accepts: **{neg_rate:.1%}** on {len(neg_clips)} clips (~{audio_s:.0f}s in infer benchmark)",
                "",
                "## Performance (single shared spotter, 1–2 threads)",
                f"- Wall RTF (infer only): **{wall_rtf:.3f}** ({wall_infer:.2f}s / {audio_s:.1f}s audio)",
                f"- CPU time per second of audio: **{cpu_per_s * 1000:.1f} ms**",
                f"- {perf_note}",
                "",
                f"**Recommendation:** {recommend}",
                "",
                "Earlier 0% positives and >1× RTF were caused by a flawed eval harness "
                "(per-clip spotter rebuild and incorrect PCM scaling assumptions), not necessarily the model.",
                "",
                f"Raw JSON: `{OUT_JSON.relative_to(ROOT)}`.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps({k: v for k, v in result.items() if k not in ("false_accepts",)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())