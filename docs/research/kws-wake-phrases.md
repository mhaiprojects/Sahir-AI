# KWS wake phrases (sherpa-onnx)

_From `scripts/eval_kws.py` on this machine (Piper + optional Kokoro synthesis)._

- Model: gigaspeech zipformer KWS (see `models/README.md`)
- Custom phrases: `hey sahir, okay sahir`

## Bundled model sanity
- Default keywords on `test_wavs/`: no spurious hits = True
- `test_keywords.txt` on bundled speech: **2/2** expected hits
  - `0.wav` default='' test_keywords='LIGHT UP' (expected 'LIGHT UP')
  - `1.wav` default='' test_keywords='LOVELY CHILD' (expected 'LOVELY CHILD')
- Piper vs bundled `keywords_raw.txt`: **89%** (8/9)

## Custom wake phrases (synthetic)
- Voices: Piper + Kokoro
- Positive detection: **7%** (1/14)
- False accepts: **0.0%** on 18 clips (~65s in infer benchmark)

## Performance (single shared spotter, 1–2 threads)
- Wall RTF (infer only): **0.033** (2.12s / 65.1s audio)
- CPU time per second of audio: **107.4 ms**
- infer RTF acceptable for server-side gate

**Recommendation:** Wrapper and model are OK on bundled reference keywords; custom wake phrases need real-microphone tuning (Piper/Kokoro are weak positives).

Earlier 0% positives and >1× RTF were caused by a flawed eval harness (per-clip spotter rebuild and incorrect PCM scaling assumptions), not necessarily the model.

Raw JSON: `data/bench/kws_eval.json`.
