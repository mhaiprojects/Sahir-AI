# Chat models: selector, fallback and benchmark

The assistant can switch its Ollama chat model at runtime. Small models (0.6B–2B) answer
much faster on a modest GPU; a larger model stays available as a fallback for hard questions.

## Suggested models

```bash
ollama pull qwen3:0.6b
ollama pull qwen3:1.7b
ollama pull llama3.2:1b
ollama pull qwen3.5:2b
ollama pull llama3.1:8b        # default + fallback
```

Qwen3-family models have a "thinking" mode; the assistant always sends `think: false` for
them (`agent.models.think_kwargs`) so voice replies are not delayed by hidden reasoning.

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `OLLAMA_MODEL` | `llama3.1:8b` | Default chat model (used until you pick another one) |
| `FALLBACK_MODEL` | `llama3.1:8b` | Big model used for escalation |
| `MODEL_KEEP_ALIVE` | `30m` | How long **Load** keeps a model in memory |
| `MODEL_STATE_PATH` | `data/model_state.json` | Persisted runtime choice (gitignored) |

## Models view (web UI)

Open the ◈ **Models** item in the left nav rail:

- **Select** (radio) — makes that model the active chat model for the router, orchestrator,
  streaming chat, memory extraction and the research worker. Persisted across restarts.
- **Load / Reload** — preloads the model (`/api/generate` with `keep_alive`), shows how long it took.
- **Eject** — unloads it (`keep_alive: 0`).
- Each card shows size on disk, parameters, quantisation and, when loaded, the in-memory size
  split between VRAM and RAM (GPU %). Badges mark **Active**, **Fallback**, **Default**.
- **Auto-fallback** — retry with the fallback model when the small model returns an invalid
  action (unknown tool, unparseable JSON, missing required argument, invalid enum, or a tool
  call written as plain text).
- **Always use the big model** — force the fallback model for every turn.
- Saying "think harder", "use the big model" or "smarter model" uses the fallback for that turn.
- Every reply in the chat shows a small tag with the model that answered (and why it escalated);
  the same data is in `action_details.model` / `action_details.escalated`.
- **Run benchmark / Quick run** — runs `scripts/bench_models.py` in the background and shows the
  results table (full report in `data/bench/latest.md`).

## API

| Method | Path | Body | Purpose |
|--------|------|------|---------|
| GET | `/api/models` | – | Installed chat models, loaded state (`size`, `size_vram`, `size_ram`, `gpu_percent`), active/fallback/default |
| POST | `/api/models/select` | `{"model": "qwen3:1.7b"}` | Set the active chat model |
| POST | `/api/models/load` | `{"model": "...", "keep_alive": "30m"}` | Preload into memory |
| POST | `/api/models/eject` | `{"model": "..."}` | Unload from memory |
| POST | `/api/models/options` | `{"auto_fallback": true, "force_fallback": false, "fallback_model": "..."}` | Fallback policy |
| GET | `/api/models/benchmark` | – | Benchmark status + latest results |
| POST | `/api/models/benchmark` | `{"models": [], "quick": false}` | Start a benchmark run |

## Benchmark

```bash
venv/bin/python scripts/bench_models.py                     # all installed chat models
venv/bin/python scripts/bench_models.py --models qwen3:1.7b,llama3.1:8b --quick
```

Per model it ejects other chat models, then measures cold load time, GPU share, time to first
token and tokens/s on short spoken replies, and the latency and correctness of the real
orchestrator decision call (same system prompt, tools and options as `agent/orchestrator.py`)
over the labelled prompts in `scripts/bench_prompts.json`. Nothing is executed; decisions are
only inspected. "Correct w/ heuristic" also applies the orchestrator's production keyword
fallback. Results: `data/bench/<timestamp>.json`, `latest.json`, `latest.md`.

## Notes

- Ollama keeps models in its own store (`OLLAMA_MODELS`, default under the service user's home).
  Check free space on that filesystem before pulling several models.
- On a GPU with little VRAM, keep only one chat model loaded at a time; a second loaded model pushes the
  first partly into system RAM and slows it down.
