from agent.memory import MemoryService, parse_memory_json
from tests.conftest import FakeLLM


def test_parse_memory_json_variants():
    assert parse_memory_json('{"memories": [{"content": "User has a cat.", "category": "fact"}]}') == [
        {"content": "User has a cat.", "category": "fact"}]
    assert parse_memory_json('noise {"memories": ["User likes jazz."]} trailing')[0]["content"] == "User likes jazz."
    assert parse_memory_json("not json") == []
    assert parse_memory_json('{"memories": [{"content": "x", "category": "weird"}]}') == []


def test_extraction_creates_pending_and_dedups(store, embedder):
    svc = MemoryService(store, embedder, FakeLLM())
    msgs = [{"role": "user", "content": "I really love green tea"}, {"role": "assistant", "content": "Noted!"}]
    created = svc.extract_from_messages(msgs, conversation_id=None)
    assert len(created) == 1 and created[0]["status"] == "pending" and created[0]["source"] == "auto"
    again = svc.extract_from_messages(msgs)
    assert again == []  # duplicate suppressed
    # approve → becomes searchable
    svc.update(created[0]["id"], status="active")
    hits = svc.search("green tea", k=3)
    assert hits and hits[0][0]["content"] == "User likes green tea."


def test_manual_crud(store, embedder):
    svc = MemoryService(store, embedder)
    m = svc.add("User's dog is called Rex", category="person")
    assert m["has_embedding"] and m["category"] == "person"
    m2 = svc.update(m["id"], content="User's dog is called Max")
    assert m2["content"].endswith("Max")
    assert store.delete_memory(m["id"])
