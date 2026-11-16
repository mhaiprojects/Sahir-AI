"""Thin Ollama wrappers for embeddings and chat (swappable in tests)."""

from __future__ import annotations

import logging
from typing import Any, Iterator, Protocol

import ollama

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    model: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class ChatLLM(Protocol):
    model: str

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]: ...

    def chat_stream(self, messages: list[dict[str, str]], **kwargs: Any) -> Iterator[str]: ...


class OllamaEmbedder:
    """Ollama /api/embed client. nomic-embed-text needs task prefixes for best retrieval."""

    def __init__(self, host: str, model: str, batch_size: int = 16) -> None:
        self.model = model
        self.batch_size = batch_size
        self._client = ollama.Client(host=host)
        self._prefixed = "nomic" in model.lower()

    def _embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            resp = self._client.embed(model=self.model, input=batch)
            out.extend([list(map(float, v)) for v in resp["embeddings"]])
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._prefixed:
            texts = [f"search_document: {t}" for t in texts]
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        if self._prefixed:
            text = f"search_query: {text}"
        return self._embed([text])[0]


class OllamaChat:
    def __init__(self, host: str, model: str) -> None:
        self.model = model
        self._client = ollama.Client(host=host)

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        resp = self._client.chat(model=self.model, messages=messages, **kwargs)
        return {"content": (resp.get("message", {}) or {}).get("content", "") or ""}

    def chat_stream(self, messages: list[dict[str, str]], **kwargs: Any) -> Iterator[str]:
        for part in self._client.chat(model=self.model, messages=messages, stream=True, **kwargs):
            token = (part.get("message", {}) or {}).get("content", "")
            if token:
                yield token
