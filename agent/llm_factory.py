"""Construct chat LLM clients from Settings (Ollama default, optional OpenAI-compatible)."""

from __future__ import annotations

import logging
import os
from typing import Any

import ollama

from agent.llm import ChatLLM, OllamaChat
from agent.openai_compat import OpenAIChat
from agent.ollama_runtime import ollama_chat_kwargs
from config import Settings

logger = logging.getLogger(__name__)


def resolve_llm_backend(settings: Settings) -> str:
    raw = (os.getenv("LLM_BACKEND") or settings.llm_backend or "ollama").strip().lower()
    return raw if raw in ("ollama", "openai") else "ollama"


def chat_model_name(settings: Settings) -> str:
    if resolve_llm_backend(settings) == "openai":
        return settings.openai_model
    return settings.ollama_model


def build_chat_llm(settings: Settings) -> ChatLLM:
    backend = resolve_llm_backend(settings)
    if backend == "openai":
        if settings.openai_base_url_error:
            logger.warning("OPENAI_BASE_URL invalid (%s); falling back to Ollama", settings.openai_base_url_error)
            backend = "ollama"
        elif settings.openai_api_key_load_error:
            logger.warning("OpenAI API key load error (%s); falling back to Ollama", settings.openai_api_key_load_error)
            backend = "ollama"
    if backend == "openai":
        return OpenAIChat(
            settings.openai_base_url,
            settings.openai_model,
            api_key=settings.openai_api_key,
            timeout_s=settings.openai_timeout_seconds,
            model_getter=lambda: settings.openai_model,
        )
    return OllamaChat(
        settings.ollama_host,
        settings.ollama_model,
        model_getter=lambda: settings.ollama_model,
    )


def chat_completion(
    settings: Settings,
    model: str,
    messages: list[dict[str, Any]],
    *,
    client: Any | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Ollama-shaped chat completion for router / orchestrator paths."""
    if resolve_llm_backend(settings) == "openai":
        chat = OpenAIChat(
            settings.openai_base_url,
            model,
            api_key=settings.openai_api_key,
            timeout_s=settings.openai_timeout_seconds,
        )
        return chat.chat_completion(messages, **kwargs)
    ollama_client = client or ollama.Client(host=settings.ollama_host)
    return ollama_client.chat(model=model, messages=messages, **ollama_chat_kwargs(model, **kwargs))


def llm_status(settings: Settings) -> dict[str, Any]:
    backend = resolve_llm_backend(settings)
    out: dict[str, Any] = {
        "backend": backend,
        "chat_model": chat_model_name(settings),
        "router_stays_on_ollama": True,
    }
    if backend == "openai":
        out.update(
            {
                "openai_base_url": settings.openai_base_url,
                "openai_model": settings.openai_model,
                "openai_api_key_source": settings.openai_api_key_source,
                "openai_configured": bool(settings.openai_base_url) and not settings.openai_base_url_error,
                "openai_key_error": settings.openai_api_key_load_error,
                "openai_base_url_warning": settings.openai_base_url_warning,
            }
        )
    else:
        out["ollama_host"] = settings.ollama_host
        out["ollama_model"] = settings.ollama_model
    return out