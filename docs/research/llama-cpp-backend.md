# OpenAI-compatible LLM backend (llama-server)

_Baseline from `scripts/bench_llm_backend.py` on this machine._

## When it is worth switching

- Measured Ollama scheduling/load overhead dominates TTFT for your chat model.
- You need a runtime flag Ollama does not expose (e.g. fixed `-ngl`, custom KV, grammar).
- You run **chat only** on llama-server while **Ollama keeps** `nomic-embed-text` and pinned `qwen3:0.6b` router tiers.

## VRAM on 4 GB (RX 5500)

Ollama already pins the router (~0.7 GB) and nomic on CPU. A second GPU server for chat
competes for the ~2.5 GB left — **do not run both chat stacks on GPU without Michael's approval**.

## Build llama-server (Vulkan, user install — no sudo)

```bash
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
cmake -B build -DGGML_VULKAN=1
cmake --build build -j
```

Example server (after you have a GGUF locally):

```bash
./build/bin/llama-server -m /path/to/model.gguf --port 8080 -ngl 99 -c 4096
```

Sahir: `LLM_BACKEND=openai`, `OPENAI_BASE_URL=http://127.0.0.1:8080/v1`, `OPENAI_MODEL=<id from /v1/models>`.

## Benchmark procedure

Uses one **warm-up** run and **5** measured streaming samples (~150-word prompt, `max_tokens=200`, `temperature=0`). Runs are **invalid** if completion is under ~50 tokens.

```bash
timeout 300 venv/bin/python scripts/bench_llm_backend.py
```

Ollama model used for baseline: `llama3.2:1b`.

| Backend | TTFT p50 (ms) | Total p50 (ms) | tok/s p50 (server wall) |
|---------|--------------:|---------------:|------------------------:|
| ollama | 32.5 | 5434 | 37.0 (server) |
| openai | — | — | SKIP: LLM_BACKEND is not openai |

- Completion tokens p50: **200** (server)
- TTFT p95: 33.6 ms

Raw JSON: `data/bench/llm_backend.json`.
