"""Long-term memory: manual CRUD, retrieval, and LLM auto-extraction into a review queue."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from agent.llm import ChatLLM, Embedder
from agent.store import AgentStore

logger = logging.getLogger(__name__)

CATEGORIES = ("preference", "fact", "person", "project", "task", "other")

EXTRACT_SYSTEM = """You extract durable long-term memories about the USER from a conversation.
Only keep facts that will still be useful in future conversations: preferences, personal facts,
people they mention, ongoing projects, standing instructions. Ignore small talk, one-off questions,
facts about the assistant, and anything the assistant said unless the user confirmed it.
Write each memory as a short third-person statement, e.g. "User prefers dark mode.".
Respond ONLY with JSON: {"memories": [{"content": "...", "category": "preference|fact|person|project|task|other"}]}
Return {"memories": []} when nothing is worth remembering."""


class MemoryService:
    def __init__(self, store: AgentStore, embedder: Embedder, llm: ChatLLM | None = None,
                 *, dedup_threshold: float = 0.92) -> None:
        self.store = store
        self.embedder = embedder
        self.llm = llm
        self.dedup_threshold = dedup_threshold

    def _embed(self, text: str) -> list[float] | None:
        try:
            return self.embedder.embed_documents([text])[0]
        except Exception as exc:
            logger.warning("Memory embedding failed (stored without vector): %s", exc)
            return None

    def add(self, content: str, *, category: str = "fact", status: str = "active", source: str = "manual",
            source_conversation_id: int | None = None) -> dict[str, Any]:
        category = category if category in CATEGORIES else "other"
        return self.store.add_memory(
            content, category=category, status=status, source=source,
            source_conversation_id=source_conversation_id, embedding=self._embed(content),
        )

    def update(self, mem_id: int, *, content: str | None = None, category: str | None = None,
               status: str | None = None) -> dict[str, Any] | None:
        embedding = self._embed(content) if content is not None else None
        if category is not None and category not in CATEGORIES:
            category = "other"
        return self.store.update_memory(mem_id, content=content, category=category, status=status,
                                        embedding=embedding)

    def search(self, query: str, k: int = 5, min_score: float = 0.0) -> list[tuple[dict[str, Any], float]]:
        if not query.strip():
            return []
        vec = self.embedder.embed_query(query)
        return [(m, s) for m, s in self.store.search_memories(vec, k=k) if s >= min_score]

    def is_duplicate(self, content: str, vec: list[float] | None) -> bool:
        lowered = content.strip().lower()
        for m in self.store.list_memories():
            if m["status"] != "rejected" and m["content"].strip().lower() == lowered:
                return True
        if vec is None:
            return False
        hits = self.store.search_memories(vec, k=1, statuses=("active", "pending", "rejected"))
        return bool(hits and hits[0][1] >= self.dedup_threshold)

    def extract_from_messages(self, messages: list[dict[str, Any]], *, conversation_id: int | None = None,
                              status: str = "pending") -> list[dict[str, Any]]:
        """Ask the LLM for candidate memories; store new ones as pending for user review."""
        if self.llm is None:
            raise RuntimeError("No LLM configured for memory extraction")
        transcript = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:1500]}"
            for m in messages if m.get("role") in {"user", "assistant"}
        )
        if not transcript.strip():
            return []
        resp = self.llm.chat(
            [{"role": "system", "content": EXTRACT_SYSTEM},
             {"role": "user", "content": f"Conversation:\n{transcript[-8000:]}"}],
            format="json",
            options={"temperature": 0.1, "num_predict": 400},
        )
        candidates = parse_memory_json(resp.get("content", ""))
        created: list[dict[str, Any]] = []
        for cand in candidates:
            vec = self._embed(cand["content"])
            if self.is_duplicate(cand["content"], vec):
                continue
            created.append(self.store.add_memory(
                cand["content"], category=cand["category"], status=status, source="auto",
                source_conversation_id=conversation_id, embedding=vec,
            ))
        logger.info("Memory extraction: %d candidates, %d new", len(candidates), len(created))
        return created


def parse_memory_json(raw: str) -> list[dict[str, str]]:
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    items = data.get("memories", []) if isinstance(data, dict) else data if isinstance(data, list) else []
    out: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, str):
            content, category = item, "fact"
        elif isinstance(item, dict):
            content = str(item.get("content") or item.get("memory") or "")
            category = str(item.get("category") or "fact").lower()
        else:
            continue
        content = content.strip()
        if 3 <= len(content) <= 500:
            out.append({"content": content, "category": category if category in CATEGORIES else "other"})
    return out[:10]
