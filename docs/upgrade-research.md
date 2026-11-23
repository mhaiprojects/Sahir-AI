# Upgrade research — local-first voice agent (Nov 2026)

Research date: 2026-11-14 (GPU facts refreshed 2026-11-23). Target machine (**the dev machine**): **8-core desktop
CPU, ample system RAM, AMD Radeon RX 5500 (Navi 14 / RDNA1, 4 GB VRAM), no NVIDIA/CUDA**. ROCm does not
officially support this GPU (`gfx1012`); Ollama's bundled ROCm backend drops it. **LLM inference already
runs on the GPU through Ollama's Vulkan backend** (RADV; enabled by default in 0.32.x). STT (faster-whisper),
TTS (Piper), and embeddings are still mostly **CPU** unless configured otherwise; whisper.cpp and
llama.cpp-server are optional **Vulkan** paths for STT and LLM if we need more control than Ollama exposes.
See [research/gpu-acceleration-research.md](./research/gpu-acceleration-research.md) for measured VRAM and
latency. Licenses were checked against the upstream repos linked below.

## TL;DR — what we adopted

| Area | Current | Decision | Why |
|------|---------|----------|-----|
| VAD | Silero (inside faster-whisper, final pass only) | **Silero VAD v6 (MIT) also as a server-side speech gate** before Whisper in always-listening mode + configurable thresholds | Bundled already (`faster_whisper/assets/silero_vad_v6.onnx`), zero new deps, cuts wasted Whisper runs on noise |
| Vector store | none | **sqlite-vec 0.1.9 (Apache-2.0/MIT)** in `data/agent.db`, **numpy cosine fallback** | Single on-disk SQLite file, no server, Python 3.14 wheel available |
| Embeddings | none | **Ollama `nomic-embed-text`** (768-d, already installed) | Local, already pulled, good retrieval quality for size |
| Memory | none | **Own lightweight memory layer** (SQLite + Ollama JSON extraction + human review queue) | Mem0/Letta are Apache-2.0 but heavyweight (servers/extra DBs); our needs are small |
| TTS | Piper | **Piper stays default; Kokoro-82M via sherpa-onnx 2.0.1 as optional backend** (`TTS_BACKEND=kokoro`) | Kokoro is markedly more natural, Apache-2.0 weights; `kokoro-onnx` pip pkg requires Python <3.14, sherpa-onnx ships cp314 wheels. Measured caveats below. |
| STT | faster-whisper tiny/base | **Keep faster-whisper**; document `large-v3-turbo`/`small.en` as quality options; Moonshine / Parakeet as next experiments | Works today; turbo is too slow on 8-core CPU for live, fine for final pass if latency acceptable |
| LLM serving | Ollama (Vulkan on RX 5500; `llama3.1:8b` partially offloaded today) | **Keep Ollama**; prefer a small chat model that fits fully in VRAM; optional llama.cpp-server (Vulkan) only if benchmarks justify it | Vulkan is already active—biggest win is VRAM fit and routing model pinning, not swapping the serving stack |
| Agent loop | stateless router, history from browser | **Server-side agent loop** with persistent conversations, memory + RAG context, tool routing, SSE streaming | Core of the "complete local-first agent" goal |

## 1. Speech-to-text

| Option | Version / license | CPU fit (8-core) | GPU fit (4 GB AMD) | Notes |
|--------|-------------------|-----------------|-------------------|-------|
| **faster-whisper** (CTranslate2) | 1.2.1 installed, MIT; Whisper weights MIT | Good: tiny/base real-time, small.en OK, large-v3-turbo ~1–3× RT int8 | None (CUDA only) | Supports `large-v3-turbo` / `turbo` alias. Bundles Silero VAD v6. **Keep.** |
| whisper.cpp | MIT | Good | **Vulkan works** (`-DGGML_VULKAN=1`) | Only path to use the AMD GPU for STT; would need a subprocess/server integration |
| **Moonshine Voice** | MIT code + MIT English/streaming models (Feb 2026) | Excellent — designed for CPU, streaming, Medium-streaming 245M = 6.65% WER (better than Whisper large-v3 7.44% on OpenASR) | n/a | Strong candidate for the **live** tier (replaces tiny). Next experiment. |
| NVIDIA Parakeet TDT 0.6B v3 | CC-BY-4.0 (attribution) | OK via sherpa-onnx int8 export | n/a | 25 European languages, punctuation; NeMo itself is CUDA-oriented — use sherpa-onnx |
| NVIDIA Canary | CC-BY-4.0 (1B-flash) / some NC variants | Heavy | needs CUDA | Not a fit |
| Vosk | Apache-2.0 | Very light | n/a | Lower accuracy than Whisper/Moonshine; good only for tiny devices |
| sherpa-onnx | 2.0.1 (Oct 2026), Apache-2.0, cp314 wheels | Excellent | n/a | One runtime for Parakeet, Moonshine, Zipformer streaming, Kokoro TTS, KWS, VAD |

