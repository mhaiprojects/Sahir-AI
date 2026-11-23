#!/usr/bin/env python3
"""Measure Kokoro OOV word skips via lexicon checks and faster-whisper round-trip overlap."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import PROJECT_ROOT, Settings  # noqa: E402
from kokoro_speech import (  # noqa: E402
    KokoroLexicon,
    collect_oov_words,
    default_user_lexicon_path,
    prepare_kokoro_text,
    reset_kokoro_oov_fallback_count,
)
from tts_backends import KokoroTTS, float_to_wav_bytes, measure_kokoro_rtf  # noqa: E402

TEST_SENTENCES = (
    "My PC and GPU run Sahir with Ollama.",
    "Install ClickUp on the RX5500 workstation.",
    "Visit https://example.com/docs and email help@example.com.",
    "Release v2.4.1 adds 3D acceleration.",
    "NASA and JPEG are common acronyms here.",
    "The URL is www.sahir.ai/status#health.",
)


def _words_from_text(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[a-z0-9]+", text.lower())}


def _transcribe_whisper(wav_path: Path, settings: Settings) -> str:
    import numpy as np
    from faster_whisper import WhisperModel

    with wave.open(str(wav_path), "rb") as wf:
        nch = wf.getnchannels()
        pcm = wf.readframes(wf.getnframes())
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if nch > 1:
            audio = audio.reshape(-1, nch).mean(axis=1)

    model = WhisperModel(
        settings.whisper_final_model,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
    )
    segments, _info = model.transcribe(audio, beam_size=1, language="en")
    return " ".join(seg.text.strip() for seg in segments)


def _overlap_score(source: str, heard: str) -> dict[str, object]:
    src = _words_from_text(source)
    got = _words_from_text(heard)
    if not src:
        return {"overlap_ratio": 1.0, "missing": [], "heard": heard}
    hit = src & got
    missing = sorted(src - got)
    return {
        "overlap_ratio": round(len(hit) / len(src), 3),
        "missing": missing,
        "heard": heard,
    }


def run_check(*, use_prep: bool, settings: Settings | None = None) -> dict[str, object]:
    settings = settings or Settings()
    model_dir = settings.kokoro_model_dir
    if not model_dir.is_dir():
        raise SystemExit(f"Kokoro model missing: {model_dir}")

    lang = os.getenv("KOKORO_LEXICON", "us-en")
    base = model_dir / f"lexicon-{lang}.txt"
    lex = KokoroLexicon.load(base, user_lexicon=default_user_lexicon_path())
    user_copy = default_user_lexicon_path()
    if not user_copy.is_file():
        example = PROJECT_ROOT / "docs/examples/kokoro_user_lexicon.txt"
        if example.is_file():
            user_copy.parent.mkdir(parents=True, exist_ok=True)
            user_copy.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            lex = KokoroLexicon.load(base, user_lexicon=user_copy)

    reset_kokoro_oov_fallback_count()
    tts = KokoroTTS(model_dir, lexicon=lex)

    rows: list[dict[str, object]] = []
    lexicon_oov_raw = 0
    lexicon_oov_after_prep = 0
    low_overlap = 0

    with tempfile.TemporaryDirectory(prefix="kokoro-oov-") as tmpdir:
        tmp = Path(tmpdir)
        for sentence in TEST_SENTENCES:
            pre_oov = collect_oov_words(sentence, lex)
            lexicon_oov_raw += len(pre_oov)
            speak = prepare_kokoro_text(sentence, lex) if use_prep else sentence
            post_oov = collect_oov_words(speak, lex) if use_prep else pre_oov
            lexicon_oov_after_prep += len(post_oov)
            audio, sr = tts.synthesize(sentence, prepare=use_prep)
            wav = tmp / "utt.wav"
            wav.write_bytes(float_to_wav_bytes(audio, sr))
            heard = _transcribe_whisper(wav, settings)
            overlap = _overlap_score(sentence, heard)
            if float(overlap["overlap_ratio"]) < 0.55:
                low_overlap += 1
            rows.append(
                {
                    "sentence": sentence,
                    "speakable": speak,
                    "lexicon_oov_raw": pre_oov,
                    "lexicon_oov_prepared": post_oov,
                    "overlap": overlap,
                    "audio_s": round(len(audio) / sr, 3) if sr else 0,
                }
            )

    rtf_block: dict[str, object] | None = None
    piper_rtf: float | None = None
    try:
        rtf_block = measure_kokoro_rtf(model_dir, " ".join(TEST_SENTENCES[:3]))
    except Exception as exc:
        rtf_block = {"error": str(exc)}

    try:
        from piper import PiperVoice

        text = " ".join(TEST_SENTENCES[:3])
        voice = PiperVoice.load(str(settings.piper_model_path))
        t0 = time.perf_counter()
        chunks = list(voice.synthesize(text))
        t1 = time.perf_counter()
        samples = sum(len(c.audio_float_array) for c in chunks)
        dur = samples / voice.config.sample_rate if samples else 0
        piper_rtf = round((t1 - t0) / dur, 3) if dur else None
    except Exception:
        piper_rtf = None

    return {
        "prepared": use_prep,
        "sentences": len(TEST_SENTENCES),
        "lexicon_oov_tokens_raw": lexicon_oov_raw,
        "lexicon_oov_tokens_after_prep": lexicon_oov_after_prep,
        "low_overlap_utterances": low_overlap,
        "rows": rows,
        "kokoro_rtf": rtf_block,
        "piper_rtf": piper_rtf,
        "user_lexicon": str(user_copy),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", action="store_true", help="Raw text (no Kokoro prep)")
    parser.add_argument("--after", action="store_true", help="With Kokoro prep (default)")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    args = parser.parse_args()
    use_prep = not args.before
    if args.before and args.after:
        before = run_check(use_prep=False)
        after = run_check(use_prep=True)
        payload = {"before": before, "after": after}
    elif args.before:
        payload = run_check(use_prep=False)
    else:
        payload = run_check(use_prep=True)

    if args.json:
        print(json.dumps(payload, indent=2))
        return

    if "before" in payload and "after" in payload:
        b, a = payload["before"], payload["after"]
        print(
            f"lexicon OOV raw: before={b['lexicon_oov_tokens_raw']} (prep={b['lexicon_oov_tokens_after_prep']}) "
            f"after={a['lexicon_oov_tokens_raw']} (prep={a['lexicon_oov_tokens_after_prep']})"
        )
        print(f"low overlap utterances: before={b['low_overlap_utterances']} after={a['low_overlap_utterances']}")
    else:
        print(
            f"lexicon OOV raw: {payload['lexicon_oov_tokens_raw']} "
            f"after_prep: {payload['lexicon_oov_tokens_after_prep']}"
        )
        print(f"low overlap utterances: {payload['low_overlap_utterances']}")
    if payload.get("kokoro_rtf"):
        print("kokoro_rtf:", json.dumps(payload["kokoro_rtf"], indent=2))
    if payload.get("piper_rtf") is not None:
        print(f"piper_rtf: {payload['piper_rtf']}")


if __name__ == "__main__":
    main()