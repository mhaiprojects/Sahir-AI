"""FastAPI routes for the agent manager: chats, memory, RAG, and context-aware agent chat."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from agent.loop import MODES, Agent
from agent.rag import SUPPORTED_EXTENSIONS, IngestError

logger = logging.getLogger(__name__)


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationRename(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    mode: str | None = Field(default=None, pattern="^(agent|route|chat|cursor|stream|tools)$")


class MemoryCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    category: str = "fact"


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    category: str | None = None
    status: str | None = Field(default=None, pattern="^(active|pending|rejected)$")


class ExtractRequest(BaseModel):
    conversation_id: int


class RagSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    k: int = Field(default=5, ge=1, le=20)


class AgentChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    conversation_id: int | None = None
    mode: str = Field(default="agent", pattern="^(agent|route|chat|cursor|stream)$")


def _md_export(conv: dict[str, Any], messages: list[dict[str, Any]]) -> str:
    lines = [f"# {conv['title']}", "", f"_Conversation {conv['id']} · created {conv['created_at']}_", ""]
    for m in messages:
        who = {"user": "You", "assistant": "Assistant"}.get(m["role"], m["role"].title())
        lines += [f"### {who} — {m['created_at']}", "", m["content"], ""]
    return "\n".join(lines)


def create_agent_router(get_agent: Callable[[], Agent], request_lock: threading.Lock | None = None) -> APIRouter:
    api = APIRouter()
    lock = request_lock or threading.Lock()

    # ----------------------------------------------------------------- status
    @api.get("/api/agent/status")
    def agent_status() -> dict[str, Any]:
        agent = get_agent()
        return {
            **agent.store.stats(),
            "embed_model": agent.embedder.model,
            "chat_model": agent.llm.model,
            "auto_memory": agent.config.auto_memory,
            "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
            "modes": list(MODES),
        }

    # ---------------------------------------------------------- conversations
    @api.get("/api/projects")
    def list_projects() -> dict[str, Any]:
        store = get_agent().store
        projects = store.list_projects()
        general = store.get_project_by_slug("general")
        return {"projects": projects, "general_project_id": general["id"] if general else None}

    @api.get("/api/conversations")
    def list_conversations(q: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = 0) -> dict[str, Any]:
        return {"conversations": get_agent().store.list_conversations(q=q, limit=limit, offset=offset)}

    @api.post("/api/conversations")
    def create_conversation(body: ConversationCreate) -> dict[str, Any]:
        return get_agent().store.create_conversation(body.title)

    @api.get("/api/conversations/search")
    def search_messages(q: str = Query(..., min_length=1), limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
        return {"results": get_agent().store.search_messages(q, limit=limit)}

    @api.get("/api/conversations/{conv_id}")
    def get_conversation(conv_id: int) -> dict[str, Any]:
        store = get_agent().store
        conv = store.get_conversation(conv_id)
        if not conv:
            raise HTTPException(404, "Conversation not found")
        return {**conv, "messages": store.get_messages(conv_id)}

    @api.patch("/api/conversations/{conv_id}")
    def patch_conversation(conv_id: int, body: ConversationPatch) -> dict[str, Any]:
        store = get_agent().store
        conv = store.get_conversation(conv_id)
        if not conv:
            raise HTTPException(404, "Conversation not found")
        if body.title is not None:
            try:
                conv = store.rename_conversation(conv_id, body.title)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            if not conv:
                raise HTTPException(404, "Conversation not found")
        if body.mode is not None:
            try:
                conv = store.set_conversation_mode(conv_id, body.mode)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            if not conv:
                raise HTTPException(404, "Conversation not found")
        return conv or store.get_conversation(conv_id)

    @api.delete("/api/conversations/{conv_id}")
    def delete_conversation(conv_id: int) -> dict[str, bool]:
        if not get_agent().store.delete_conversation(conv_id):
            raise HTTPException(404, "Conversation not found")
        return {"deleted": True}

    @api.get("/api/conversations/{conv_id}/export")
    def export_conversation(conv_id: int, format: str = Query("md", pattern="^(md|json)$")):
        store = get_agent().store
        conv = store.get_conversation(conv_id)
        if not conv:
            raise HTTPException(404, "Conversation not found")
        messages = store.get_messages(conv_id)
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in conv["title"])[:50] or "conversation"
        if format == "json":
            body = json.dumps({**conv, "messages": messages, "exported_at": datetime.now(timezone.utc).isoformat()},
                              ensure_ascii=False, indent=2)
            return PlainTextResponse(body, media_type="application/json",
                                     headers={"Content-Disposition": f'attachment; filename="{safe}.json"'})
        return PlainTextResponse(_md_export(conv, messages), media_type="text/markdown",
                                 headers={"Content-Disposition": f'attachment; filename="{safe}.md"'})

    # --------------------------------------------------------------- memories
    @api.get("/api/memories")
    def list_memories(status: str | None = Query(None, pattern="^(active|pending|rejected)$"), q: str | None = None) -> dict[str, Any]:
        return {"memories": get_agent().store.list_memories(status=status, q=q)}

    @api.post("/api/memories")
    def create_memory(body: MemoryCreate) -> dict[str, Any]:
        try:
            return get_agent().memory.add(body.content, category=body.category)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @api.patch("/api/memories/{mem_id}")
    def update_memory(mem_id: int, body: MemoryUpdate) -> dict[str, Any]:
        try:
            mem = get_agent().memory.update(mem_id, content=body.content, category=body.category, status=body.status)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not mem:
            raise HTTPException(404, "Memory not found")
        return mem

    @api.delete("/api/memories/{mem_id}")
    def delete_memory(mem_id: int) -> dict[str, bool]:
        if not get_agent().store.delete_memory(mem_id):
            raise HTTPException(404, "Memory not found")
        return {"deleted": True}

    @api.post("/api/memories/extract")
    def extract_memories(body: ExtractRequest) -> dict[str, Any]:
        agent = get_agent()
        if not agent.store.get_conversation(body.conversation_id):
            raise HTTPException(404, "Conversation not found")
        if not lock.acquire(timeout=120):
            raise HTTPException(429, "Assistant is busy. Try again shortly.")
        try:
            created = agent.memory.extract_from_messages(
                agent.store.get_messages(body.conversation_id), conversation_id=body.conversation_id
            )
        except Exception as exc:
            logger.exception("Memory extraction failed")
            raise HTTPException(502, f"Memory extraction failed: {exc}") from exc
        finally:
            lock.release()
        return {"created": created, "count": len(created)}

    @api.post("/api/memories/search")
    def search_memories(body: RagSearchRequest) -> dict[str, Any]:
        try:
            hits = get_agent().memory.search(body.query, k=body.k)
        except Exception as exc:
            raise HTTPException(502, f"Embedding failed: {exc}") from exc
        return {"results": [{**m, "score": round(s, 4)} for m, s in hits]}

    # -------------------------------------------------------------------- RAG
    @api.get("/api/rag/documents")
    def list_documents() -> dict[str, Any]:
        return {"documents": get_agent().store.list_documents()}

    @api.post("/api/rag/documents")
    async def upload_documents(files: list[UploadFile] = File(...)) -> dict[str, Any]:
        agent = get_agent()
        results: list[dict[str, Any]] = []
        for f in files:
            data = await f.read()
            try:
                res = agent.rag.ingest(f.filename or "upload.txt", data, f.content_type)
                results.append({"filename": f.filename, "ok": True, "duplicate": res.duplicate, "document": res.document})
            except IngestError as exc:
                results.append({"filename": f.filename, "ok": False, "error": str(exc)})
            except Exception as exc:
                logger.exception("Ingest failed for %s", f.filename)
                results.append({"filename": f.filename, "ok": False, "error": f"Ingest failed: {exc}"})
        if results and not any(r["ok"] for r in results):
            raise HTTPException(400, detail=results)
        return {"results": results}

    @api.get("/api/rag/documents/{doc_id}")
    def get_document(doc_id: int, limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
        store = get_agent().store
        doc = store.get_document(doc_id)
        if not doc:
            raise HTTPException(404, "Document not found")
        return {**doc, "chunks": store.get_chunks(doc_id, limit=limit)}

    @api.delete("/api/rag/documents/{doc_id}")
    def delete_document(doc_id: int) -> dict[str, bool]:
        if not get_agent().store.delete_document(doc_id):
            raise HTTPException(404, "Document not found")
        return {"deleted": True}

    @api.post("/api/rag/search")
    def rag_search(body: RagSearchRequest) -> dict[str, Any]:
        try:
            hits = get_agent().rag.search(body.query, k=body.k)
        except Exception as exc:
            raise HTTPException(502, f"Embedding failed: {exc}") from exc
        return {"results": [h.to_dict() for h in hits]}

    # ------------------------------------------------------------- agent chat
    @api.post("/api/agent/chat")
    def agent_chat(body: AgentChatRequest) -> dict[str, Any]:
        if not lock.acquire(blocking=False):
            raise HTTPException(429, "Assistant is busy with another request. Try again shortly.")
        try:
            r = get_agent().respond(body.message, conversation_id=body.conversation_id, mode=body.mode)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            lock.release()
        return {
            "reply": r.reply, "action": r.action, "action_details": r.action_details, "routed": r.routed,
            "conversation_id": r.conversation_id, "user_message_id": r.user_message_id,
            "assistant_message_id": r.assistant_message_id, "sources": r.sources,
        }

    @api.post("/api/agent/chat/stream")
    def agent_chat_stream(body: AgentChatRequest) -> StreamingResponse:
        agent = get_agent()

        def events():
            # Acquire inside the generator so the lock can never leak if the client disconnects early.
            if not lock.acquire(blocking=False):
                yield f"data: {json.dumps({'type': 'error', 'error': 'Assistant is busy. Try again shortly.'})}\n\n"
                return
            try:
                mode = "chat" if body.mode == "stream" else body.mode
                for ev in agent.respond_events(
                    body.message, conversation_id=body.conversation_id, mode=mode
                ):
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            except Exception as exc:  # pragma: no cover - defensive
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"
            finally:
                lock.release()

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return api
