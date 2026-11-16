import pytest

from agent.rag import IngestError, RagService, chunk_text, extract_text


def test_chunk_text_sizes_and_overlap():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 60 for i in range(10))
    chunks = chunk_text(text, chunk_size=400, overlap=80)
    assert len(chunks) > 3
    assert all(len(c) <= 400 + 100 for c in chunks)


def test_chunk_text_long_sentence_hard_split():
    chunks = chunk_text("x" * 2500, chunk_size=1000, overlap=0)
    assert len(chunks) == 3


def test_extract_text_types():
    assert extract_text("a.md", b"# Hi") == "# Hi"
    with pytest.raises(IngestError):
        extract_text("a.exe", b"MZ")


def test_extract_pdf():
    pypdf = pytest.importorskip("pypdf")
    import io

    w = pypdf.PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    assert extract_text("blank.pdf", buf.getvalue()) == ""


def test_ingest_search_delete(store, embedder):
    rag = RagService(store, embedder, chunk_size=300, overlap=50)
    doc = rag.ingest("pets.md", b"Rex is a golden retriever who loves swimming.\n\nThe cat is named Miso.").document
    rag.ingest("cars.txt", b"The car is a blue Toyota Corolla from 2015.")
    assert doc["chunk_count"] >= 1
    dup = rag.ingest("pets-copy.md", b"Rex is a golden retriever who loves swimming.\n\nThe cat is named Miso.")
    assert dup.duplicate is True
    hits = rag.search("golden retriever swimming", k=2)
    assert hits[0].filename == "pets.md"
    assert store.delete_document(doc["id"])
    assert all(h.filename != "pets.md" for h in rag.search("golden retriever", k=5))


def test_ingest_rejects_empty_and_large(store, embedder):
    rag = RagService(store, embedder, max_bytes=10)
    with pytest.raises(IngestError):
        rag.ingest("a.txt", b"")
    with pytest.raises(IngestError):
        rag.ingest("a.txt", b"x" * 11)
