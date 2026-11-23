"""Deterministic supervisor directives (Tier 0) before the small-model JSON tool loop."""

from __future__ import annotations

import re
from typing import Any

from agent.store import AgentStore

_SPAWN_PREFIX = re.compile(
    r"(?is)^(?:please\s+)?(?:spin\s+up|start|open|create|spawn)\s+(?:a\s+)?(?:new\s+)?(?:sub[- ]?)?chat\s+"
    r"(?:for|about|on)\s+",
)
_BRIEF_TAIL = re.compile(r"(?is)^(.+?)(?:,\s*brief\s*:\s*|\s+brief\s*:\s*)(.+)$")
_SUMMARISE_ALL = re.compile(r"(?is)^(?:please\s+)?summari[sz]e\s+(?:everything|all(?:\s+chats)?)\s*\.?$")
_SUMMARISE_ONE = re.compile(r"(?is)^(?:please\s+)?summari[sz]e\s+(.+?)\s*\.?$")
_ARCHIVE = re.compile(r"(?is)^(?:please\s+)?archive\s+(?:chat\s+)?(.+?)\s*\.?$")
_UNARCHIVE = re.compile(r"(?is)^(?:please\s+)?(?:unarchive|restore)\s+(?:chat\s+)?(.+?)\s*\.?$")
_RENAME = re.compile(r"(?is)^(?:please\s+)?rename\s+(.+?)\s+to\s+(.+?)\s*\.?$")
_MOVE = re.compile(r"(?is)^(?:please\s+)?move\s+(.+?)\s+to\s+project\s+(.+?)\s*\.?$")
_BRIEF_CHAT = re.compile(r"(?is)^(?:please\s+)?(?:set\s+)?brief\s+(?:for\s+)?(.+?)\s*:\s*(.+)$")
_INJECT = re.compile(
    r"(?is)^(?:please\s+)?(?:inject|give)\s+(?:chat\s+)?(.+?)\s+(?:background|context)\s+(?:on|about)\s+(.+?)\s*\.?$"
)
_LIST_CHATS = re.compile(
    r"(?is)(?:^|\b)(?:what|which)\s+(?:sub[- ]?)?chats?\s+(?:do\s+(?:we|i)\s+have|are\s+(?:there|we)(?:\s+have)?)"
)
_LIST_CHATS2 = re.compile(r"(?is)^(?:please\s+)?(?:list|show)\s+(?:me\s+)?(?:the\s+)?(?:sub[- ]?)?chats?\b")
_STATUS = re.compile(r"(?is)^(?:project\s+|chat\s+)?status\s*\??$")

STRUCTURAL_ACTION_RE = re.compile(
    r"\b(move|archive|rename|brief|inject|give|unarchive|restore|summari[sz]e|spin\s+up|spawn|start|open|create)\b",
    re.I,
)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _strip_article(title: str) -> str:
    t = title.strip()
    return re.sub(r"^the\s+", "", t, flags=re.I).strip()


def resolve_chat_in_project(
    store: AgentStore,
    project_id: int,
    hint: str,
    *,
    include_archived: bool = True,
) -> dict[str, Any] | None:
    hint = _strip_article((hint or "").strip())
    if not hint:
        return None
    id_m = re.fullmatch(r"#?(\d+)", hint)
    if id_m:
        conv = store.get_conversation(int(id_m.group(1)))
        if conv and int(conv.get("project_id") or 0) == project_id and conv.get("kind") != "supervisor":
            return conv
        return None
    hint_n = _norm(hint)
    best: dict[str, Any] | None = None
    best_score = 0
    for c in store.list_sub_chats(project_id, include_archived=include_archived):
        title_n = _norm(str(c.get("title") or ""))
        score = 0
        if hint_n == title_n:
            score = 100
        elif hint_n in title_n or title_n in hint_n:
            score = 85
        else:
            overlap = len(set(hint_n.split()) & set(title_n.split()))
            if overlap:
                score = 20 + overlap * 15
        for al in c.get("aliases") or []:
            an = _norm(str(al))
            if hint_n == an or hint_n in an or an in hint_n:
                score = max(score, 90)
        if score > best_score:
            best_score = score
            best = c
    return best if best_score >= 20 else None


def resolve_project(store: AgentStore, hint: str) -> dict[str, Any] | None:
    hint = (hint or "").strip()
    if not hint:
        return None
    if hint.isdigit():
        return store.get_project(int(hint))
    hint_n = _norm(hint)
    for p in store.list_projects():
        if _norm(p["name"]) == hint_n or str(p.get("slug") or "").lower() == hint.lower():
            return p
    for p in store.list_projects():
        name_n = _norm(p["name"])
        if hint_n in name_n or name_n in hint_n:
            return p
    return None


def is_roster_question(utterance: str) -> bool:
    text = (utterance or "").strip()
    if not text:
        return False
    if _LIST_CHATS.search(text) or _LIST_CHATS2.match(text) or _STATUS.match(text):
        return True
    return False


def utterance_requests_structural_action(utterance: str) -> bool:
    return bool(STRUCTURAL_ACTION_RE.search(utterance or ""))


