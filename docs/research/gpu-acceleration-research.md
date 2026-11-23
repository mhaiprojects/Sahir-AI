# GPU acceleration research: Sahir on an entry-level 4 GB AMD GPU

Research only; no code or configuration was changed. Date: 2026-11-16.
Builds on [`docs/upgrade-research.md`](../upgrade-research.md) and its roadmap.
Related issues are listed in [§10](#10-tracked-in-issues).

Labels used below:
- **Measured (dev box)**: read from the development machine's own logs or sysfs.
- **Reported**: a number someone published (linked).
- **Estimate**: extrapolation from the reported numbers. Not measured. Confirm with the benchmark plan in §8.

## 1. Summary

- **The card has 4 GB of VRAM** (4,278,190,080 bytes, measured; AMD RX 5500 class GPU on the dev machine). Before any model loads, the desktop and Chrome already use about 1.1 GB of it, so only about 2.9 GB is free.
- **Ollama already uses the GPU through Vulkan** on the dev machine (AMD RX 5500, 4 GB). Ollama 0.32.15 has Vulkan on by default (`OLLAMA_VULKAN:true`) and detects the card via RADV. Its built-in ROCm is skipped ("dropping ROCm device — no rocblas support for gfx target this RDNA1 chip"). `docs/upgrade-research.md` reflects this as of 2026-11-23 ([#19](https://github.com/mhaiprojects/Sahir-AI/issues/19)).
- **llama3.1:8b does not fit in VRAM.** Its weights alone are about 4.6 GiB. Ollama currently puts **10 of 33 layers** on the GPU (about 1.56 GiB) and keeps the rest on the CPU. Generation runs at about 4.8–6.8 tokens/s, so the partial offload is barely helping.
- **Measured latency breakdown:** most of the 8–12 s reply delay is two Ollama calls per turn. The first is the orchestrator call: about 340 prompt tokens at about 104 t/s (about 3.2 s), plus about 25 tokens generated at about 5.9 t/s (about 4 s). The second is a short call of about 2 s. The LLM step is the bottleneck. STT and TTS are not.
- **The biggest win is a smaller LLM that fits entirely on the GPU**, for example Llama 3.2 3B or Qwen3 4B at Q4_K_M, through the same Ollama Vulkan backend. Estimated LLM time per turn drops from about 9 s to about 2–3 s. The trade-off is weaker tool-calling quality, which needs testing.
- **ROCm is not a realistic option for this RDNA1 chip.** AMD has no plans to support it, and the `HSA_OVERRIDE_GFX_VERSION=10.3.0` hack is reported as invalid on RDNA1 (crashes and memory aperture faults). CTranslate2 (faster-whisper) has ROCm wheels but no this RDNA1 chip kernels, and no Vulkan backend. ONNX Runtime and sherpa-onnx have no usable AMD path on this card apart from an experimental WebGPU-over-Vulkan plugin, and that isn't worth it because Piper is already real-time on the CPU.

## 2. Hardware and software facts (measured on the dev box, read-only)

| Item | Value | Source command |
|---|---|---|
| GPU | Entry-level AMD RDNA1 desktop card, driver `amdgpu` | `lspci -v` |
| VRAM | **4 GB** (about 4,080 MiB usable) | `/sys/class/drm/card*/device/mem_info_vram_total`, `glxinfo -B` |
| VRAM used with no models loaded | about 1,130 MiB (desktop compositor and browser) | `mem_info_vram_used` |
| GTT (system RAM the GPU can map) | about 32 GiB | `mem_info_gtt_total` |
| Memory bandwidth | about 224 GB/s (GDDR6, 128-bit) | `pp_dpm_mclk` |
| Mesa / RADV | Mesa 26.x with `vulkan-radeon`; Vulkan 1.4 | `vulkaninfo --summary` |
| ROCm / HIP system packages | none installed | package manager |
| Ollama | 0.32.x, script install. Bundled backends: `vulkan`, `rocm_v7_2`, `cuda_v12/13`, CPU variants | `ollama --version` |
| Ollama GPU detection | `library=Vulkan`, total 4.0 GiB, available about 3.5 GiB. The bundled ROCm backend drops the card ("no rocblas support for gfx target"). `default_num_ctx=4096` | `journalctl -u ollama` |
| Ollama env | `OLLAMA_VULKAN:true`, `OLLAMA_FLASH_ATTENTION:false`, `OLLAMA_KV_CACHE_TYPE:` (f16), `OLLAMA_KEEP_ALIVE:5m`, `LLAMA_ARG_FIT_TARGET` unset (llama.cpp default is a 1,024 MiB free margin) | journal "server config" |
| llama3.1:8b load | projected 5,019 MiB vs 2,866 MiB free. **Offloaded 10/33 layers**: about 1,557 MiB weights + 144 MiB KV + about 130 MiB compute on the GPU; 3,128 MiB weights + 368 MiB KV on the CPU; n_ctx 4096 | journal |
| nomic-embed-text load | loaded on the GPU alongside the LLM, but only **7/13 layers** fit | journal |
| Measured LLM speed | prompt eval 102–114 t/s on about 340-token prompts, 33–53 t/s on short ones. **Generation 4.4–6.8 t/s** | journal `print_timing` |
| Python runtimes in project venv | onnxruntime 1.24.4 providers `['AzureExecutionProvider','CPUExecutionProvider']`. ctranslate2 4.8.1 sees 0 CUDA/ROCm devices | venv python |
| CPU / RAM | 8-core desktop CPU without integrated graphics (so the desktop always uses the discrete card's VRAM); ample system RAM | `free -g` |

## 3. Per-component answers

### 3.1 faster-whisper / CTranslate2: ROCm or Vulkan on this RDNA1 chip?

- **No Vulkan backend.** CTranslate2 supports CPU, CUDA, and since 4.7 AMD ROCm (HIP) through the same `device="cuda"` API. Its docs list RDNA2/RDNA3 for ROCm. [CT2 hardware support](https://opennmt.net/CTranslate2/hardware_support.html), [PR #1989](https://github.com/OpenNMT/CTranslate2/pull/1989)
- **The ROCm wheels have no kernels for this RDNA1 chip.** Someone who inspected the official v4.7.1 ROCm wheel found these targets: gfx803, 900, 906, 908, 90a, 942, 950, 1030, 1100–1102, 1150, 1151, 1200, 1201. RDNA1 and this RDNA1 chip are absent. The wheel also needs a system ROCm install, which the dev box doesn't have. [nabe2030 recipe](https://github.com/nabe2030/faster-whisper-rocm-strix-halo)
- **ROCm on this RDNA1 chip:** AMD has stated there is no plan to officially add ROCm support for this chip. Building from source is the only path, and numerical accuracy is "left to the end user". [ROCm/ROCm #1735](https://github.com/ROCm/ROCm/issues/1735). Debian and Ubuntu distro ROCm packages enable this RDNA1 chip for some libraries (enough for llama.cpp, not PyTorch) per the same thread. That doesn't apply on Manjaro.
- **The `HSA_OVERRIDE_GFX_VERSION=10.3.0` hack is unreliable or invalid on RDNA1.** A llama.cpp maintainer explains it is not valid on RDNA1, because the RDNA2 target it pretends to be has instructions RDNA1 lacks ([llama.cpp #20564](https://github.com/ggml-org/llama.cpp/issues/20564), where a similar RDNA1 card on ROCm 7.2 hit `HSA_STATUS_ERROR_MEMORY_APERTURE_VIOLATION`). The rocmfix table lists RDNA1 cards as "No working override, use Vulkan" ([rocmfix](https://github.com/xanpavle/rocmfix)). Older reports had partial success with PyTorch 1.13 and memory faults with 2.x ([ROCm #1735](https://github.com/ROCm/ROCm/issues/1735)). Someone also reported CTranslate2 ROCm forks giving numerically wrong transcripts on RDNA4 ([WhisperLive #520](https://github.com/collabora/WhisperLive/issues/520)). **Verdict: don't do it.**
- **Alternative: whisper.cpp with Vulkan** (`cmake -B build -DGGML_VULKAN=ON`) works on AMD through Mesa RADV with no ROCm needed. I found no published RDNA1 card numbers. Reported: on a Radeon 8060S iGPU, Vulkan base.en transcribed an 11 s clip in 0.65 s, about 17× real-time ([WhisperLive #520](https://github.com/collabora/WhisperLive/issues/520)). whisper.cpp 1.8.3 claims a 3–4× real-time factor improvement on iGPUs over CPU ([Phoronix](https://www.phoronix.com/news/Whisper-cpp-1.8.3-12x-Perf)).
- **Is it worth it here?** Probably not yet. The live tier uses `tiny` and the final tier uses `base`, both small and already fast on an 8-core CPU, while the LLM takes about 9 s per turn. Moving STT to the GPU would also take VRAM away from the LLM. *Estimate (low confidence):* it saves a few hundred ms per turn at most. The project docs' CPU path (Moonshine streaming for the live tier) is still the better STT upgrade.

### 3.2 Ollama / llama.cpp: llama3.1:8b and nomic-embed-text on the RDNA1 card

- **Vulkan status (late 2026):** Ollama added Vulkan in 0.12.6 (experimental) and 0.12.11 ([Phoronix](https://www.phoronix.com/news/ollama-0.12.11-Vulkan)). Current docs say "Vulkan is enabled by default when the backend is installed". `OLLAMA_VULKAN=0` or `GGML_VK_VISIBLE_DEVICES=-1` turns it off, and **no flag is needed to turn it on** ([docs.ollama.com/gpu](https://docs.ollama.com/gpu)). the dev box confirms this: Vulkan is active with no unit overrides.
- **ROCm in Ollama:** its bundled `rocm_v7_2` explicitly drops this RDNA1 chip (see the log line above). The log suggests `HSA_OVERRIDE_GFX_VERSION`, but per §3.1 that is not valid on RDNA1. Reported on a similar RDNA1 card anyway: llama.cpp **Vulkan beat ROCm** (pp512 302 vs 104 t/s, tg128 about 31.4 vs 23.4 t/s, Qwen 9B Q4_K_M) ([cryptoprofi](https://www.cryptoprofi.info/?p=19698)).
- **Does 8B Q4_K_M fit?** **Not in 4 GB.** Weights are about 4.6 GiB (CPU 3,128 + GPU 1,557 MiB in the load log). KV cache at 4096 ctx is 512 MiB (f16, 128 KiB/token), plus about 130 MiB compute. On a card with 8 GB it would fit with room for context. The docs' "about 30 t/s" claim comes from an **8 GB** similar RDNA1 card running a 9B model (Qwen3.5-9B Q4_K_M, tg128 30.22, pp512 350.72 t/s, [zenn.dev](https://zenn.dev/c4n4242/articles/75659aebdd62d4)), **so it does not apply to this 4 GB card with this model.**
- **Partial offload (`num_gpu` layers):** already happening automatically (10/33 layers). Measured generation is still 4.4–6.8 t/s. Prompt eval is about 104 t/s. With about 70% of the weights on the CPU, the CPU's memory bandwidth sets the speed. *Estimate (low confidence):* lowering llama.cpp's free-VRAM margin (`LLAMA_ARG_FIT_TARGET`, default 1024 MiB, [llama.cpp fit docs](https://github.com/ggml-org/llama.cpp/blob/HEAD/tools/cli/README.md)) and keeping the embed model off the GPU might fit about 14–16 layers, worth perhaps +10–30% generation speed (about 7–8 t/s). It's a small gain with a risk of out-of-memory errors when Chrome's VRAM use spikes.
- **Smaller models that fit fully in VRAM (about 2.9 GB free):**
  - *Estimate (medium confidence):* fully on the GPU, a 3B model reaches 30–60 t/s generation and several hundred t/s prompt eval (the 9B reached 300–350 t/s pp512, and a 3B is about 3× fewer FLOPs). The orchestrator call would drop from about 7 s to about 1–2 s and the whole turn's LLM time from about 9 s to about 2–3 s.
  - The catch is quality. The orchestrator relies on tool calls (answer / PC tool / delegate). Smaller models are weaker at this, so it must be tested with `scripts/e2e_agent.py`.
- **nomic-embed-text:** 274 MB and fast on the CPU. On the GPU it competes with the LLM for VRAM (the log shows it squeezed to 7/13 layers). **Recommendation:** run it on the CPU (`options={"num_gpu": 0}` on embed calls) so the chat model gets the VRAM. *Estimate:* embedding latency stays in the tens of ms (not measured).
- **RDNA1 limits to know:** RDNA1 has no matrix cores (`matrix cores: none`) and no bf16. Prompt processing slows sharply with long context. Reported on a similar RDNA1 card: pp512 at 4k KV depth 245 t/s, at 16k 110 t/s, at 65k 32 t/s ([zenn.dev](https://zenn.dev/c4n4242/articles/75659aebdd62d4)). **Keep the context at 4k or less** for this agent. The same source notes the first run after idle is slow (clocks ramping up), so warm up before benchmarking.

### 3.3 Piper / Kokoro (onnxruntime, sherpa-onnx) on the GPU?

- **onnxruntime ROCm EP:** removed from the source tree in ORT 1.23 ([ORT ROCm EP docs](https://onnxruntime.ai/docs/execution-providers/ROCm-ExecutionProvider.html), [PR #25181](https://github.com/microsoft/onnxruntime/pull/25181), [v1.23.0 notes](https://github.com/microsoft/onnxruntime/releases/tag/v1.23.0)). AMD now points to **MIGraphX** ([MIGraphX EP](https://onnxruntime.ai/docs/execution-providers/MIGraphX-ExecutionProvider.html)), which needs ROCm and so doesn't work on this RDNA1 chip.
- **DirectML** is Windows-only. Not applicable.
- **WebGPU EP (Dawn on top of Vulkan):** ORT now ships a WebGPU plugin EP for native Linux (`pip install onnxruntime-ep-webgpu`, ORT ≥ 1.24.4) ([ORT WebGPU EP](https://onnxruntime.ai/docs/execution-providers/WebGPU-ExecutionProvider.html), [PyPI](https://pypi.org/project/onnxruntime-ep-webgpu/)). In theory this could run Piper or Silero on RADV. It's new (0.x), operator coverage for VITS/Kokoro graphs is unverified, and it would not help sherpa-onnx (Kokoro), which bundles its own ORT.
- **sherpa-onnx:** has a GPU provider for NVIDIA CUDA only. AMD support is an open request ([sherpa-onnx #196](https://github.com/k2-fsa/sherpa-onnx/issues/196)).
- **Worth it?** **No.** Piper medium is already faster than real-time on one core, and replies are spoken sentence by sentence. *Estimate (medium confidence):* GPU TTS saves under about 100–200 ms before first audio, while costing VRAM the LLM needs. Revisit only if Kokoro becomes the default and is measurably slow.
- **Silero VAD / openWakeWord:** tiny models with a per-frame CPU cost. A GPU adds transfer overhead and gives no benefit.

## 4. VRAM and RAM budget (4,080 MiB card)

Figures marked "measured" come from today's Ollama logs and sysfs. Sizes for models not on the dev box are approximate download sizes.

| Component | VRAM if on GPU | RAM if on CPU | Fits on GPU now? |
|---|---|---|---|
| Desktop (KWin, Plasma, Chrome) baseline | **about 1,130 MiB measured** | n/a | always resident |
| llama.cpp default free margin (`LLAMA_ARG_FIT_TARGET`) | 1,024 MiB kept free | n/a | tunable |
| llama3.1:8b Q4_K_M full (4k ctx, f16 KV) | about 5,000 MiB (projected 5,019 measured) | about 5–6 GB | **No.** About 10 layers / 1.8 GiB today |
| Llama 3.2 3B Q4_K_M (4k ctx) | about 2.0 GB + 0.45 GB KV + about 0.15 GB compute ≈ 2.6 GB | about 2.6 GB | **Yes**, if the fit margin is reduced to about 256 MiB or ctx is 2k |
| Qwen3 4B Q4_K_M (4k ctx) | about 2.5 GB + 0.58 GB KV + compute ≈ 3.2 GB | about 3.2 GB | **No** at 4k f16. **Borderline** at 2k ctx or q8_0 KV |
| nomic-embed-text | about 308 MiB projected (measured) | about 0.3 GB | yes, but it competes with the LLM, so keep on CPU |
| whisper base (whisper.cpp Vulkan) | about 150–250 MB (estimate) | about 0.2–0.4 GB | possible, low value |
| whisper tiny | about 75–150 MB (estimate) | about 0.1–0.2 GB | possible, low value |
| Piper / Kokoro / Silero / openWakeWord | n/a (no usable GPU path) | about 0.1–0.3 GB each | stay on CPU |

**Recommended simultaneous layout (estimate):**
- **GPU:** desktop about 1.1 GB + Llama 3.2 3B at about 2.6 GB = about 3.7 GB of 4.0 GB, with a fit margin of about 256–384 MiB.
- **CPU and RAM:** Whisper tiny and base, Silero, Piper/Kokoro, nomic-embed-text, the app itself, and llama3.1:8b on demand for "hard" requests if a two-tier setup is kept.
- **RAM total:** under about 10 GB of ample, so system RAM is not a constraint.

## 5. Ranked upgrade path

Speed-ups are estimates from the cited sources plus today's measured baseline. None are measured on the dev box yet.

| # | Change | Expected effect (estimate) | Effort | Confidence |
|---|---|---|---|---|
| 1 | **Switch the orchestrator / chat model to a small model that fits fully in VRAM** (Llama 3.2 3B Q4_K_M first, Qwen3 4B as second candidate), still through Ollama's existing Vulkan backend. Just `ollama pull` plus `OLLAMA_MODEL=`. Keep llama3.1:8b as an optional "quality" model on CPU+partial GPU. | Generation about 6 → **30–60 t/s** (5–10×). Turn LLM time about 9 s → **about 2–3 s**. Reply after speech about 8–12 s → **about 3–5 s** | Low (config + e2e tests). Main risk is tool-calling quality | Medium (speed); Low (quality) |
| 2 | **Keep nomic-embed-text on the CPU** (`num_gpu: 0` on embed requests) and **stop the two models fighting over VRAM.** Optionally `OLLAMA_MAX_LOADED_MODELS` and keep-alive tuning so the LLM stays loaded (avoid reload stalls). | Frees about 300 MiB for the LLM and avoids eviction/reload hiccups of seconds. Embedding speed about unchanged | Very low (one option in agent embed call) | Medium-High |
| 3 | **Tune Ollama memory for the 4 GB card:** systemd drop-in with `LLAMA_ARG_FIT_TARGET` about 256–384, `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`, explicit `num_ctx` 2048–4096 in requests (the project currently sends none). | Lets the 3B/4B model fit with headroom. With the 8B model it may fit about 14–16 layers for about +10–30% (about 7–8 t/s) | Low (needs sudo for the drop-in, then an Ollama restart) | Medium |
| 4 | **Trim the prompt** of the first orchestrator call (about 340 tokens per turn, measured). It's a non-GPU change that compounds with #1 | Each 100 tokens saved = about 1 s today on 8B (measured 104 t/s), about 0.1–0.3 s on 3B GPU | Low–Medium | Medium |
| 5 | **Optional: llama.cpp `llama-server` (Vulkan) directly** instead of Ollama, behind an OpenAI-compatible backend option. Only worth it for flags Ollama doesn't expose, or if benchmarks show Ollama overhead | Small, about 0–15% over Ollama Vulkan (same ggml backend) | Medium (build + service + code path) | Low |
| 6 | **STT: Moonshine streaming for the live tier on CPU** (already in the docs' roadmap); whisper.cpp Vulkan only if STT ever becomes the bottleneck | Live captions faster/more accurate. GPU STT about a few hundred ms at most | Medium | Low–Medium |
| 7 | **TTS on GPU (ORT WebGPU EP)** | Negligible | Medium–High, experimental | Low. **Not recommended** |
| ✗ | ROCm / `HSA_OVERRIDE_GFX_VERSION` / source-built ROCm for this RDNA1 chip | Negative (instability, wrong numerics, Vulkan is faster anyway) | High | High that it's not worth it |
| ✗ | 8B fully on GPU | Needs an 8 GB+ card (for example RX 6600/7600 class, or the 8 GB variant of a similar RDNA1 card gets about 30 t/s on 9B per zenn.dev) | Hardware purchase | High |

## 6. Risks and complications

- **Keep docs in sync:** after kernel/Mesa/Ollama upgrades, re-record VRAM and t/s in `docs/upgrade-research.md` and this file.
- **Kernel updates:** amdgpu and RADV behaviour can change between kernel point releases. Take a baseline benchmark **before** a kernel update and repeat it after. Keep an LTS kernel as a fallback boot entry.
- **Mesa updates** (rolling distributions) can change RADV compute performance either way. Valve's RADV work improved llama.cpp pp on RDNA ([Phoronix](https://www.phoronix.com/news/RADV-Valve-Boost-Llama.cpp)), but regressions happen. Record the Mesa version with each benchmark.
- **Ollama is a script install in `/usr/local`**, not pacman, so it doesn't update with the system. Upgrading it can change the bundled ggml/Vulkan backend. Pin the version and re-benchmark on upgrade.
- **Shared VRAM with the desktop:** Chrome/KWin usage fluctuates (about 1.1 GB now). If the LLM is sized to the last few hundred MiB, a heavy Chrome tab can cause allocation failures or fallback to the CPU. Keep a margin of about 256 MiB or more. There's no iGPU to move the desktop to.
- **RDNA1 has no matrix cores:** prompt processing degrades fast with long context. Keep ctx ≤ 4k and the RAG context trimmed.
- **GPU clock ramp:** short requests after idle can run at low clocks (reported up to about 43% slower generation on the first run, [zenn.dev](https://zenn.dev/c4n4242/articles/75659aebdd62d4)). Warm up before measuring. For real use, consider a keep-warm ping.
- **Model quality:** 3B/4B models are weaker at tool calls and multi-step delegation than 8B. A two-tier router (small model for routing and quick answers, 8B for research summaries) may be needed.
- **Thermals/power:** small entry-level cards often have small coolers. Sustained GPU load may be new for the machine, so watch `sensors` during benchmarks.

## 7. What not to do

- Don't install ROCm or use `HSA_OVERRIDE_GFX_VERSION` for this card.
- Don't build CTranslate2 from source for this RDNA1 chip.
- Don't buy into the "about 30 t/s with 8B" claim for this card. That figure is from an 8 GB card with a model that fits.

## 8. Benchmark plan (later, after approval; read-mostly)

1. **Baseline, before any kernel/driver update.** Record `uname -r`, Mesa version, Ollama version and VRAM used. Run a fixed script of 10 voice-style prompts through `scripts/e2e_agent.py`, and capture from the Ollama journal: prompt eval t/s, eval t/s, and total turn latency (speech end → first audio).
2. **8B tuning.** Set `LLAMA_ARG_FIT_TARGET=384`, `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`, embeddings on CPU. Record layers offloaded and t/s.
3. **Small models.** `ollama pull llama3.2:3b` (and `qwen3:4b`). Confirm `ollama ps` shows 100% GPU. Same 10 prompts. Record t/s, latency, and **tool-call accuracy** (correct action chosen / total).
4. **Raw backend check (optional).** `llama-bench -p 512 -n 128 -ngl 99 -r 3` with one warm-up run, using a Vulkan llama.cpp build, to compare against the Ollama numbers and the zenn.dev/cryptoprofi figures.
5. **Repeat after kernel/driver updates.** Compare against step 1.
6. **STT (only if needed).** Time faster-whisper `base` int8 on CPU vs whisper.cpp Vulkan `base` on 5 recorded utterances.
7. Write results to `docs/upgrade-research.md` (measured, with dates) and decide on the default model.

## 9. Sources

- Ollama hardware support (Vulkan on by default, `OLLAMA_VULKAN=0` to disable): https://docs.ollama.com/gpu
- Ollama 0.12.11 Vulkan: https://www.phoronix.com/news/ollama-0.12.11-Vulkan
- Ollama AMD backends overview: https://local-ai-experts.com/en/wiki/ollama-amd-gpu-options
- Similar RDNA1 card (8 GB variant) llama.cpp Vulkan benchmarks, Qwen3.5-9B, KV-depth scaling, clock ramp: https://zenn.dev/c4n4242/articles/75659aebdd62d4
- Similar RDNA1 card Vulkan vs ROCm llama.cpp: https://www.cryptoprofi.info/?p=19698
- llama.cpp Vulkan perf discussion: https://github.com/ggml-org/llama.cpp/discussions/10879
- RADV improvements for llama.cpp: https://www.phoronix.com/news/RADV-Valve-Boost-Llama.cpp
- llama.cpp ROCm on similar RDNA1 card failure / override invalid on RDNA1: https://github.com/ggml-org/llama.cpp/issues/20564
- AMD: no this RDNA1 chip ROCm plans: https://github.com/ROCm/ROCm/issues/1735
- HSA override table (RDNA1: no working override): https://github.com/xanpavle/rocmfix
- llama.cpp `--fit-target` (default 1024 MiB): https://github.com/ggml-org/llama.cpp/blob/HEAD/tools/cli/README.md
- Llama 3.2 3B on similar RDNA1 card user report: https://www.reddit.com/r/LocalLLaMA/comments/1nndqcb/im_curious_of_your_setups/
- CTranslate2 hardware support: https://opennmt.net/CTranslate2/hardware_support.html
- CTranslate2 ROCm PR: https://github.com/OpenNMT/CTranslate2/pull/1989
- CTranslate2 ROCm wheel targets (Strix Halo recipe): https://github.com/nabe2030/faster-whisper-rocm-strix-halo
- CTranslate2-rocm fork: https://github.com/arlo-phoenix/CTranslate2-rocm/blob/rocm/README_ROCM.md
- whisper.cpp Vulkan on AMD + numbers: https://github.com/collabora/WhisperLive/issues/520
- whisper.cpp 1.8.3 iGPU speed-up: https://www.phoronix.com/news/Whisper-cpp-1.8.3-12x-Perf
- whisper.cpp: https://github.com/ggml-org/whisper.cpp
- ORT ROCm EP (removed in 1.23): https://onnxruntime.ai/docs/execution-providers/ROCm-ExecutionProvider.html, https://github.com/microsoft/onnxruntime/pull/25181, https://github.com/microsoft/onnxruntime/releases/tag/v1.23.0
- ORT MIGraphX EP: https://onnxruntime.ai/docs/execution-providers/MIGraphX-ExecutionProvider.html
- ORT WebGPU EP / plugin: https://onnxruntime.ai/docs/execution-providers/WebGPU-ExecutionProvider.html, https://pypi.org/project/onnxruntime-ep-webgpu/
- sherpa-onnx AMD GPU request: https://github.com/k2-fsa/sherpa-onnx/issues/196

## 10. Tracked in issues

| Upgrade path item | Issue |
|---|---|
| Model selector and benchmark harness | [#1](https://github.com/mhaiprojects/Sahir-AI/issues/1), [#2](https://github.com/mhaiprojects/Sahir-AI/issues/2) (shipped), follow-ups [#32](https://github.com/mhaiprojects/Sahir-AI/issues/32) |
| 1. Small default model fully on the GPU, 8B as fallback | [#3](https://github.com/mhaiprojects/Sahir-AI/issues/3) |
| 2. Embeddings on the CPU | [#4](https://github.com/mhaiprojects/Sahir-AI/issues/4) |
| 3. Ollama memory tuning for 4 GB cards | [#5](https://github.com/mhaiprojects/Sahir-AI/issues/5) |
| 4. Trim the orchestrator prompt | [#6](https://github.com/mhaiprojects/Sahir-AI/issues/6) |
| 5. Optional llama.cpp server (Vulkan) backend | [#7](https://github.com/mhaiprojects/Sahir-AI/issues/7) |
| 6. Moonshine streaming STT | [#8](https://github.com/mhaiprojects/Sahir-AI/issues/8) |
| Fix the stale CPU-only claim in `docs/upgrade-research.md` (doc corrected 2026-11-23) | [#19](https://github.com/mhaiprojects/Sahir-AI/issues/19) |
| Move Ollama model storage off the root disk | [#31](https://github.com/mhaiprojects/Sahir-AI/issues/31) |
