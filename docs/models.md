# Chat models: selector, fallback and benchmark

The assistant can switch its Ollama chat model at runtime. Small models (0.6B–2B) answer
much faster on a modest GPU; a larger model stays available as a fallback for hard questions.

## Two-tier routing (small default + quality fallback)

On a **4 GB** GPU class machine (see `VRAM_*_GB` in `.env.example`):

| Tier | Model | Role |
|------|-------|------|
| **Default chat** | Auto-picked **1–4B** model that fits the chat VRAM budget | Every turn unless you pick another model in the UI; ranked by `data/bench/latest.json` **`effective_correct_pct`** when a benchmark exists, else by fit/size. **`FALLBACK_MODEL` is never auto-selected.** |
| **Quality fallback** | `FALLBACK_MODEL` (default `llama3.1:8b`) | One turn when you say “think harder”, when **Always use the big model** is on, when the small model returns invalid tool JSON (if auto-fallback is on), or when you force escalation. Activity panel warns that loading the 8B model may push the pinned **router** (`ROUTER_MODEL`) to CPU. |

The pinned router model (`qwen3:0.6b` by default) may also be the active chat model when it wins the
auto ranking — that keeps one small model in VRAM for both roles. If a better-fitting chat model
scores higher in the benchmark, auto-default prefers that instead.

`GET /api/models` returns `default_selection` (`active_source`, `fits_vram`, `bench_effective_correct_pct`,
`auto_default_model`, …) alongside per-model **VRAM tight** warnings.

## Suggested models

```bash
ollama pull qwen3:0.6b
ollama pull qwen3:1.7b
ollama pull llama3.2:1b
ollama pull qwen3.5:2b
ollama pull llama3.1:8b        # quality fallback (FALLBACK_MODEL), not the auto default
```