Sources: https://github.com/SYSTRAN/faster-whisper · https://getfraze.app/learn/whisper-large-v3-turbo ·
https://github.com/ggml-org/whisper.cpp · https://petewarden.com/2026/02/13/announcing-moonshine-voice/ ·
https://moonshine-voice.readthedocs.io/en/stable/models/available-models/ ·
https://github.com/moonshine-ai/moonshine/blob/main/LICENSE · https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3 ·
https://github.com/k2-fsa/sherpa-onnx/pull/2500 · https://pypi.org/project/sherpa-onnx/

## 2. Text-to-speech

| Option | License | CPU fit | Notes |
|--------|---------|---------|-------|
| **Piper** (`piper-tts` 1.7 installed; 1.8.0 current) | Engine **GPL-3.0-or-later** (OHF-Voice/piper1-gpl); each voice has its own dataset licence | Excellent, real-time on 1 core | Keep as default. GPL is fine for a personal local app; note it if the project is ever redistributed under a permissive licence. |
| **Kokoro-82M** | Weights Apache-2.0; `kokoro-onnx` wrapper MIT (but `Python <3.14`) | Good (~faster than real-time int8 on 8 cores) | **Adopted as optional backend via sherpa-onnx** (`kokoro-multi-lang-v1_0` / `kokoro-en-v0_19`). |
| F5-TTS | Code MIT, **weights CC-BY-NC-4.0** | Slow on CPU (diffusion) | Non-commercial weights, GPU-oriented — skip |
| XTTS-v2 (idiap coqui-ai-TTS fork) | Code MPL-2.0, **weights CPML (non-commercial)** | Slow on CPU | Voice cloning; licence + speed rule it out |
| Orpheus 3B | Apache-2.0 + Llama 3.2 terms | Too heavy for CPU real-time | Needs GPU |
| Chatterbox (Resemble AI) | MIT | Heavy-ish (0.5B), GPU preferred | Nice expressive cloning; revisit with a GPU |
| sherpa-onnx TTS | Apache-2.0 runtime | Excellent | Runs Piper/VITS, Kokoro, Matcha models — single runtime |

Sources: https://pypi.org/project/piper-tts/ · https://github.com/OHF-Voice/piper1-gpl ·
https://github.com/hexgrad/kokoro · https://pypi.org/project/kokoro-onnx/ · https://github.com/thewh1teagle/kokoro-onnx ·
https://github.com/SWivid/F5-TTS · https://github.com/idiap/coqui-ai-TTS/discussions/216 ·
https://huggingface.co/canopylabs/orpheus-3b-0.1-ft · https://github.com/resemble-ai/chatterbox

## 3. Wake word

| Option | License | Notes |
|--------|---------|-------|
| **openWakeWord** (0.4.0 installed, `hey_jarvis`) | Code Apache-2.0; **bundled pretrained models CC BY-NC-SA 4.0** | Keep for personal use; train a custom model for anything commercial |
| microWakeWord | Library Apache-2.0; models from microwakeword.com non-commercial by default | Built for ESP32 satellites — useful if we add Wyoming/ESPHome satellites |
| Porcupine (Picovoice) | **Not OSS**; free tier personal/non-commercial only, needs AccessKey | Avoid (key + licence) |
| sherpa-onnx KWS | Apache-2.0 | Open-vocabulary keyword spotting with no training — good future replacement for custom phrases |

Sources: https://github.com/dscripka/openWakeWord · https://huggingface.co/davidscripka/openwakeword ·
https://github.com/OHF-Voice/micro-wake-word · https://microwakeword.com/license ·
https://github.com/k2-fsa/sherpa-onnx/blob/master/sherpa-onnx/python/sherpa_onnx/keyword_spotter.py

## 4. Voice activity detection

| Option | License | Notes |
|--------|---------|-------|
| **Silero VAD v6** (Aug 2025) | MIT | 16% fewer errors on noisy data vs v5. Already bundled in faster-whisper 1.2.1 and pipecat. **Adopted as server-side speech gate.** |
| WebRTC VAD | BSD | Very cheap, much less accurate; browser already does RMS gating |
| TEN VAD (Agora) | Apache-2.0 **plus additional restrictive conditions** (no competing with Agora, no enabling third parties, derivative works bound) | Faster end-of-speech detection, but licence is not plain OSS — avoid by default |

