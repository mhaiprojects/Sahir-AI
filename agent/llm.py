"""Thin Ollama wrappers for embeddings and chat (swappable in tests)."""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterator, Protocol

import ollama

from agent.ollama_runtime import embed_options, ollama_chat_kwargs

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
            resp = self._client.embed(model=self.model, input=batch, options=embed_options())
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
    """Chat client. ``model_getter`` lets the runtime model selector switch models live."""

    def __init__(self, host: str, model: str, model_getter: Callable[[], str] | None = None) -> None:
        self._model = model
        self._model_getter = model_getter
        self._client = ollama.Client(host=host)

    @property
    def model(self) -> str:
        return self._model_getter() if self._model_getter else self._model

    @model.setter
    def model(self, value: str) -> None:
        self._model = value
        self._model_getter = None

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        model = self.model
        resp = self._client.chat(model=model, messages=messages, **ollama_chat_kwargs(model, **kwargs))
        return {"content": (resp.get("message", {}) or {}).get("content", "") or ""}

    def chat_stream(self, messages: list[dict[str, str]], **kwargs: Any) -> Iterator[str]:
        model = self.model
        kw = ollama_chat_kwargs(model, **kwargs)
        kw["stream"] = True
        for part in self._client.chat(model=model, messages=messages, **kw):
            token = (part.get("message", {}) or {}).get("content", "")
            if token:
                yield token
