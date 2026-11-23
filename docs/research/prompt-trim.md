# Prompt trim (item 21 / GitHub #6)

Model: **llama3.1:8b** (default chat model). Orchestrator measurement = system prompt + tool schemas + sample user turn (`scripts/measure_prompts.py`, Ollama `prompt_eval_count` with `num_predict: 1`).

## Token counts (prompt_eval_count)

| Prompt | Before | After | Δ |
|--------|--------|-------|---|
| Orchestrator (system + tools) | 756 | 451 | **−40%** |
| Supervisor (system + sample catalog) | 301 | 266 | −12% |
| Tier-2 router | 178 | 178 | 0% |

Char/4 estimates track Ollama within ~5% on the orchestrator bundle.

## First-call latency (measure script, warm model)

| Prompt | Before ms | After ms |
|--------|-----------|----------|
| Orchestrator | 16188* | 4243 |
| Supervisor | 2841 | 2693 |
| Tier-2 | 1838 | 1830 |

\*First orchestrator sample was cold-loaded; after-trim runs were on a warm model. Bench decision p50 (quick, 9 prompts): **5106 → 5219 ms** (within noise).

## Bench accuracy (`scripts/bench_models.py --quick --models llama3.1:8b`)

| Metric | Before | After |
|--------|--------|-------|
| Valid JSON % | 100.0 | 100.0 |
| Correct % | 100.0 | 100.0 |
| Effective correct % | 100.0 | 100.0 |

## Changes

- **`agent/orchestrator.py`**: shorter `ORCH_SYSTEM`; slim inline PC tool schemas (`_ORCH_PC_TOOLS`); compact `answer` / `delegate` tool definitions.
- **`agent/supervisor.py`**: compressed `SUPERVISOR_JSON_SYSTEM` (same tools, fewer words).
- **`agent/workers.py`**, **`agent/clickup.py`**: shorter worker catalog lines (still distinguish research / code / ClickUp).
- **`scripts/measure_prompts.py`**, **`agent/prompt_metrics.py`**: measurement helpers and CI ceilings.
- **`tests/test_prompt_sizes.py`**: estimate-based length guards.

Tier-2 router prompt was already small; left unchanged to avoid routing regressions.

## Re-verify

```bash
timeout 120 venv/bin/python scripts/measure_prompts.py
timeout 600 venv/bin/python scripts/bench_models.py --quick --models llama3.1:8b
timeout 120 venv/bin/python -m pytest -q tests/test_prompt_sizes.py
```