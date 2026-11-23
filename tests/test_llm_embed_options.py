"""Ollama embedder passes num_gpu: 0 when EMBED_ON_CPU is set."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from agent import gpu_config as gpu_config_mod
from agent.llm import OllamaEmbedder


def test_ollama_embedder_cpu_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_ON_CPU", "true")
    gpu_config_mod.reset_gpu_config()
    captured: list[dict[str, Any]] = []

    client = MagicMock()

    def embed(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {"embeddings": [[0.1, 0.2]]}

    client.embed = embed
    # set _client after init to avoid real Ollama
    emb = OllamaEmbedder("http://127.0.0.1:11434", "nomic-embed-text")
    emb._client = client
    emb.embed_query("hello")
    assert captured[0]["options"] == {"num_gpu": 0}
    gpu_config_mod.reset_gpu_config()