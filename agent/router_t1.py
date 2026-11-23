"""Router Tier 1: embedding nearest-neighbour over labelled examples + project chats."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from agent.chat_router import (
    RouteContext,
    RouteDecision,
    STICKY_CONFIDENCE,
    resolve_target_chat,
)
from agent.gpu_config import get_gpu_config
from agent.store import AgentStore

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_PATH = Path(__file__).resolve().parent / "router_examples.json"
HOLDOUT_PATH = Path(__file__).resolve().parent / "router_holdout.json"
CACHE_DIR = PROJECT_ROOT / "data"


class RouterEmbedder(Protocol):
    model: str

    def embed_query(self, text: str) -> list[float]: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


@dataclass
class RouterExample:
    text: str
    label: str
    command: str | None = None
    source: str = "static"


@dataclass
class Tier1Hit:
    label: str
    margin: float
    best_sim: float
    command: str | None = None


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _hash_examples(examples: list[RouterExample]) -> str:
    payload = json.dumps(
        [(e.text, e.label, e.command, e.source) for e in examples],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def load_static_examples() -> list[RouterExample]:
    raw = json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))
    out: list[RouterExample] = []
    for row in raw.get("examples", []):
        out.append(RouterExample(
            text=str(row["text"]),
            label=str(row["label"]).upper(),
            command=row.get("command"),
            source="static",
        ))
    return out


def load_holdout_examples() -> list[tuple[str, str]]:
    if not HOLDOUT_PATH.is_file():
        return []
    raw = json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))
    return [(str(r["text"]), str(r["label"]).upper()) for r in raw.get("examples", [])]


def chat_examples(chats: list[dict[str, Any]]) -> list[RouterExample]:
    out: list[RouterExample] = []
    for c in chats:
        title = (c.get("title") or "").strip()
        if not title:
            continue
        tid = c.get("id")
        out.append(RouterExample(
            text=f"switch to {title}",
            label="S",
            command="switch_chat",
            source=f"chat:{tid}",
        ))
        out.append(RouterExample(
            text=f"open the {title} chat",
            label="S",
            command="switch_chat",
            source=f"chat:{tid}",
        ))
        out.append(RouterExample(
            text=f"give me a summary of the {title} chat",
            label="D",
            source=f"chat:{tid}",
        ))
        for alias in c.get("aliases") or []:
            al = str(alias).strip()
            if not al:
                continue
            out.append(RouterExample(
                text=f"message the {al} chat about the blocker",
                label="C",
                source=f"chat:{tid}:alias",
            ))
            out.append(RouterExample(
                text=f"tell the {al} chat to add tests",
                label="C",
                source=f"chat:{tid}:alias",
            ))
    return out


@dataclass
class _IndexedExample:
    example: RouterExample
    vector: np.ndarray


class RouterTier1Index:
    """In-memory NN index with optional on-disk embedding cache for static examples."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._embedder: RouterEmbedder | None = None
        self._static: list[_IndexedExample] = []
        self._examples_hash = ""
        self._model = ""
        self._chat_cache: dict[str, list[_IndexedExample]] = {}

    def _cache_path(self, examples_hash: str, model: str) -> Path:
        safe_model = re_sub_model(model)
        return CACHE_DIR / f"router_t1_cache_{safe_model}_{examples_hash}.json"

    def ensure_static(self, embedder: RouterEmbedder) -> None:
        with self._lock:
            static_ex = load_static_examples()
            ex_hash = _hash_examples(static_ex)
            model = embedder.model
            if self._static and self._examples_hash == ex_hash and self._model == model:
                return
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path = self._cache_path(ex_hash, model)
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if data.get("examples_hash") == ex_hash and data.get("model") == model:
                        self._static = [
                            _IndexedExample(
                                example=RouterExample(**row["example"]),
                                vector=np.array(row["vector"], dtype=np.float32),
                            )
                            for row in data.get("rows", [])
                        ]
                        self._examples_hash = ex_hash
                        self._model = model
                        self._embedder = embedder
                        logger.info("Router T1 loaded %s cached embeddings (%s)", len(self._static), path.name)
                        return
                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                    logger.warning("Router T1 cache read failed: %s", exc)
            texts = [e.text for e in static_ex]
            t0 = time.perf_counter()
            vectors = [np.array(v, dtype=np.float32) for v in embedder.embed_documents(texts)]
            elapsed = time.perf_counter() - t0
            self._static = [_IndexedExample(static_ex[i], vectors[i]) for i in range(len(static_ex))]
            self._examples_hash = ex_hash
            self._model = model
            self._embedder = embedder
            rows = [
                {"example": {
                    "text": ie.example.text,
                    "label": ie.example.label,
                    "command": ie.example.command,
                    "source": ie.example.source,
                }, "vector": ie.vector.tolist()}
                for ie in self._static
            ]
            path.write_text(
                json.dumps({"examples_hash": ex_hash, "model": model, "rows": rows}, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("Router T1 embedded %s static examples in %.2fs", len(self._static), elapsed)

    def _chat_key(self, chats: list[dict[str, Any]]) -> str:
        bits = sorted(
            (int(c.get("id") or 0), str(c.get("title") or ""), tuple(c.get("aliases") or []))
            for c in chats
        )
        return hashlib.sha256(repr(bits).encode()).hexdigest()[:12]

    def _dynamic_index(self, embedder: RouterEmbedder, chats: list[dict[str, Any]]) -> list[_IndexedExample]:
        if not chats:
            return []
        key = self._chat_key(chats)
        if key in self._chat_cache:
            return self._chat_cache[key]
        dyn = chat_examples(chats)
        if not dyn:
            return []
        vectors = [np.array(v, dtype=np.float32) for v in embedder.embed_documents([d.text for d in dyn])]
        indexed = [_IndexedExample(dyn[i], vectors[i]) for i in range(len(dyn))]
        self._chat_cache[key] = indexed
        return indexed

    def classify(
        self,
        utterance: str,
        embedder: RouterEmbedder,
        *,
        chats: list[dict[str, Any]] | None = None,
    ) -> tuple[Tier1Hit | None, float]:
        """Returns (hit, embed_ms). hit is None when index empty."""
        t0 = time.perf_counter()
        self.ensure_static(embedder)
        q = np.array(embedder.embed_query(utterance), dtype=np.float32)
        embed_ms = (time.perf_counter() - t0) * 1000
        pool = list(self._static)
        if chats:
            pool.extend(self._dynamic_index(embedder, chats))
        if not pool:
            return None, embed_ms

        label_scores: dict[str, float] = {}
        label_best_ex: dict[str, _IndexedExample] = {}
        for ie in pool:
            sim = _cosine(q, ie.vector)
            lab = ie.example.label
            if sim > label_scores.get(lab, -1.0):
                label_scores[lab] = sim
                label_best_ex[lab] = ie

        if not label_scores:
            return None, embed_ms

        ranked = sorted(label_scores.items(), key=lambda x: x[1], reverse=True)
        best_label, best_sim = ranked[0]
        second_sim = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = best_sim - second_sim
        cmd = label_best_ex[best_label].example.command if best_label == "S" else None
        return Tier1Hit(label=best_label, margin=margin, best_sim=best_sim, command=cmd), embed_ms


def re_sub_model(model: str) -> str:
    return hashlib.md5(model.encode()).hexdigest()[:10]


def invalidate_static_cache() -> None:
    """Drop in-memory static index (tests / example file edits)."""
    _index._static = []
    _index._examples_hash = ""
    _index._chat_cache.clear()


classify_for_eval = RouterTier1Index.classify  # scripts.calibrate_router


_index = RouterTier1Index()


def warm_router_index_background(embedder_fn: Any) -> None:
    def work() -> None:
        try:
            emb = embedder_fn()
            if emb is None:
                return
            _index.ensure_static(emb)
        except Exception as exc:
            logger.warning("Router T1 warm-up failed: %s", exc)

    threading.Thread(target=work, name="router-t1-warm", daemon=True).start()


def try_tier1_route(
    utterance: str,
    ctx: RouteContext,
    *,
    store: AgentStore | None,
    embedder: RouterEmbedder | None,
    t0: float,
    precomputed: Tier1Hit | None = None,
) -> RouteDecision | None:
    cfg = get_gpu_config()
    if not cfg.router_t1_enabled or not ctx.tier1_enabled or embedder is None:
        return None
    if precomputed is not None:
        hit = precomputed
    else:
        hit, _embed_ms = _index.classify(utterance, embedder, chats=ctx.chats)
    if hit is None:
        return None
    total_ms = (time.perf_counter() - t0) * 1000
    confidence = min(0.99, max(STICKY_CONFIDENCE, hit.margin))
    sticky = hit.margin < cfg.router_t1_margin
    if not sticky and hit.label == "D" and ctx.supervisor_chat_id:
        return RouteDecision(
            label="D",
            tier=1,
            confidence=confidence,
            target_chat_id=ctx.supervisor_chat_id,
            utterance=utterance,
            ms=total_ms,
            payload={"tier1": True, "margin": round(hit.margin, 4), "best_sim": round(hit.best_sim, 4)},
        )
    if not sticky and hit.label == "S" and hit.command:
        cmd = hit.command
        tid = ctx.active_chat_id
        if cmd == "switch_chat" and store:
            m = utterance.lower()
            for c in ctx.chats:
                title = (c.get("title") or "").lower()
                if title and title in m:
                    tid = int(c["id"])
                    break
            if tid is None and ctx.chats:
                resolved = resolve_target_chat(store, ctx, utterance)
                tid = resolved or ctx.active_chat_id
        return RouteDecision(
            label="S",
            tier=1,
            confidence=confidence,
            target_chat_id=tid,
            command=cmd,
            utterance=utterance,
            ms=total_ms,
            payload={"tier1": True, "margin": round(hit.margin, 4)},
        )
    return RouteDecision(
        label="C",
        tier=1,
        confidence=confidence,
        target_chat_id=ctx.active_chat_id,
        utterance=utterance,
        ms=total_ms,
        payload={
            "tier1": True,
            "sticky": sticky,
            "margin": round(hit.margin, 4),
            "best_label": hit.label,
        },
    )


def get_index() -> RouterTier1Index:
    return _index