"""Message content updates keep FTS search in sync."""

from __future__ import annotations

from agent.store import AgentStore


def test_set_message_content_reindexes_fts(tmp_path) -> None:
    db = tmp_path / "agent.db"
    store = AgentStore(db)
    conv = store.create_conversation("FTS fold")
    cid = int(conv["id"])
    mid = store.add_message(cid, "user", "What is the weather today?", {})
    store.set_message_content(mid, "What is the weather today?\n\n(added: and platypuses)")
    hits = store.search_messages("platypuses", limit=10)
    assert hits, "expected FTS to find folded addition"
    assert any(int(h.get("conversation_id") or 0) == cid for h in hits)