Sources: https://github.com/snakers4/silero-vad · https://github.com/snakers4/silero-vad/releases/tag/v6.0 ·
https://github.com/TEN-framework/ten-vad/blob/main/LICENSE · https://github.com/kizuna-ai-lab/sokuji/issues/194

## 5. Streaming pipelines

| Option | License | Fit |
|--------|---------|-----|
| **Pipecat** (1.7.0 installed; 1.16.0 current, Nov 2026) | BSD-2-Clause | Already a dependency; good for a future full-duplex WebRTC/WebSocket pipeline (barge-in, interruptions). Upgrade pin when doing streaming audio. |
| LiveKit Agents | Apache-2.0, self-hostable | Needs a LiveKit server (SFU); overkill for one-user LAN |
| RealtimeSTT / RealtimeTTS (KoljaB) | Apache-2.0 / MIT | Great single-process desktop mode; overlaps with our own pipeline |
| Wyoming protocol (OHF) | MIT | Interop with Home Assistant / satellites; worth adding as an optional server later |

Today the web UI uses HTTP upload + two-phase Whisper. We added **SSE token streaming for chat replies**
(`/api/agent/chat/stream`) — the cheapest latency win. Full-duplex audio streaming (Pipecat over WebSocket)
is listed under "what remains".

Sources: https://github.com/pipecat-ai/pipecat · https://pypi.org/project/pipecat-ai/ · https://github.com/livekit/agents ·
https://github.com/KoljaB/RealtimeSTT · https://github.com/KoljaB/RealtimeTTS · https://github.com/rhasspy/wyoming

## 6. Local LLM serving

| Option | License | Fit for this PC |
|--------|---------|-----------------|
| **Ollama** (current) | MIT | **Vulkan backend in use on the dev machine** (`library=Vulkan`, 4.0 GiB total VRAM). `llama3.1:8b` does **not** fit: ~10/33 layers on GPU, generation ~4.8–6.8 tok/s (~70% of weights on CPU). **Keep Ollama**; switch the default orchestrator to a 3–4B Q4 model that fits entirely on the GPU for ~30–60 tok/s generation (see gpu-acceleration research). |
| llama.cpp `llama-server` | MIT | Same Vulkan stack as Ollama (ggml). Optional alternate OpenAI-compatible server if we need flags Ollama hides; reported ~30 tok/s gen for 9B Q4_K_M on an **8 GB** RDNA1 card—not achievable with 8B on this **4 GB** card. |
| vLLM | Apache-2.0 | GPU-first (CUDA/ROCm supported cards); CPU backend exists but not worthwhile here |
| LM Studio | **Proprietary** (free) | Not OSS |
| LocalAI | MIT | OpenAI-compatible umbrella over llama.cpp/whisper/piper; extra layer we don't need |

Sources: https://github.com/ollama/ollama · https://github.com/ggml-org/llama.cpp ·
https://github.com/ggml-org/llama.cpp/issues/20564 · https://blog.schiller.sh/2025/04/12/llama.cpp.html ·
https://github.com/ROCm/ROCm/issues/1735 · https://github.com/vllm-project/vllm · https://github.com/mudler/LocalAI

## 7. Embeddings, vector stores, memory

| Option | License | Notes |
|--------|---------|-------|
| **sqlite-vec 0.1.9** (Mar 2026; 0.1.10 alphas add DiskANN) | Apache-2.0 / MIT | Pre-v1 but stable enough; brute-force KNN is plenty for <100k chunks. **Adopted**, with numpy fallback if the extension can't load. |
| LanceDB | Apache-2.0 | Embedded columnar store; great at scale, extra dependency |
| Chroma | Apache-2.0 | Embedded or server; heavier dependency tree |
| Mem0 | Apache-2.0 | Memory layer w/ LLM extraction; needs a vector DB + config; we reimplemented the useful 20% |
| Letta (MemGPT) | Apache-2.0 | Full agent server with its own DB; too heavy to embed |
| nomic-embed-text (Ollama) | Apache-2.0 | 768-d; uses `search_document:` / `search_query:` task prefixes — implemented |

Sources: https://github.com/asg017/sqlite-vec · https://github.com/asg017/sqlite-vec/releases · https://pypi.org/project/sqlite-vec/ ·
https://github.com/lancedb/lancedb · https://github.com/chroma-core/chroma · https://github.com/mem0ai/mem0 ·
https://github.com/letta-ai/letta · https://ollama.com/library/nomic-embed-text

