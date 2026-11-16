"""Document ingestion (txt/md/pdf), chunking, embedding, and retrieval."""

from __future__ import annotations

import hashlib
import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.llm import Embedder
from agent.store import AgentStore, ScoredChunk

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".yaml", ".yml", ".log",
    ".py", ".js", ".ts", ".html", ".css", ".sh", ".toml", ".ini", ".xml",
}
PDF_EXTENSIONS = {".pdf"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | PDF_EXTENSIONS


class IngestError(ValueError):
    pass


@dataclass
class IngestResult:
    document: dict[str, Any]
    duplicate: bool = False


def extract_text(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext in PDF_EXTENSIONS:
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover
            raise IngestError("pypdf is not installed; cannot read PDF files") from exc
        try:
            reader = PdfReader(io.BytesIO(data))
            pages = [(page.extract_text() or "") for page in reader.pages]
        except Exception as exc:
            raise IngestError(f"Could not parse PDF: {exc}") from exc
        return "\n\n".join(p.strip() for p in pages if p.strip())
    if ext in TEXT_EXTENSIONS or not ext:
        for enc in ("utf-8", "utf-16"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")
    raise IngestError(
        f"Unsupported file type {ext!r}. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
    )


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def chunk_text(text: str, chunk_size: int = 900, overlap: int = 150) -> list[str]:
    """Paragraph-aware chunking with character overlap between consecutive chunks."""
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    for para in paragraphs:
        if len(para) <= chunk_size:
            pieces.append(para)
            continue
        buf = ""
        for sent in _SENTENCE_RE.split(para):
            while len(sent) > chunk_size:  # pathological long sentence: hard split
                if buf:
                    pieces.append(buf)
                    buf = ""
                pieces.append(sent[:chunk_size])
                sent = sent[chunk_size:]
            if len(buf) + len(sent) + 1 > chunk_size and buf:
                pieces.append(buf)
                buf = sent
            else:
                buf = f"{buf} {sent}".strip()
        if buf:
            pieces.append(buf)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if not current:
            current = piece
        elif len(current) + len(piece) + 2 <= chunk_size:
            current = f"{current}\n\n{piece}"
        else:
            chunks.append(current)
            tail = current[-overlap:] if overlap > 0 else ""
            if tail and " " in tail:
                tail = tail[tail.index(" ") + 1 :]
            current = f"{tail}\n\n{piece}".strip() if tail else piece
    if current:
        chunks.append(current)
    return chunks


class RagService:
    def __init__(self, store: AgentStore, embedder: Embedder, *, chunk_size: int = 900, overlap: int = 150,
                 max_bytes: int = 25 * 1024 * 1024) -> None:
        self.store = store
        self.embedder = embedder
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.max_bytes = max_bytes

    def ingest(self, filename: str, data: bytes, mime: str | None = None) -> IngestResult:
        filename = Path(filename or "upload.txt").name[:200]
        if not data:
            raise IngestError("File is empty")
        if len(data) > self.max_bytes:
            raise IngestError(f"File too large ({len(data)} bytes > {self.max_bytes})")
        sha = hashlib.sha256(data).hexdigest()
        existing = self.store.find_document_by_hash(sha)
        if existing:
            return IngestResult(document=existing, duplicate=True)
        text = extract_text(filename, data)
        chunks = chunk_text(text, self.chunk_size, self.overlap)
        if not chunks:
            raise IngestError("No extractable text found in file")
        embeddings = self.embedder.embed_documents(chunks)
        doc = self.store.add_document(
            filename=filename,
            mime=mime,
            size_bytes=len(data),
            sha256=sha,
            chunks=chunks,
            embeddings=embeddings,
            embed_model=self.embedder.model,
        )
        logger.info("Ingested %s: %d chunks", filename, len(chunks))
        return IngestResult(document=doc)

    def search(self, query: str, k: int = 4, min_score: float = 0.0) -> list[ScoredChunk]:
        if not query.strip():
            return []
        vec = self.embedder.embed_query(query)
        return [c for c in self.store.search_chunks(vec, k=k) if c.score >= min_score]
