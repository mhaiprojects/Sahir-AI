"""Shared Ollama request options (num_ctx, embed on CPU)."""

from __future__ import annotations

from typing import Any

from agent.gpu_config import get_gpu_config


def embed_options() -> dict[str, Any]:
    if get_gpu_config().embed_on_cpu:
        return {"num_gpu": 0}
    return {}


def chat_options(*, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    opts: dict[str, Any] = {"num_ctx": get_gpu_config().ollama_num_ctx}
    if extra:
        opts.update(extra)
    return opts


def ollama_chat_kwargs(model: str, **kwargs: Any) -> dict[str, Any]:
    """Merge thinking-mode kwargs with default chat ``options`` (explicit num_ctx)."""
    from agent.models import think_kwargs

    out = dict(think_kwargs(model))
    base = chat_options()
    if "options" in kwargs:
        base = {**base, **kwargs.pop("options")}
    out["options"] = base
    out.update(kwargs)
    return out