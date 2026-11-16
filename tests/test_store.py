from agent.store import AgentStore, _fts_query


def test_conversation_crud_and_search(store: AgentStore):
    c = store.create_conversation("Trip planning")
    store.add_message(c["id"], "user", "Book a flight to Melbourne next Friday")
    store.add_message(c["id"], "assistant", "Sure, which airline do you prefer?")
    c2 = store.create_conversation()
    store.add_message(c2["id"], "user", "What's the weather like?")

    convs = store.list_conversations()
    assert {x["id"] for x in convs} == {c["id"], c2["id"]}
    assert store.get_conversation(c["id"])["message_count"] == 2

    assert [x["id"] for x in store.list_conversations(q="melbourne")] == [c["id"]]
    hits = store.search_messages("airline")
    assert hits and hits[0]["conversation_id"] == c["id"] and "[airline]" in hits[0]["snippet"]

    renamed = store.rename_conversation(c["id"], "Melbourne trip")
    assert renamed["title"] == "Melbourne trip"
    assert store.delete_conversation(c["id"]) is True
    assert store.get_messages(c["id"]) == []  # cascade
    assert store.search_messages("airline") == []  # FTS delete trigger
    assert store.delete_conversation(c["id"]) is False


def test_history_limit_returns_latest_in_order(store: AgentStore):
    c = store.create_conversation()
    for i in range(10):
        store.add_message(c["id"], "user", f"msg {i}")
    last = store.get_messages(c["id"], limit=3)
    assert [m["content"] for m in last] == ["msg 7", "msg 8", "msg 9"]


def test_memory_vector_search(store: AgentStore):
    store.add_memory("likes tea", embedding=[1.0, 0.0, 0.0])
    store.add_memory("owns a dog", embedding=[0.0, 1.0, 0.0])
    store.add_memory("pending one", status="pending", embedding=[1.0, 0.1, 0.0])
    hits = store.search_memories([0.9, 0.1, 0.0], k=5)
    assert [m["content"] for m, _ in hits][0] == "likes tea"
    assert all(m["status"] == "active" for m, _ in hits)
    assert "embedding" not in hits[0][0]


def test_vector_backend_reported(store: AgentStore):
    assert store.vector_backend in {"sqlite-vec", "numpy"}
    assert store.stats()["vector_backend"] == store.vector_backend


def test_numpy_fallback_matches(tmp_path):
    s = AgentStore(tmp_path / "x.db")
    s.vector_backend = "numpy"
    s.add_memory("a", embedding=[1.0, 0.0])
    s.add_memory("b", embedding=[0.0, 1.0])
    assert s.search_memories([0.0, 1.0], k=1)[0][0]["content"] == "b"


def test_fts_query_sanitises():
    assert _fts_query('foo "bar" OR baz*') == '"foo"* "bar"* "OR"* "baz"*'
    assert _fts_query("!!!") == '""'
