"""VRAM budget math for chat models alongside a pinned router + CPU embeddings."""

from __future__ import annotations

from typing import Any

from agent.gpu_config import GpuRuntimeConfig, get_gpu_config


def model_fits_vram(size_bytes: int, cfg: GpuRuntimeConfig | None = None) -> bool:
    if size_bytes <= 0:
        return False
    return size_bytes <= chat_vram_budget_bytes(cfg)


def chat_vram_budget_bytes(cfg: GpuRuntimeConfig | None = None) -> int:
    cfg = cfg or get_gpu_config()
    avail_gb = cfg.vram_total_gb - cfg.vram_router_gb - cfg.vram_embed_gb - cfg.vram_desktop_gb
    return int(max(0.0, avail_gb) * 1_000_000_000)


def vram_warning_for_model(size_bytes: int, cfg: GpuRuntimeConfig | None = None) -> dict[str, Any] | None:
    """Return a warning dict when an on-disk chat model likely won't fit the chat VRAM budget."""
    if size_bytes <= 0:
        return None
    cfg = cfg or get_gpu_config()
    budget = chat_vram_budget_bytes(cfg)
    if size_bytes <= budget:
        return None
    return {
        "vram_tight": True,
        "message": (
            f"Model size {size_bytes / 1e9:.2f} GB exceeds the ~{budget / 1e9:.2f} GB chat VRAM budget "
            f"(after router ~{cfg.vram_router_gb} GB, embeddings ~{cfg.vram_embed_gb} GB, "
            f"desktop ~{cfg.vram_desktop_gb} GB of {cfg.vram_total_gb} GB total). "
            "Loading it may evict the router to CPU and slow routing."
        ),
        "model_size_bytes": size_bytes,
        "chat_budget_bytes": budget,
    }


def budget_summary(cfg: GpuRuntimeConfig | None = None) -> dict[str, Any]:
    cfg = cfg or get_gpu_config()
    budget = chat_vram_budget_bytes(cfg)
    return {
        "total_gb": cfg.vram_total_gb,
        "router_reserve_gb": cfg.vram_router_gb,
        "embed_reserve_gb": cfg.vram_embed_gb,
        "desktop_gb": cfg.vram_desktop_gb,
        "chat_budget_gb": round(budget / 1e9, 3),
        "chat_budget_bytes": budget,
    }