## Measured on the dev box

### Application stack (2026-11-15)

| Check | Result |
|-------|--------|
| sqlite-vec 0.1.9 in the Python 3.14 venv | loads (`vec_version()` = v0.1.9); `vector_backend=sqlite-vec` |
| nomic-embed-text via Ollama | 768-d, ~40–120 ms per short text on CPU (prefer CPU `num_gpu: 0` so chat models keep VRAM—see [#4](https://github.com/mhaiprojects/Sahir-AI/issues/4)) |
| Kokoro int8 multi-lang v1.1 via sherpa-onnx 2.0.1 | works; **RTF ≈ 1.6** (4.7 s for 2.9 s of audio, 8 threads) — slower than Piper; sherpa-onnx ≥2.0 **ignores espeak data and needs a lexicon**, so out-of-vocabulary words (e.g. "PC") are **skipped**. Hence Kokoro stays opt-in. The int8 English v0.19 package has no lexicon and produces silence on 2.x. |
| Realtime voice loop (`/ws/voice`, Piper, Whisper base, Ollama chat model) | simple question with `llama3.1:8b` (partial Vulkan offload): reply ≈ 8–12 s after end of speech, first audio ≈ 1.5 s after the reply (sentence-streamed TTS). **LLM dominates** (~9 s of two Ollama calls; see gpu-acceleration research). |
| Research worker (RAG + Ollama) | 20–31 s per summary (CPU-heavy RAG + same LLM bottleneck) |
| Barge-in | speech during TTS → `barge_in` within one VAD window (~0.2 s), remaining sentences dropped |

### GPU, VRAM, and routing latency (2026-11-16 – 2026-11-23)

Hardware: **RX 5500, 4 GB VRAM**. Desktop (KWin + Chrome) uses ~1.1 GB before models load. Ollama 0.32.15,
Vulkan via RADV; no usable ROCm or CUDA. Full journal numbers: [gpu-acceleration-research.md](./research/gpu-acceleration-research.md).

| Check | Result |
|-------|--------|
| `llama3.1:8b` on 4 GB card | ~10/33 layers GPU (~1.56 GiB weights on GPU); generation ~4.8–6.8 tok/s |
| `qwen3:0.6b` classify (warm, GPU-resident) | wall **p50 ~150 ms** (e.g. p50 161 ms routing JSON bench 2026-11-16; p50 140 ms one-letter classify 2026-11-23) |
| `qwen3:0.6b` cold load | **~7 s** |
| VRAM contention | With a larger chat model (e.g. qwen3.5:2b) and `nomic-embed-text` also loaded, `qwen3:0.6b` showed **~77% CPU / 23% GPU** in `ollama ps`—router must be pinned and chat picks must not evict it |
| VRAM budget (target layout) | router **~0.7 GB** + nomic **~0.3 GB** → **~2.5 GB** of 4 GB left for the active chat model |
| nomic-embed-text warm (GPU) | p50 ~31 ms (embedding router tier); still recommended on CPU for VRAM [#4](https://github.com/mhaiprojects/Sahir-AI/issues/4) |

Supervisor/router architecture latency goals and tiered router design are in the external architecture doc (§6.1);
implementation is tracked under Sahir items 2 and 8 in the prioritized task list.

## Recommended roadmap (after this upgrade)

Prioritized GPU work is in [gpu-acceleration-research.md](./research/gpu-acceleration-research.md) §5 (small
default model on Vulkan, embeddings on CPU, Ollama memory tuning, optional raw llama-server).

1. **Moonshine Medium-Streaming for live transcription** (MIT, CPU-optimised) — replace Whisper tiny live tier ([#8](https://github.com/mhaiprojects/Sahir-AI/issues/8)).
2. **Small default Ollama chat model** that fits fully in VRAM on the RX 5500 (Vulkan already enabled)—not a second Vulkan bring-up ([#3](https://github.com/mhaiprojects/Sahir-AI/issues/3)).
3. **Pipecat WebSocket transport** for full-duplex audio with barge-in.
4. **Wyoming server** for Home Assistant / satellite mics ([#29](https://github.com/mhaiprojects/Sahir-AI/issues/29)).
5. sherpa-onnx KWS for custom wake phrases without training ([#30](https://github.com/mhaiprojects/Sahir-AI/issues/30)).
6. Optional **llama.cpp-server (Vulkan)** only if Ollama overhead or missing flags block tuning ([#7](https://github.com/mhaiprojects/Sahir-AI/issues/7)).
