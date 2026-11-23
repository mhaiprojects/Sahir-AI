"""Pick the default chat model: VRAM-fitting 1–4B, scored from benchmark data when available."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from agent.gpu_config import get_gpu_config
from agent.vram_budget import chat_vram_budget_bytes

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BENCH_PATH = PROJECT_ROOT / "data" / "bench" / "latest.json"
_EMBED_RE = re.compile(r"(embed|bge|minilm|e5-|nomic)", re.I)
_PARAM_RE = re.compile(r"([\d.]+)\s*([BMK])", re.I)


def bench_results_path() -> Path:
    raw = os.getenv("MODEL_BENCH_PATH", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_BENCH_PATH


def load_bench_scores(path: Path | None = None) -> dict[str, dict[str, Any]]:
    path = path or bench_results_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        logger.warning("Ignoring unreadable bench file %s: %s", path, exc)
        return {}
    rows = data.get("results") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("error") or not row.get("model"):
            continue
        name = str(row["model"])
        out[name] = row
        base, _, tag = name.partition(":")
        if tag:
            out.setdefault(base, row)
    return out


def _tag_base(name: str) -> str:
    return name.split(":", 1)[0]


def parameter_billions(model: dict[str, Any]) -> float | None:
    details = model.get("details") or {}
    raw = str(details.get("parameter_size") or "")
    m = _PARAM_RE.search(raw)
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2).upper()
    if unit == "B":
        return val
    if unit == "M":
        return val / 1000.0
    if unit == "K":
        return val / 1_000_000.0
    return None


def _param_band(params_b: float | None) -> int:
    """Sort key band: 0 = 1–4B sweet spot, 1 = unknown / tiny, 2 = larger than 4B."""
    if params_b is None:
        return 1
    if 1.0 <= params_b <= 4.0:
        return 0
    if params_b < 1.0:
        return 1
    return 2


def _bench_row(name: str, scores: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if name in scores:
        return scores[name]
    base = _tag_base(name)
    return scores.get(base)


def _sort_key(model: dict[str, Any], budget: int, scores: dict[str, dict[str, Any]]) -> tuple[Any, ...]:
    size = int(model.get("size") or 0)
    fits = size > 0 and size <= budget
    params = parameter_billions(model)
    sc = _bench_row(model.get("name", ""), scores)
    eff = sc.get("effective_correct_pct") if sc else None
    dec = (sc.get("decision_ms") or {}).get("p50") if sc else None
    return (
        0 if fits else 1,
        _param_band(params),
        0 if eff is not None else 1,
        -(float(eff) if eff is not None else 0.0),
        float(dec) if dec is not None else 999_999.0,
        size if size else 1 << 62,
        model.get("name", ""),
    )


def _is_chat_model(model: dict[str, Any]) -> bool:
    name = model.get("name") or ""
    return bool(name) and not _EMBED_RE.search(name)


def choose_auto_default(
    installed: list[dict[str, Any]],
    *,
    fallback_model: str,
    router_model: str | None = None,
    bench_path: Path | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Return (model_name, meta). Never returns ``fallback_model`` as the auto default."""
    router_model = router_model or get_gpu_config().router_model
    fallback_base = _tag_base(fallback_model)
    eligible = [
        m for m in installed
        if _is_chat_model(m) and _tag_base(m.get("name", "")) != fallback_base
    ]
    if not eligible:
        return None, {"reason": "no_eligible_chat_models", "fits_vram": False}

    budget = chat_vram_budget_bytes()
    scores = load_bench_scores(bench_path)
    fitting = [m for m in eligible if int(m.get("size") or 0) <= budget]
    pool = fitting if fitting else eligible
    ranked = sorted(pool, key=lambda m: _sort_key(m, budget, scores))
    if not ranked:
        return None, {"reason": "no_ranked_candidate", "fits_vram": False}

    router_base = _tag_base(router_model)
    best = ranked[0]
    router_entries = [m for m in ranked if _tag_base(m.get("name", "")) == router_base]
    non_router = [m for m in ranked if _tag_base(m.get("name", "")) != router_base]

    pick = best
    picked_better_than_router = False
    if non_router and router_entries:
        if _sort_key(non_router[0], budget, scores) < _sort_key(router_entries[0], budget, scores):
            pick = non_router[0]
            picked_better_than_router = True

    name = str(pick.get("name", ""))
    size = int(pick.get("size") or 0)
    sc = _bench_row(name, scores)
    eff = sc.get("effective_correct_pct") if sc else None
    params = parameter_billions(pick)
    meta: dict[str, Any] = {
        "reason": "bench_ranked" if eff is not None else ("vram_fit" if size <= budget else "vram_overflow_smallest"),
        "fits_vram": size <= budget,
        "parameter_b": params,
        "bench_effective_correct_pct": eff,
        "chat_budget_bytes": budget,
        "router_model": router_model,
        "picked_better_than_router": picked_better_than_router,
        "bench_path": str(bench_path or bench_results_path()) if (bench_path or bench_results_path()).is_file() else None,
    }
    return name, meta