Qwen3-family models have a "thinking" mode; the assistant always sends `think: false` for
them (`agent.models.think_kwargs`) so voice replies are not delayed by hidden reasoning.

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `OLLAMA_MODEL` | `llama3.1:8b` | Env default before first auto pick; overridden by auto-default when `auto_default` is on |
| `FALLBACK_MODEL` | `llama3.1:8b` | Quality fallback for escalation (never chosen as auto default) |
| `MODEL_BENCH_PATH` | `data/bench/latest.json` | Benchmark file used to rank auto-default candidates |
| `MODEL_KEEP_ALIVE` | `30m` | How long **Load** keeps a model in memory |
| `MODEL_STATE_PATH` | `data/model_state.json` | Persisted runtime choice (gitignored) |
| `OLLAMA_NUM_CTX` | `4096` | `num_ctx` sent on every Sahir chat/generate call ([#5](https://github.com/mhaiprojects/Sahir-AI/issues/5)) |
| `EMBED_ON_CPU` | `true` | `num_gpu: 0` on Ollama embed calls so `nomic-embed-text` stays off the GPU ([#4](https://github.com/mhaiprojects/Sahir-AI/issues/4)) |
| `ROUTER_MODEL` | `qwen3:0.6b` | Model pinned at startup for the future utterance router |
| `ROUTER_NUM_CTX` | `1024` | Context window for router pin / keep-warm |
| `ROUTER_PIN_ENABLED` | `true` | Load router model at web server start (`keep_alive: -1`) |
| `ROUTER_KEEP_WARM_SEC` | `120` | Periodic minimal generate to avoid RDNA1 clock ramp |
| `VRAM_*_GB` | see `.env.example` | Budget used for **VRAM tight** warnings on this page |

### Ollama service tuning (4 GB AMD / Vulkan)

Sahir cannot change the Ollama daemon's environment. For an RX 5500–class **4 GB** card, set on the
**Ollama service** (then restart Ollama yourself):

| Variable | Suggested | Notes |
|----------|-----------|--------|
| `OLLAMA_FLASH_ATTENTION` | `1` | Lower KV RAM |
| `OLLAMA_KV_CACHE_TYPE` | `q8_0` | Quantised KV |
| `LLAMA_ARG_FIT_TARGET` | `256`–`384` | MiB free VRAM margin for llama.cpp fit |

Example user systemd drop-in (paths vary by install): see comments in `.env.example`. Full rationale:
[upgrade-research.md](./upgrade-research.md) and [gpu-acceleration-research.md](./research/gpu-acceleration-research.md).

## Models view (web UI)

Open the ◈ **Models** item in the left nav rail:

- **Select** (radio) — makes that model the active chat model for the router, orchestrator,
  streaming chat, memory extraction and the research worker. Persisted across restarts.
- **Load / Reload** — preloads the model (`/api/generate` with `keep_alive`), shows how long it took.
- **Eject** — unloads it (`keep_alive: 0`).
- Each card shows size on disk, parameters, quantisation and, when loaded, the in-memory size
  split between VRAM and RAM (GPU %). Badges mark **Active**, **Fallback**, **Default**, and
  **VRAM tight** when the on-disk model is larger than the configured chat budget (router +
  embeddings + desktop reserved).
- A red banner appears when the **active** model would likely evict the pinned router model.
- **Auto-fallback** — retry with the fallback model when the small model returns an invalid
  action (unknown tool, unparseable JSON, missing required argument, invalid enum, or a tool
  call written as plain text).
- **Always use the big model** — force the fallback model for every turn.
- Saying "think harder", "use the big model" or "smarter model" uses the fallback for that turn.
- Every reply in the chat shows a small tag with the model that answered (and why it escalated);
  the same data is in `action_details.model` / `action_details.escalated`.
- **Model store** — path to Ollama's `OLLAMA_MODELS` directory and free/total disk space on that
  volume; warns when space is low or the store is on the root filesystem (before you `ollama pull`).
- **Run benchmark / Quick run** — runs `scripts/bench_models.py` in the background and shows the
  results table (full report in `data/bench/latest.md`).

## API

| Method | Path | Body | Purpose |
|--------|------|------|---------|
| GET | `/api/models` | – | Installed chat models, loaded state, `model_storage`, `default_selection`, `vram_budget`, VRAM warnings |
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

## Ollama model storage location (#31)

Pulled models live under **`OLLAMA_MODELS`** on the Ollama **service** (not in the Sahir repo). On
**buspc** this is already on a large disk: `/mnt/ssd6/ollama/models` via a systemd drop-in.

The **Models** page shows that path plus free/total space (from the nearest existing parent of the
store) before you pull more models. Warnings appear when free space is below **`OLLAMA_MODELS_WARN_FREE_GB`**
(default **10**) or when the store sits on the **root** filesystem.

Sahir resolves the path read-only in order:

1. `OLLAMA_MODELS_DIR` in Sahir `.env` (override for display only)
2. `OLLAMA_MODELS=…` from `systemctl show ollama -p Environment` (no sudo)
3. `/usr/share/ollama/.ollama/models` if the `ollama` unit is loaded
4. `~/.ollama/models` for a user-run daemon

### Moving model storage off the root disk (admin / sudo)

Requires **root**: Sahir does not change Ollama or systemd.

```bash
sudo systemctl edit ollama
```

Example drop-in:

```ini
[Service]
Environment="OLLAMA_MODELS=/mnt/ssd6/ollama/models"
RequiresMountsFor=/mnt/ssd6
```

Then (paths and user vary by distro):

```bash
sudo mkdir -p /mnt/ssd6/ollama/models
sudo rsync -a /usr/share/ollama/.ollama/models/ /mnt/ssd6/ollama/models/
sudo chown -R ollama:ollama /mnt/ssd6/ollama
sudo systemctl daemon-reload
sudo systemctl restart ollama
ollama list
systemctl show ollama -p Environment
```

On **buspc**, `OLLAMA_MODELS=/mnt/ssd6/ollama/models` is already configured; use the Models page to
confirm free space before large pulls.

| Variable | Default | Meaning |
|----------|---------|---------|
| `OLLAMA_MODELS_DIR` | (unset) | Override path shown in the UI (optional) |
| `OLLAMA_MODELS_WARN_FREE_GB` | `10` | Warn when free space on the store volume is below this |

## Notes

- Check free space on the model store volume before pulling several models (Models page or `df`).
- On a GPU with little VRAM, keep only one chat model loaded at a time; a second loaded model pushes the
  first partly into system RAM and slows it down.
