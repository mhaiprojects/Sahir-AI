"""Agent loop: persistent conversation + memory/RAG context + tool routing (+ streaming)."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Iterator

ProgressFn = Callable[[dict[str, Any]], None]

from activity import publish, tee_progress
from agent.config import AgentConfig
from agent.llm import ChatLLM, Embedder
from agent.memory import MemoryService
from agent.rag import RagService
from agent.store import AgentStore
from agent.supervisor import SupervisorService
from config import Settings
from tools import router

from agent.numbered_replies import build_numbered_reply, tts_text_for_reply

logger = logging.getLogger(__name__)


def _save_assistant_reply(
    store: AgentStore,
    conv_id: int,
    reply: str,
    *,
    action: str,
    action_details: dict[str, Any],
    sources: dict[str, Any],
    tasks: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]], int, str | None, bool]:
    raw = (reply or "").strip()
    display, items, numbered_ui = build_numbered_reply(
        reply, action=action, action_details=action_details, tasks=tasks or None,
    )
    tts = tts_text_for_reply(raw_reply=raw, items=items)
    meta: dict[str, Any] = {
        "action": action,
        "action_details": action_details,
        "sources": sources,
        "numbered_items": items,
        "tts_text": tts,
        "show_numbered_list": numbered_ui,
    }
    if tasks:
        meta["tasks"] = tasks
    assistant_id = store.add_message(conv_id, "assistant", display, meta)
    return display, items, assistant_id, tts, numbered_ui

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
    numbered_items: list[dict[str, Any]] = field(default_factory=list)
    tts_text: str | None = None
    show_numbered_list: bool = False


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
        self.supervisor: SupervisorService | None = None
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
        out: list[dict[str, str]] = []
        for m in msgs:
            if m["role"] in {"user", "assistant"}:
                out.append({"role": m["role"], "content": m["content"]})
            elif m["role"] == "system" and (m.get("meta") or {}).get("injection"):
                out.append({"role": "system", "content": m["content"]})
        return out

    def retrieve(self, text: str, *, project_id: int | None = None) -> RetrievedContext:
        ctx = RetrievedContext()
        try:
            qvec = self.embedder.embed_query(text)
        except Exception as exc:
            logger.warning("Context retrieval skipped (embedding failed): %s", exc)
            ctx.error = str(exc)
            return ctx
        for mem, score in self.store.search_memories(
            qvec, k=self.config.memory_top_k, project_id=project_id,
        ):
            if score >= self.config.memory_min_score:
                ctx.memories.append({**mem, "score": round(score, 4)})
        for chunk in self.store.search_chunks(qvec, k=self.config.rag_top_k, project_id=project_id):
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
            publish("step", f"background: extracting memories from chat #{conversation_id}", source="memory")
            try:
                recent = self.store.get_messages(conversation_id, limit=4)
                conv = self.store.get_conversation(conversation_id)
                pid = int(conv["project_id"]) if conv and conv.get("project_id") else None
                self.memory.extract_from_messages(recent, conversation_id=conversation_id, project_id=pid)
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
                extra_meta: dict[str, Any] | None = None, on_progress: ProgressFn | None = None) -> AgentReply:
        text = text.strip()
        if not text:
            raise ValueError("Message is empty")
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        conv = self.resolve_conversation(conversation_id, source=source, auto_session=auto_session)
        if not conv.get("mode") and mode in MODES:
            stored = "chat" if mode == "chat" else mode
            try:
                self.store.set_conversation_mode(conv["id"], stored)
                conv = self.store.get_conversation(conv["id"]) or conv
            except ValueError:
                pass
        started = time.monotonic()
        publish("step", f"▶ {mode} request in chat #{conv['id']} ({source}/{input_type}): "
                f"{' '.join(text.split())[:160]}", source=mode)
        # Mirror every progress event (stages, tool calls, workers, model switches) to the live panel.
        on_progress = tee_progress(on_progress, source=mode)
        on_progress({"type": "meta", "conversation_id": conv["id"], "mode": mode})
        history = self.history(conv["id"])
        if mode != "cursor" and on_progress:
            on_progress({"type": "progress", "stage": "context", "message": "Retrieving memories and documents…"})
        pid = int(conv["project_id"]) if conv.get("project_id") else None
        ctx = self.retrieve(text, project_id=pid) if mode != "cursor" else RetrievedContext()
        if on_progress and ctx.memories:
            on_progress({"type": "progress", "stage": "context",
                         "message": f"Found {len(ctx.memories)} relevant memories"})
        if on_progress and ctx.chunks:
            on_progress({"type": "progress", "stage": "context",
                         "message": f"Found {len(ctx.chunks)} document excerpts"})
        block = ctx.to_block()
        user_id = self.store.add_message(conv["id"], "user", text, {"input_type": input_type, **(extra_meta or {})})
        self._auto_title(conv, text)

        if conv.get("kind") == "supervisor":
            if self.supervisor is None:
                self.supervisor = SupervisorService(self.store, self.llm, self.settings)
            sup_result = self.supervisor.run_turn(
                text,
                project_id=int(conv["project_id"]),
                supervisor_conv_id=int(conv["id"]),
                history=history,
                context_block=block,
                on_progress=on_progress,
            )
            sources = ctx.sources()
            display, items, assistant_id, tts, numbered_ui = _save_assistant_reply(
                self.store, conv["id"], sup_result.reply or "(no reply)",
                action=sup_result.action, action_details=sup_result.action_details, sources=sources,
            )
            publish(
                "step",
                f"■ supervisor done in {time.monotonic() - started:.1f}s",
                source="supervisor",
            )
            out_cid = int(conv["id"])
            if sup_result.action == "switch_chat":
                details = sup_result.action_details or {}
                if details.get("conversation_id"):
                    out_cid = int(details["conversation_id"])
            return AgentReply(
                reply=display, action=sup_result.action, action_details=sup_result.action_details,
                routed=sup_result.routed, conversation_id=out_cid, user_message_id=user_id,
                assistant_message_id=assistant_id, sources=sources, numbered_items=items,
                tts_text=tts, show_numbered_list=numbered_ui,
            )

        if mode == "agent" and self.orchestrator is None:
            if on_progress:
                on_progress({"type": "progress", "stage": "mode", "message": "Orchestrator unavailable — using tool router"})
            mode = "route"
        if mode == "agent":
            result = self.orchestrator.run(
                text, history=history, context_block=block,
                base_context={"conversation_id": conv["id"],
                              "memories": [m["content"] for m in ctx.memories],
                              "rag_sources": [c["filename"] for c in ctx.chunks]},
                on_progress=on_progress,
            )
        elif mode == "cursor":
            workspace = self.store.cursor_workspace_for_conversation(conv["id"], self.settings.cursor_workspace)
            result = router.run_cursor_task_detailed(
                text, self.settings, history=history, on_progress=on_progress, workspace=workspace,
            )
        elif mode == "route":
            if on_progress:
                on_progress({"type": "progress", "stage": "route", "message": "Routing via tool layer…"})
            result = router.route_utterance_detailed(text, self.settings, history=history, context=block,
                                                     on_progress=on_progress)
        else:
            if on_progress:
                on_progress({"type": "progress", "stage": "chat", "message": "Generating reply…",
                             "model": self.llm.model})
            result = router.chat_text_only_detailed(text, self.settings, history=history, context=block)

        sources = ctx.sources()
        tasks = getattr(result, "tasks", [])
        display, items, assistant_id, tts, numbered_ui = _save_assistant_reply(
            self.store, conv["id"], result.reply or "(no reply)",
            action=result.action, action_details=result.action_details, sources=sources, tasks=tasks,
        )
        publish("error" if result.action == "error" else "step",
                f"■ {mode} done in {time.monotonic() - started:.1f}s → action {result.action}", source=mode,
                level="error" if result.action == "error" else "info")
        if result.action != "error" and mode != "cursor":
            self._schedule_memory_extraction(conv["id"])
        return AgentReply(
            reply=display, action=result.action, action_details=result.action_details,
            routed=result.routed, conversation_id=conv["id"], user_message_id=user_id,
            assistant_message_id=assistant_id, sources=sources, numbered_items=items,
            tts_text=tts, show_numbered_list=numbered_ui,
        )

    def stream(self, text: str, *, conversation_id: int | None = None, source: str = "web") -> Iterator[dict[str, Any]]:
        """Stream a context-aware chat reply token by token (no tool routing)."""
        text = text.strip()
        if not text:
            raise ValueError("Message is empty")
        conv = self.resolve_conversation(conversation_id, source=source)
        history = self.history(conv["id"])
        pid = int(conv["project_id"]) if conv.get("project_id") else None
        ctx = self.retrieve(text, project_id=pid)
        user_id = self.store.add_message(conv["id"], "user", text, {"input_type": "text", "stream": True})
        self._auto_title(conv, text)
        yield {"type": "meta", "conversation_id": conv["id"], "user_message_id": user_id, "sources": ctx.sources()}
        started = time.monotonic()
        publish("step", f"▶ chat request in chat #{conv['id']} ({source}): {' '.join(text.split())[:160]}",
                source="chat")
        if ctx.memories or ctx.chunks:
            publish("step", f"[context] {len(ctx.memories)} memories, {len(ctx.chunks)} document excerpts",
                    source="chat")
        publish("model", f"streaming reply with {self.llm.model}", source="chat")

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
        publish("error" if error else "step",
                f"■ chat done in {time.monotonic() - started:.1f}s ({len(tokens)} tokens)"
                + (f": {error}" if error else ""), source="chat", level="error" if error else "info")
        action = "error" if error else "stream_chat"
        display, items, assistant_id, tts, numbered_ui = _save_assistant_reply(
            self.store, conv["id"], reply, action=action, action_details={}, sources=ctx.sources(),
        )
        if not error:
            self._schedule_memory_extraction(conv["id"])
        yield {
            "type": "done", "reply": display, "conversation_id": conv["id"],
            "assistant_message_id": assistant_id, "numbered_items": items,
            "tts_text": tts, "show_numbered_list": numbered_ui,
        }

    def respond_events(self, text: str, *, conversation_id: int | None = None, mode: str = "chat",
                       source: str = "web", input_type: str = "text", auto_session: bool = False,
                       extra_meta: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """Run respond() (or stream() for mode chat) and yield progress events for the web UI."""
        if mode == "chat":
            yield from self.stream(text, conversation_id=conversation_id, source=source)
            return

        event_q: queue.SimpleQueue[dict[str, Any] | None] = queue.SimpleQueue()
        holder: list[AgentReply] = []
        errors: list[BaseException] = []

        def emit(ev: dict[str, Any]) -> None:
            event_q.put(ev)

        def work() -> None:
            try:
                holder.append(self.respond(
                    text, conversation_id=conversation_id, mode=mode, source=source,
                    input_type=input_type, auto_session=auto_session, extra_meta=extra_meta,
                    on_progress=emit,
                ))
            except BaseException as exc:
                errors.append(exc)
            finally:
                event_q.put(None)

        threading.Thread(target=work, name="agent-respond-events", daemon=True).start()
        while True:
            ev = event_q.get()
            if ev is None:
                break
            yield ev
        if errors:
            yield {"type": "error", "error": str(errors[0])}
            return
        r = holder[0]
        yield {
            "type": "done", "reply": r.reply, "conversation_id": r.conversation_id,
            "action": r.action, "action_details": r.action_details, "routed": r.routed,
            "sources": r.sources, "assistant_message_id": r.assistant_message_id,
            "numbered_items": r.numbered_items,
            "tts_text": r.tts_text,
            "show_numbered_list": r.show_numbered_list,
        }
