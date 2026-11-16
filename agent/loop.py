"""Agent loop: persistent conversation + memory/RAG context + tool routing (+ streaming)."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Iterator

from agent.config import AgentConfig
from agent.llm import ChatLLM, Embedder
from agent.memory import MemoryService
from agent.rag import RagService
from agent.store import AgentStore
from config import Settings
from tools import router

logger = logging.getLogger(__name__)

MODES = ("agent", "route", "chat", "cursor")

STREAM_SYSTEM = (
    "You are Sahir, a helpful local voice assistant running entirely on the user's PC. "
    "Answer concisely (a few sentences unless asked for detail) in plain language suitable for speech. "
    "Use the conversation history, the remembered facts about the user, and the document excerpts "
    "below when relevant. If you use a document excerpt, mention the file name."
)


@dataclass
class RetrievedContext:
    memories: list[dict[str, Any]] = field(default_factory=list)
    chunks: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_block(self) -> str:
        parts: list[str] = []
        if self.memories:
            parts.append("Known facts about the user (long-term memory):")
            parts += [f"- {m['content']}" for m in self.memories]
        if self.chunks:
            parts.append("Relevant excerpts from the user's local documents:")
            for c in self.chunks:
                parts.append(f"[{c['filename']} #{c['idx']}] {c['content'][:1200]}")
        return "\n".join(parts)

    def sources(self) -> dict[str, Any]:
        return {
            "memories": [{"id": m["id"], "content": m["content"], "score": m.get("score")} for m in self.memories],
            "chunks": [
                {k: c[k] for k in ("chunk_id", "document_id", "filename", "idx", "score")} for c in self.chunks
            ],
        }


@dataclass
class AgentReply:
    reply: str
    action: str
    action_details: dict[str, Any]
    routed: bool
    conversation_id: int
    user_message_id: int
    assistant_message_id: int
    sources: dict[str, Any]


class Agent:
    def __init__(self, settings: Settings, config: AgentConfig, store: AgentStore, embedder: Embedder,
                 llm: ChatLLM, orchestrator: Any | None = None) -> None:
        self.settings = settings
        self.config = config
        self.store = store
        self.embedder = embedder
        self.llm = llm
        self.memory = MemoryService(store, embedder, llm, dedup_threshold=config.memory_dedup_threshold)
        self.rag = RagService(store, embedder, chunk_size=config.chunk_size, overlap=config.chunk_overlap,
                              max_bytes=config.max_upload_mb * 1024 * 1024)
        self.orchestrator = orchestrator
        self._bg_threads: list[threading.Thread] = []

    # ------------------------------------------------------------ helpers
    def resolve_conversation(self, conversation_id: int | None, *, source: str = "web",
                             auto_session: bool = False) -> dict[str, Any]:
        if conversation_id:
            conv = self.store.get_conversation(conversation_id)
            if conv:
                return conv
            logger.warning("Conversation %s not found; creating a new one", conversation_id)
        if auto_session:
            conv = self.store.latest_active_conversation(self.config.session_idle_minutes, source=source)
            if conv:
                return conv
        return self.store.create_conversation(source=source)

    def history(self, conversation_id: int) -> list[dict[str, str]]:
        msgs = self.store.get_messages(conversation_id, limit=self.config.history_turns * 2)
        return [{"role": m["role"], "content": m["content"]} for m in msgs if m["role"] in {"user", "assistant"}]

    def retrieve(self, text: str) -> RetrievedContext:
        ctx = RetrievedContext()
        try:
            qvec = self.embedder.embed_query(text)
        except Exception as exc:
            logger.warning("Context retrieval skipped (embedding failed): %s", exc)
            ctx.error = str(exc)
            return ctx
        for mem, score in self.store.search_memories(qvec, k=self.config.memory_top_k):
            if score >= self.config.memory_min_score:
                ctx.memories.append({**mem, "score": round(score, 4)})
        for chunk in self.store.search_chunks(qvec, k=self.config.rag_top_k):
            if chunk.score >= self.config.rag_min_score:
                ctx.chunks.append(chunk.to_dict())
        return ctx

    def _auto_title(self, conv: dict[str, Any], text: str) -> None:
        if conv.get("title") in (None, "", "New conversation"):
            title = " ".join(text.split())[:60]
            if title:
                self.store.rename_conversation(conv["id"], title)

    def _schedule_memory_extraction(self, conversation_id: int) -> None:
        if not self.config.auto_memory:
            return

        def work() -> None:
            try:
                recent = self.store.get_messages(conversation_id, limit=4)
                self.memory.extract_from_messages(recent, conversation_id=conversation_id)
            except Exception as exc:
                logger.warning("Background memory extraction failed: %s", exc)

        t = threading.Thread(target=work, name=f"memory-extract-{conversation_id}", daemon=True)
        t.start()
        self._bg_threads = [x for x in self._bg_threads if x.is_alive()] + [t]

    def wait_background(self, timeout: float = 30.0) -> None:
        for t in list(self._bg_threads):
            t.join(timeout)

    # --------------------------------------------------------------- main
    def respond(self, text: str, *, conversation_id: int | None = None, mode: str = "agent",
                source: str = "web", input_type: str = "text", auto_session: bool = False,
                extra_meta: dict[str, Any] | None = None) -> AgentReply:
        text = text.strip()
        if not text:
            raise ValueError("Message is empty")
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        conv = self.resolve_conversation(conversation_id, source=source, auto_session=auto_session)
        history = self.history(conv["id"])
        ctx = self.retrieve(text) if mode != "cursor" else RetrievedContext()
        block = ctx.to_block()
        user_id = self.store.add_message(conv["id"], "user", text, {"input_type": input_type, **(extra_meta or {})})
        self._auto_title(conv, text)

        if mode == "agent" and self.orchestrator is None:
            mode = "route"
        if mode == "agent":
            result = self.orchestrator.run(
                text, history=history, context_block=block,
                base_context={"conversation_id": conv["id"],
                              "memories": [m["content"] for m in ctx.memories],
                              "rag_sources": [c["filename"] for c in ctx.chunks]},
            )
        elif mode == "cursor":
            result = router.run_cursor_task_detailed(text, self.settings, history=history)
        elif mode == "route":
            result = router.route_utterance_detailed(text, self.settings, history=history, context=block)
        else:
            result = router.chat_text_only_detailed(text, self.settings, history=history, context=block)

        sources = ctx.sources()
        assistant_id = self.store.add_message(
            conv["id"], "assistant", result.reply or "(no reply)",
            {"action": result.action, "action_details": result.action_details, "sources": sources,
             "tasks": getattr(result, "tasks", [])},
        )
        if result.action != "error" and mode != "cursor":
            self._schedule_memory_extraction(conv["id"])
        return AgentReply(
            reply=result.reply, action=result.action, action_details=result.action_details,
            routed=result.routed, conversation_id=conv["id"], user_message_id=user_id,
            assistant_message_id=assistant_id, sources=sources,
        )

    def stream(self, text: str, *, conversation_id: int | None = None, source: str = "web") -> Iterator[dict[str, Any]]:
        """Stream a context-aware chat reply token by token (no tool routing)."""
        text = text.strip()
        if not text:
            raise ValueError("Message is empty")
        conv = self.resolve_conversation(conversation_id, source=source)
        history = self.history(conv["id"])
        ctx = self.retrieve(text)
        user_id = self.store.add_message(conv["id"], "user", text, {"input_type": "text", "stream": True})
        self._auto_title(conv, text)
        yield {"type": "meta", "conversation_id": conv["id"], "user_message_id": user_id, "sources": ctx.sources()}

        system = STREAM_SYSTEM
        block = ctx.to_block()
        if block:
            system += "\n\n" + block
        messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": text}]
        tokens: list[str] = []
        error: str | None = None
        try:
            for tok in self.llm.chat_stream(messages, options={"temperature": 0.5, "num_predict": 512}):
                tokens.append(tok)
                yield {"type": "token", "text": tok}
        except Exception as exc:
            logger.exception("Streaming chat failed")
            error = str(exc)
            yield {"type": "error", "error": error}
        reply = "".join(tokens).strip() or (f"Error: {error}" if error else "(no reply)")
        assistant_id = self.store.add_message(
            conv["id"], "assistant", reply,
            {"action": "error" if error else "stream_chat", "sources": ctx.sources()},
        )
        if not error:
            self._schedule_memory_extraction(conv["id"])
        yield {"type": "done", "reply": reply, "conversation_id": conv["id"], "assistant_message_id": assistant_id}
