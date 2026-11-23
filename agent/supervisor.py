"""Project supervisor: spawn/brief/archive sub-chats, scoped recall, context injection (architecture §3–4)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from activity import publish
from agent.llm import ChatLLM
from agent.store import AgentStore
from agent.supervisor_intent import (
    is_roster_question,
    normalize_tool,
    parse_directive,
    utterance_requests_structural_action,
)
from config import Settings

logger = logging.getLogger(__name__)

SUMMARY_MSG_LIMIT = 40
SUMMARY_CHARS = 6000
ROSTER_REPLY_MAX = 1800

SUPERVISOR_JSON_SYSTEM = """You are the project supervisor for Sahir AI. You manage sub-chats in this project.
Respond with JSON only (no markdown). Use one of:
{"tool":"answer","reply":"short spoken reply to the user"}
{"tool":"spawn_sub_chat","title":"...","brief":"...","aliases":["..."],"mode":"agent|chat|cursor|route"}
{"tool":"update_brief","conversation_id":123,"brief":"..."}
{"tool":"rename_sub_chat","conversation_id":123,"title":"..."}
{"tool":"archive_sub_chat","conversation_id":123}
{"tool":"unarchive_sub_chat","conversation_id":123}
{"tool":"summarise_sub_chat","conversation_id":123}
{"tool":"summarise_all"}
{"tool":"inject_context","conversation_id":123,"content":"background facts…","reason":"..."}
{"tool":"move_sub_chat","conversation_id":123,"project_id":2}
Sub-chats are listed in the system context. Never invent conversation ids."""


@dataclass
class SupervisorReply:
    reply: str
    action: str = "supervisor"
    action_details: dict[str, Any] = field(default_factory=dict)
    routed: bool = True


class SupervisorService:
    def __init__(self, store: AgentStore, llm: ChatLLM, settings: Settings) -> None:
        self.store = store
        self.llm = llm
        self.settings = settings

    def _project(self, project_id: int) -> dict[str, Any]:
        proj = self.store.get_project(project_id)
        if not proj:
            raise ValueError("Project not found")
        return proj

    def _supervisor_row(self, supervisor_conv_id: int) -> dict[str, Any]:
        conv = self.store.get_conversation(supervisor_conv_id)
        if not conv or conv.get("kind") != "supervisor":
            raise ValueError("Not a supervisor conversation")
        return conv

    def catalog_block(self, project_id: int) -> str:
        subs = self.store.list_sub_chats(project_id, include_archived=True)
        lines = []
        for c in subs:
            aliases = ", ".join(c.get("aliases") or []) or "—"
            brief = (c.get("brief") or "").strip()[:240] or "—"
            lines.append(
                f"- #{c['id']} {c['title']} [{c.get('status', 'active')}] "
                f"aliases: {aliases}; brief: {brief}; msgs: {c.get('message_count', 0)}; "
                f"updated: {c.get('updated_at', '')}"
            )
        if not lines:
            lines.append("- (no sub-chats yet)")
        proj = self._project(project_id)
        return (
            f"Project: {proj['name']} (id={project_id})\n"
            f"Description: {(proj.get('description') or '').strip() or '—'}\n"
            f"Sub-chats:\n" + "\n".join(lines)
        )

    def spawn_sub_chat(
        self,
        project_id: int,
        *,
        title: str,
        brief: str = "",
        aliases: list[str] | None = None,
        mode: str | None = None,
        supervisor_conv_id: int | None = None,
    ) -> dict[str, Any]:
        proj = self._project(project_id)
        sup_id = supervisor_conv_id or proj.get("supervisor_conversation_id")
        if not sup_id:
            raise ValueError("Project has no supervisor")
        self._supervisor_row(int(sup_id))
        publish("supervisor", "", source="supervisor", state=f"spawning “{title.strip()[:60]}”")
        conv = self.store.create_conversation(
            title.strip()[:200] or "New sub-chat",
            project_id=project_id,
            kind="sub",
            parent_id=int(sup_id),
            brief=(brief or "")[:4000],
            aliases=aliases or [],
            mode=mode or "agent",
        )
        publish(
            "spawn",
            "",
            source="supervisor",
            chat_title=conv["title"],
            conversation_id=conv["id"],
            project_name=proj["name"],
        )
        publish("supervisor", "", source="supervisor", state="idle")
        return conv

    def update_brief(self, conv_id: int, brief: str) -> dict[str, Any]:
        conv = self.store.get_conversation(conv_id)
        if not conv:
            raise ValueError("Conversation not found")
        if conv.get("kind") == "supervisor":
            raise ValueError("Use project description for the supervisor")
        publish("supervisor", "", source="supervisor", state=f"updating brief for {conv['title'][:40]}")
        out = self.store.update_conversation_brief(conv_id, brief)
        if out is None:
            raise ValueError("Conversation not found")
        publish("supervisor", "", source="supervisor", state="idle")
        return out

    def append_brief(self, conv_id: int, note: str) -> dict[str, Any]:
        conv = self.store.get_conversation(conv_id)
        if not conv:
            raise ValueError("Conversation not found")
        note = (note or "").strip()
        if not note:
            raise ValueError("Note is empty")
        old = (conv.get("brief") or "").strip()
        combined = f"{old}\n• {note}" if old else note
        return self.update_brief(conv_id, combined[:4000])

    def archive_sub_chat(self, conv_id: int) -> dict[str, Any]:
        conv = self.store.get_conversation(conv_id)
        if not conv or conv.get("kind") == "supervisor":
            raise ValueError("Cannot archive this conversation")
        publish("supervisor", "", source="supervisor", state=f"archiving {conv['title'][:40]}")
        out = self.store.set_conversation_status(conv_id, "archived")
        if out is None:
            raise ValueError("Conversation not found")
        publish("supervisor", "", source="supervisor", state="idle")
        return out

    def unarchive_sub_chat(self, conv_id: int) -> dict[str, Any]:
        conv = self.store.get_conversation(conv_id)
        if not conv or conv.get("kind") == "supervisor":
            raise ValueError("Cannot unarchive this conversation")
        out = self.store.set_conversation_status(conv_id, "active")
        if out is None:
            raise ValueError("Conversation not found")
        return out

    def rename_sub_chat(self, conv_id: int, title: str) -> dict[str, Any]:
        conv = self.store.get_conversation(conv_id)
        if not conv or conv.get("kind") == "supervisor":
            raise ValueError("Cannot rename this conversation")
        return self.store.rename_conversation(conv_id, title)  # type: ignore[return-value]

    def move_sub_chat(self, conv_id: int, project_id: int, *, from_project_id: int | None = None) -> dict[str, Any]:
        conv = self.store.get_conversation(conv_id)
        if not conv:
            raise ValueError("Conversation not found")
        if from_project_id is not None and int(conv.get("project_id") or 0) != from_project_id:
            raise ValueError("That chat is not in this project")
        publish("supervisor", "", source="supervisor", state="moving chat to another project")
        out = self.store.move_conversation(conv_id, project_id)
        publish("supervisor", "", source="supervisor", state="idle")
        return out

    def _truncate_messages(self, conv_id: int) -> str:
        msgs = self.store.get_messages(conv_id, limit=SUMMARY_MSG_LIMIT)
        lines = []
        for m in msgs:
            if m["role"] not in {"user", "assistant"}:
                continue
            who = "User" if m["role"] == "user" else "Assistant"
            lines.append(f"{who}: {m['content'][:800]}")
        text = "\n".join(lines)
        if len(text) > SUMMARY_CHARS:
            text = text[-SUMMARY_CHARS:]
        return text

    def summarise_sub_chat(self, conv_id: int) -> dict[str, Any]:
        conv = self.store.get_conversation(conv_id)
        if not conv or conv.get("kind") == "supervisor":
            raise ValueError("Cannot summarise this conversation")
        publish("supervisor", "", source="supervisor", state=f"summarising {conv['title'][:40]}")
        transcript = self._truncate_messages(conv_id)
        if not transcript.strip():
            title = str(conv.get("title") or f"Chat #{conv_id}")
            brief = (conv.get("brief") or "").strip() or "no brief"
            summary = f"{title}: {brief}. No messages yet."
        else:
            resp = self.llm.chat(
                [
                    {"role": "system", "content": "Summarise the chat in 2-4 sentences for a supervisor roster. Plain text only."},
                    {"role": "user", "content": transcript},
                ],
                options={"temperature": 0.2, "num_predict": 200},
            )
            summary = (resp.get("content") or "").strip()[:2000] or "(empty summary)"
        out = self.store.update_conversation_brief(conv_id, summary)
        if out is None:
            raise ValueError("Conversation not found")
        publish("supervisor", "", source="supervisor", state="idle")
        return out

    def roster_reply(self, project_id: int, *, include_archived: bool = False) -> str:
        subs = self.store.list_sub_chats(project_id, include_archived=include_archived)
        if not subs:
            return "No sub-chats in this project yet."
        lines: list[str] = []
        for c in subs:
            brief = (c.get("brief") or "").strip()[:160] or "—"
            st = c.get("status") or "active"
            n = c.get("message_count", 0)
            lines.append(f"• {c['title']} [{st}] — {brief} ({n} msgs)")
        text = "\n".join(lines)
        if len(text) > ROSTER_REPLY_MAX:
            text = text[: ROSTER_REPLY_MAX - 1] + "…"
        return text

    def summarise_all(self, project_id: int) -> tuple[list[dict[str, Any]], str]:
        subs = self.store.list_sub_chats(project_id, include_archived=False)
        publish("supervisor", "", source="supervisor", state=f"summarising {len(subs)} chats")
        done: list[dict[str, Any]] = []
        lines: list[str] = []
        for c in subs:
            updated = self.summarise_sub_chat(int(c["id"]))
            done.append(updated)
            snip = (updated.get("brief") or updated.get("title") or "")[:400]
            lines.append(f"• {updated.get('title', c['title'])}: {snip}")
        publish("supervisor", "", source="supervisor", state="idle")
        if not lines:
            return done, "No active sub-chats to summarise."
        text = "\n".join(lines)
        if len(text) > ROSTER_REPLY_MAX:
            text = text[: ROSTER_REPLY_MAX - 1] + "…"
        return done, text

    def inject_context(
        self,
        to_conv_id: int,
        content: str,
        *,
        from_conversation_id: int | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        content = (content or "").strip()
        if not content:
            raise ValueError("Injection content is empty")
        target = self.store.get_conversation(to_conv_id)
        if not target or target.get("kind") == "supervisor":
            raise ValueError("Cannot inject into this conversation")
        publish("supervisor", "", source="supervisor", state=f"injecting into {target['title'][:40]}")
        inj = self.store.add_context_injection(
            from_conversation_id=from_conversation_id,
            to_conversation_id=to_conv_id,
            content=content,
            reason=reason,
        )
        header = "Background from supervisor"
        if reason:
            header += f" ({reason})"
        body = f"{header}\n\n{content}"
        msg_id = self.store.add_message(
            to_conv_id,
            "system",
            body,
            {"injected_by": "supervisor", "injection": True, "reason": reason},
        )
        publish(
            "inject",
            "",
            source="supervisor",
            conversation_id=to_conv_id,
            chat_title=target["title"],
            message_id=msg_id,
        )
        publish("supervisor", "", source="supervisor", state="idle")
        return {"injection": inj, "message_id": msg_id}

    def _parse_tool_json(self, raw: str) -> dict[str, Any]:
        raw = (raw or "").strip()
        if not raw:
            return {"tool": "answer", "reply": "I didn't get a response."}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                return {"tool": "answer", "reply": raw[:500]}
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return {"tool": "answer", "reply": raw[:500]}
        if not isinstance(data, dict):
            return {"tool": "answer", "reply": str(data)[:500]}
        return data

    def execute_tool(
        self, project_id: int, supervisor_conv_id: int, tool: dict[str, Any], *, utterance: str = "",
    ) -> SupervisorReply:
        name = str(tool.get("tool") or "answer")
        if name == "answer":
            return SupervisorReply(reply=str(tool.get("reply") or "Done."), action="supervisor_answer")
        if name == "spawn_sub_chat":
            conv = self.spawn_sub_chat(
                project_id,
                title=str(tool.get("title") or "New sub-chat"),
                brief=str(tool.get("brief") or ""),
                aliases=tool.get("aliases") if isinstance(tool.get("aliases"), list) else None,
                mode=tool.get("mode"),
                supervisor_conv_id=supervisor_conv_id,
            )
            return SupervisorReply(
                reply=f"Started sub-chat “{conv['title']}” (#{conv['id']}).",
                action="spawn_sub_chat",
                action_details={"conversation": conv},
            )
        if name == "update_brief":
            conv = self.update_brief(int(tool["conversation_id"]), str(tool.get("brief") or ""))
            return SupervisorReply(reply="Brief updated.", action_details={"conversation": conv})
        if name == "append_brief":
            conv = self.append_brief(int(tool["conversation_id"]), str(tool.get("brief") or ""))
            return SupervisorReply(
                reply=f"Added a note to “{conv['title']}”.",
                action="append_brief",
                action_details={"conversation": conv},
            )
        if name == "rename_sub_chat":
            conv = self.rename_sub_chat(int(tool["conversation_id"]), str(tool.get("title") or ""))
            return SupervisorReply(reply=f"Renamed to “{conv['title']}”.", action_details={"conversation": conv})
        if name == "archive_sub_chat":
            conv = self.archive_sub_chat(int(tool["conversation_id"]))
            return SupervisorReply(reply=f"Archived “{conv['title']}”.", action_details={"conversation": conv})
        if name == "unarchive_sub_chat":
            conv = self.unarchive_sub_chat(int(tool["conversation_id"]))
            return SupervisorReply(reply=f"Restored “{conv['title']}”.", action_details={"conversation": conv})
        if name == "summarise_sub_chat":
            conv = self.summarise_sub_chat(int(tool["conversation_id"]))
            return SupervisorReply(reply=f"Summary saved on “{conv['title']}”.", action_details={"conversation": conv})
        if name == "list_chats":
            text = self.roster_reply(project_id)
            return SupervisorReply(reply=text, action="list_chats", action_details={"roster": text})
        if name == "summarise_all":
            done, text = self.summarise_all(project_id)
            return SupervisorReply(
                reply=text,
                action="summarise_all",
                action_details={"count": len(done), "conversations": done},
            )
        if name == "inject_context":
            res = self.inject_context(
                int(tool["conversation_id"]),
                str(tool.get("content") or ""),
                from_conversation_id=supervisor_conv_id,
                reason=tool.get("reason"),
            )
            return SupervisorReply(reply="Context injected.", action="inject_context", action_details=res)
        if name == "move_sub_chat":
            conv = self.move_sub_chat(
                int(tool["conversation_id"]),
                int(tool["project_id"]),
                from_project_id=project_id,
            )
            return SupervisorReply(
                reply=f"Moved “{conv['title']}” to project #{conv.get('project_id')}.",
                action="move_sub_chat",
                action_details={"conversation": conv},
            )
        return SupervisorReply(reply=f"Unknown supervisor tool {name!r}.")

    def _apply_tool(
        self,
        project_id: int,
        supervisor_conv_id: int,
        tool: dict[str, Any],
        utterance: str,
    ) -> SupervisorReply | None:
        name = str(tool.get("tool") or "answer")
        if name == "answer":
            return self.execute_tool(project_id, supervisor_conv_id, tool, utterance=utterance)
        norm, err = normalize_tool(self.store, project_id, tool, utterance)
        if err:
            if not utterance_requests_structural_action(utterance) and (
                is_roster_question(utterance) or "?" in utterance
            ):
                return None
            return SupervisorReply(reply=err, action="supervisor_clarify", routed=False)
        if norm is None:
            return SupervisorReply(reply=err or "I couldn't do that.", action="supervisor_clarify", routed=False)
        return self.execute_tool(project_id, supervisor_conv_id, norm, utterance=utterance)

    def _answer_turn(
        self,
        utterance: str,
        *,
        project_id: int,
        history: list[dict[str, str]],
        context_block: str = "",
    ) -> SupervisorReply:
        roster = self.roster_reply(project_id, include_archived=True)
        system = (
            "You are the project supervisor. Answer the user's question using the roster below. "
            'Respond with JSON only: {"tool":"answer","reply":"..."} — short spoken answer, no tool calls.'
            f"\n\nRoster:\n{roster}"
        )
        if context_block:
            system += "\n\n" + context_block
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            *history[-12:],
            {"role": "user", "content": utterance},
        ]
        try:
            resp = self.llm.chat(messages, format="json", options={"temperature": 0.3, "num_predict": 350})
        except TypeError:
            resp = self.llm.chat(messages, options={"temperature": 0.3, "num_predict": 350})
        tool = self._parse_tool_json(resp.get("content", ""))
        if str(tool.get("tool") or "answer") != "answer":
            tool = {"tool": "answer", "reply": roster}
        reply = str(tool.get("reply") or "").strip()
        if not reply:
            reply = roster
        return SupervisorReply(reply=reply[:ROSTER_REPLY_MAX], action="supervisor_answer")

    def run_turn(
        self,
        text: str,
        *,
        project_id: int,
        supervisor_conv_id: int,
        history: list[dict[str, str]],
        context_block: str = "",
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> SupervisorReply:
        utterance = (text or "").strip()
        publish("supervisor", "", source="supervisor", state="thinking")
        if on_progress:
            on_progress({"type": "progress", "stage": "supervisor", "message": "Supervisor planning…"})

        directive = parse_directive(utterance)
        if directive:
            result = self._apply_tool(project_id, supervisor_conv_id, directive, utterance)
            if result is None:
                result = self._answer_turn(
                    utterance, project_id=project_id, history=history, context_block=context_block,
                )
            publish("supervisor", "", source="supervisor", state="idle")
            return result

        if is_roster_question(utterance):
            result = self.execute_tool(
                project_id, supervisor_conv_id, {"tool": "list_chats"}, utterance=utterance,
            )
            publish("supervisor", "", source="supervisor", state="idle")
            return result

        system = SUPERVISOR_JSON_SYSTEM + "\n\n" + self.catalog_block(project_id)
        if context_block:
            system += "\n\n" + context_block
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            *history[-12:],
            {"role": "user", "content": utterance},
        ]
        try:
            resp = self.llm.chat(messages, format="json", options={"temperature": 0.1, "num_predict": 400})
        except TypeError:
            resp = self.llm.chat(messages, options={"temperature": 0.1, "num_predict": 400})
        tool = self._parse_tool_json(resp.get("content", ""))
        result = self._apply_tool(project_id, supervisor_conv_id, tool, utterance)
        if result is None:
            result = self._answer_turn(
                utterance, project_id=project_id, history=history, context_block=context_block,
            )
        publish("supervisor", "", source="supervisor", state="idle")
        return result