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
    vram_total_gb: float = 4.0
    vram_router_gb: float = 0.7
    vram_embed_gb: float = 0.3
    vram_desktop_gb: float = 1.1

    @classmethod
    def from_env(cls) -> "GpuRuntimeConfig":
        return cls(
            embed_on_cpu=_bool("EMBED_ON_CPU", True),
            ollama_num_ctx=_int("OLLAMA_NUM_CTX", 4096),
            router_model=os.getenv("ROUTER_MODEL", "qwen3:0.6b").strip() or "qwen3:0.6b",
            router_num_ctx=_int("ROUTER_NUM_CTX", 1024),
            router_pin_enabled=_bool("ROUTER_PIN_ENABLED", True),
            router_keep_warm_sec=_float("ROUTER_KEEP_WARM_SEC", 120.0),
            vram_total_gb=_float("VRAM_TOTAL_GB", 4.0),
            vram_router_gb=_float("VRAM_ROUTER_GB", 0.7),
            vram_embed_gb=_float("VRAM_EMBED_GB", 0.3),
            vram_desktop_gb=_float("VRAM_DESKTOP_GB", 1.1),
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