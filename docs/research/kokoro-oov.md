# Kokoro TTS out-of-vocabulary (OOV) handling

Measured on this machine (sherpa-onnx Kokoro int8 multi-lang v1.1, CPU, 2026-11-24).

## Problem

sherpa-onnx 2.x Kokoro ignores bundled espeak-ng data and tokenises only from `lexicon-*.txt`.
Unknown tokens are **silently dropped** (C++ log: `OOV word skipped`). That affects product
terms (PC, GPU, Sahir, Ollama, ClickUp, RX5500), URLs, emails, and version strings.

## Before (raw text, no prep)

| Metric | Value |
|--------|------:|
| Lexicon OOV tokens (7 test sentences, raw) | 5 |
| Utterances with Whisper word overlap &lt; 55% | 4 / 7 |
| Kokoro RTF (`num_threads` 1 / 2 / 4) | ~2.7 / ~2.3 / ~2.1 |
| Piper RTF (same clip) | ~0.10 |
| Time to first audio (full paragraph) | waits for full synth (RTF &gt; 1) |

Whisper round-trip often missed skipped words entirely (e.g. no “GPU”, “Sahir”, “ClickUp”).

## After (`kokoro_speech.prepare_kokoro_text` + user lexicon)

| Metric | Value |
|--------|------:|
| Lexicon OOV tokens after normalisation + fallbacks | **0** |
| Utterances with Whisper word overlap &lt; 55% | **0 / 7** |
| `/api/health` → `tts.kokoro_oov_fallbacks` | increments per fallback token |

Mechanisms:

1. **Normalisation** — URLs, emails, versions, `3D`, symbols → speakable words (`kokoro_speech.py`).
2. **Fallback rewrite** — acronyms → spaced letters; alphanumerics and camelCase → splits or spelled
   letters; heuristics (e.g. ClickUp → `click up`).
3. **User lexicon** — copy `docs/examples/kokoro_user_lexicon.txt` to `models/kokoro/user_lexicon.txt`
   (gitignored tree). IPA lines are merged into `user_phoneme_lexicon.auto.txt` for sherpa; respellings
   (`word -> phrase`) apply at prep time only.
4. **No silent drop policy** — debug logs per fallback; health counter via `kokoro_tts_health()`.

## Performance

| Backend | RTF (this CPU) | Default |
|---------|----------------|---------|
| Piper | ~0.10 | **yes** (`TTS_BACKEND=piper`) |
| Kokoro threads=4 | ~2.1 | optional (`TTS_BACKEND=kokoro`, `KOKORO_NUM_THREADS`) |

Kokoro quality on covered text can be pleasant, but it remains **slower than real time on CPU**
here. Sentence streaming (`split_sentences` in `web/voice_ws.py`) still helps time-to-first-audio
when Kokoro is selected. **Recommendation: keep Piper as default**; use Kokoro when you accept
higher latency and maintain a user lexicon for product names.

## Remaining limitations

- Letter-spelling and respellings sound robotic (e.g. “P C”, “R X five five zero zero”).
- No bundled G2P (`misaki` / `g2p_en` not installed); espeak-ng data in the model dir is not used by
  sherpa-onnx 2.x Kokoro.
- Very long hostnames or rare Unicode still may need manual user-lexicon IPA lines.
- `user_phoneme_lexicon.auto.txt` is regenerated from phoneme lines in the user lexicon; arrow
  respellings are prep-only.

## Verification

```bash
timeout 600 venv/bin/python scripts/kokoro_oov_check.py --before --after
timeout 120 venv/bin/python -m pytest -q tests/test_kokoro_speech.py
```

See also `scripts/setup_kokoro.sh` and `tts_backends.py`.