def parse_directive(utterance: str) -> dict[str, Any] | None:
    text = (utterance or "").strip()
    if not text:
        return None

    if is_roster_question(text):
        return {"tool": "list_chats"}

    m = _SPAWN_PREFIX.match(text)
    if m:
        rest = text[m.end() :].strip()
        bm = _BRIEF_TAIL.match(rest)
        if bm:
            title, brief = _strip_article(bm.group(1).strip()), bm.group(2).strip()
        else:
            title, brief = _strip_article(rest), ""
        if title:
            return {"tool": "spawn_sub_chat", "title": title[:200], "brief": brief[:4000]}

    if _SUMMARISE_ALL.match(text):
        return {"tool": "summarise_all"}

    m = _SUMMARISE_ONE.match(text)
    if m and not _SUMMARISE_ALL.match(text):
        target = m.group(1).strip()
        if target and _norm(target) not in {"everything", "all", "all chats"}:
            return {"tool": "summarise_sub_chat", "target": target}

    m = _ARCHIVE.match(text)
    if m:
        return {"tool": "archive_sub_chat", "target": m.group(1).strip()}

    m = _UNARCHIVE.match(text)
    if m:
        return {"tool": "unarchive_sub_chat", "target": m.group(1).strip()}

    m = _RENAME.match(text)
    if m:
        return {"tool": "rename_sub_chat", "target": m.group(1).strip(), "title": m.group(2).strip()[:200]}

    m = _MOVE.match(text)
    if m:
        return {"tool": "move_sub_chat", "target": m.group(1).strip(), "project_hint": m.group(2).strip()}

    m = _BRIEF_CHAT.match(text)
    if m:
        return {"tool": "update_brief", "target": m.group(1).strip(), "brief": m.group(2).strip()[:4000]}

    m = _INJECT.match(text)
    if m:
        return {
            "tool": "inject_context",
            "target": m.group(1).strip(),
            "content": m.group(2).strip(),
            "reason": "supervisor directive",
        }

    return None


STRUCTURAL_TOOLS = frozenset({
    "move_sub_chat",
    "archive_sub_chat",
    "unarchive_sub_chat",
    "rename_sub_chat",
    "update_brief",
    "inject_context",
    "summarise_sub_chat",
    "summarise_all",
})


def utterance_has_verb(utterance: str, tool: str) -> bool:
    u = utterance or ""
    if tool == "move_sub_chat":
        return bool(re.search(r"\bmove\b", u, re.I))
    if tool == "archive_sub_chat":
        return bool(re.search(r"\barchive\b", u, re.I))
    if tool == "unarchive_sub_chat":
        return bool(re.search(r"\b(?:unarchive|restore)\b", u, re.I))
    if tool == "rename_sub_chat":
        return bool(re.search(r"\brename\b", u, re.I))
    if tool == "update_brief":
        return bool(re.search(r"\bbrief\b", u, re.I))
    if tool == "inject_context":
        return bool(re.search(r"\b(?:inject|give)\b", u, re.I))
    if tool == "summarise_sub_chat":
        return bool(re.search(r"\bsummari[sz]e\b", u, re.I))
    return True


def normalize_tool(
    store: AgentStore,
    project_id: int,
    tool: dict[str, Any],
    utterance: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve ids/targets and reject unsafe structural actions. Returns (tool, error_reply)."""
    name = str(tool.get("tool") or "answer")
    if name == "answer":
        return tool, None

    if name in STRUCTURAL_TOOLS and not utterance_has_verb(utterance, name):
        return None, (
            "I didn't run that — say explicitly what to change "
            "(for example: move API refactor to project Other, archive Research, or rename X to Y)."
        )

    def _conv_in_project(conv_id: int) -> dict[str, Any] | None:
        conv = store.get_conversation(conv_id)
        if not conv or conv.get("kind") == "supervisor":
            return None
        if int(conv.get("project_id") or 0) != project_id:
            return None
        return conv

    if name == "spawn_sub_chat":
        return tool, None

    if name == "summarise_all":
        if not re.search(r"\bsummari[sz]e\b", utterance or "", re.I):
            return None, "Say “summarise everything” to refresh all sub-chat briefs."
        return tool, None

    if name == "move_sub_chat":
        dest = tool.get("project_id")
        if dest is None and tool.get("project_hint"):
            proj = resolve_project(store, str(tool["project_hint"]))
            if not proj:
                return None, f"I couldn't find project {tool['project_hint']!r}."
            dest = int(proj["id"])
        if dest is None:
            return None, "Which project should I move it to? Say: move <chat> to project <name>."
        conv = None
        if tool.get("conversation_id") is not None:
            raw = store.get_conversation(int(tool["conversation_id"]))
            if raw and int(raw.get("project_id") or 0) != project_id:
                return None, "That chat is in another project — I only manage chats in this project."
            conv = _conv_in_project(int(tool["conversation_id"]))
        if conv is None and tool.get("target"):
            conv = resolve_chat_in_project(store, project_id, str(tool["target"]))
        if conv is None:
            return None, "Which chat in this project should I move?"
        return {**tool, "conversation_id": conv["id"], "project_id": int(dest)}, None

    needs_target = {
        "archive_sub_chat",
        "unarchive_sub_chat",
        "rename_sub_chat",
        "update_brief",
        "inject_context",
        "summarise_sub_chat",
    }
    if name in needs_target:
        conv = None
        if tool.get("conversation_id") is not None:
            raw = store.get_conversation(int(tool["conversation_id"]))
            if raw and int(raw.get("project_id") or 0) != project_id:
                return None, "That chat is in another project — I only manage chats in this project."
            conv = _conv_in_project(int(tool["conversation_id"]))
        if conv is None and tool.get("target"):
            conv = resolve_chat_in_project(store, project_id, str(tool["target"]))
        if conv is None:
            return None, "Which sub-chat did you mean? Use its title or alias from this project."
        out = {**tool, "conversation_id": conv["id"]}
        if name == "rename_sub_chat" and not (tool.get("title") or "").strip():
            return None, "What should I rename it to?"
        if name == "update_brief" and not (tool.get("brief") or "").strip():
            return None, "What should the brief say?"
        if name == "inject_context" and not (tool.get("content") or "").strip():
            return None, "What background should I inject?"
        return out, None

    if name == "list_chats":
        return tool, None

    return tool, None