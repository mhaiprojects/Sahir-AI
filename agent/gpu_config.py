"""GPU / VRAM and Ollama runtime options (env-driven, application-level only)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


@dataclass(frozen=True)
class GpuRuntimeConfig:
    embed_on_cpu: bool = True
    ollama_num_ctx: int = 4096
    router_model: str = "qwen3:0.6b"
    router_num_ctx: int = 1024
    router_pin_enabled: bool = True
    router_keep_warm_sec: float = 120.0
    router_placement_check_sec: float = 60.0
    router_min_gpu_fraction: float = 0.9
    router_placement_reload_cooldown_sec: float = 300.0
    router_classify_grace_sec: float = 3.0
    vram_total_gb: float = 4.0
    vram_router_gb: float = 0.7
    vram_embed_gb: float = 0.3
    vram_desktop_gb: float = 1.1
    router_t1_enabled: bool = True
    router_t1_margin: float = 0.07
    router_t2_enabled: bool = True
    router_t2_timeout_ms: float = 400.0
    router_t2_confidence: float = 0.55
    router_t2_use_fallback: bool = False
    router_t2_fallback_model: str = "qwen3:1.7b"

    @classmethod
    def from_env(cls) -> "GpuRuntimeConfig":
        return cls(
            embed_on_cpu=_bool("EMBED_ON_CPU", True),
            ollama_num_ctx=_int("OLLAMA_NUM_CTX", 4096),
            router_model=os.getenv("ROUTER_MODEL", "qwen3:0.6b").strip() or "qwen3:0.6b",
            router_num_ctx=_int("ROUTER_NUM_CTX", 1024),
            router_pin_enabled=_bool("ROUTER_PIN_ENABLED", True),
            router_keep_warm_sec=_float("ROUTER_KEEP_WARM_SEC", 120.0),
            router_placement_check_sec=_float("ROUTER_PLACEMENT_CHECK_SEC", 60.0),
            router_min_gpu_fraction=_float("ROUTER_MIN_GPU_FRACTION", 0.9),
            router_placement_reload_cooldown_sec=_float("ROUTER_PLACEMENT_RELOAD_COOLDOWN_SEC", 300.0),
            router_classify_grace_sec=_float("ROUTER_CLASSIFY_GRACE_SEC", 3.0),
            vram_total_gb=_float("VRAM_TOTAL_GB", 4.0),
            vram_router_gb=_float("VRAM_ROUTER_GB", 0.7),
            vram_embed_gb=_float("VRAM_EMBED_GB", 0.3),
            vram_desktop_gb=_float("VRAM_DESKTOP_GB", 1.1),
            router_t1_enabled=_bool("ROUTER_T1_ENABLED", True),
            router_t1_margin=_float("ROUTER_T1_MARGIN", 0.07),
            router_t2_enabled=_bool("ROUTER_T2_ENABLED", True),
            router_t2_timeout_ms=_float("ROUTER_T2_TIMEOUT_MS", 400.0),
            router_t2_confidence=_float("ROUTER_T2_CONFIDENCE", 0.55),
            router_t2_use_fallback=_bool("ROUTER_T2_FALLBACK", False),
            router_t2_fallback_model=os.getenv("ROUTER_T2_FALLBACK_MODEL", "qwen3:1.7b").strip()
            or "qwen3:1.7b",
        )


_cached: GpuRuntimeConfig | None = None


def get_gpu_config() -> GpuRuntimeConfig:
    global _cached
    if _cached is None:
        _cached = GpuRuntimeConfig.from_env()
    return _cached


def reset_gpu_config() -> None:
    """Clear cached config (tests)."""
    global _cached
    _cached = None