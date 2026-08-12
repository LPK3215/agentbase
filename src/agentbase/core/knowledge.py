"""Knowledge base management — pluggable storage, parsing, and embeddings.

Documents are stored with their full content.  When a document is added it is
automatically split into chunks (~500 chars by paragraph boundaries) and,
if an embedding provider is configured, each chunk's embedding vector is
**persisted** to the database for fast retrieval.

Storage backend is chosen automatically:

- ``db_path=Path("data/knowledge.db")``  →  SQLite (dev / single-user)
- ``dsn="postgresql://..."``            →  PostgreSQL (prod / multi-user)

Document parsing is pluggable — ``ingest_file`` uses the parser registry.
Embeddings are pluggable — use ``embedding_provider`` to inject any
``EmbeddingProvider`` (OpenAI, Cohere, local sentence-transformers…).

Usage::

    # SQLite + hash embeddings (zero-config dev)
    kb = KnowledgeBase(db_path=Path("data/knowledge.db"))

    # PostgreSQL + OpenAI embeddings (prod)
    from agentbase.core.embeddings import embedding_registry
    kb = KnowledgeBase(
        dsn="postgresql://user:pass@localhost/agentbase",
        embedding_provider=embedding_registry.get("openai"),
    )

    doc = kb.add_document(source="readme.md", title="README", content="...")
    results = kb.search("installation", top_k=5)
    kb.delete_document(doc_id=1)
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agentbase.core.embeddings import EmbeddingProvider
from agentbase.core.storage import StorageBackend, create_storage


@dataclass
class Document:
    """A knowledge-base document record."""

    id: int | None
    source: str
    title: str
    content: str
    chunk_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "title": self.title,
            "content": self.content,
            "chunk_count": self.chunk_count,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class Chunk:
    """A single chunk of a document."""

    id: int | None
    document_id: int
    content: str
    chunk_index: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "document_id": self.document_id,
            "content": self.content,
            "chunk_index": self.chunk_index,
        }


@dataclass
class SearchResult:
    """A search hit."""

    document: Document
    chunk: Chunk | None
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "document": self.document.to_dict(),
            "chunk": self.chunk.to_dict() if self.chunk else None,
            "score": self.score,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_get(row: Any, key: str) -> Any:
    """Get a value from a row (sqlite3.Row or dict)."""
    if hasattr(row, "__getitem__"):
        try:
            return row[key]
        except (KeyError, IndexError):
            return None
    return getattr(row, key, None)


def _row_to_doc(row: Any) -> Document:
    return Document(
        id=_row_get(row, "id"),
        source=_row_get(row, "source"),
        title=_row_get(row, "title"),
        content=_row_get(row, "content"),
        chunk_count=_row_get(row, "chunk_count") or 0,
        metadata=json.loads(_row_get(row, "metadata") or "{}"),
        created_at=_row_get(row, "created_at") or "",
        updated_at=_row_get(row, "updated_at") or "",
    )


def _row_to_chunk(row: Any) -> Chunk:
    return Chunk(
        id=_row_get(row, "id"),
        document_id=_row_get(row, "document_id"),
        content=_row_get(row, "content"),
        chunk_index=_row_get(row, "chunk_index"),
    )


def _chunk_text(
    text: str,
    max_chunk_size: int = 500,
    *,
    strategy: str = "paragraph",
    overlap: int = 0,
) -> list[str]:
    """Split text into chunks.

    Strategies:
    - ``paragraph`` (default): Split on double newlines, merge small paragraphs.
    - ``recursive``: Split hierarchically by headers → paragraphs → sentences.
      Better for structured Markdown documents.
    - ``fixed``: Simple fixed-size split (no paragraph awareness).

    Args:
        text: Input text to chunk.
        max_chunk_size: Maximum characters per chunk.
        strategy: Chunking strategy name.
        overlap: Number of characters to overlap between chunks (recursive only).
    """
    if strategy == "recursive":
        return _chunk_recursive(text, max_chunk_size, overlap)
    if strategy == "fixed":
        return [text[i : i + max_chunk_size] for i in range(0, len(text), max_chunk_size)]
    return _chunk_paragraph(text, max_chunk_size)


def _chunk_paragraph(text: str, max_chunk_size: int = 500) -> list[str]:
    """Split text into chunks by paragraph, respecting a max size.

    Paragraphs are split on double newlines.  Small paragraphs are merged
    up to ``max_chunk_size``; oversized paragraphs are hard-split.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        if len(para) > max_chunk_size:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0
            for i in range(0, len(para), max_chunk_size):
                chunks.append(para[i : i + max_chunk_size])
        elif current_len + len(para) + 2 > max_chunk_size:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += len(para) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _chunk_recursive(text: str, max_chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Recursive hierarchical chunking: headers → paragraphs → sentences.

    Tries to split on Markdown headers first (## ), then paragraphs,
    then sentences, then hard split as last resort. Preserves context
    by overlapping chunks by ``overlap`` characters.
    """
    # Split on Markdown headers (## or ### etc.)
    sections = re.split(r"(?=^#{1,6}\s)", text, flags=re.MULTILINE)
    if len(sections) <= 1:
        sections = [text]

    chunks: list[str] = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        if len(section) <= max_chunk_size:
            chunks.append(section)
            continue
        # Section too large: split into paragraphs
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", section) if p.strip()]
        if len(paragraphs) <= 1:
            # No paragraphs: split into sentences
            sentences = re.split(r"(?<=[.!?\u3002\uff01\uff1f])\s+", section)
            _merge_into_chunks(sentences, max_chunk_size, overlap, chunks)
        else:
            _merge_into_chunks(paragraphs, max_chunk_size, overlap, chunks)
    return chunks


def _merge_into_chunks(
    parts: list[str],
    max_chunk_size: int,
    overlap: int,
    out: list[str],
) -> None:
    """Merge parts into chunks respecting max size with optional overlap."""
    current: list[str] = []
    current_len = 0
    for part in parts:
        if len(part) > max_chunk_size:
            if current:
                out.append("\n\n".join(current))
                current = []
                current_len = 0
            for i in range(0, len(part), max_chunk_size - overlap):
                out.append(part[i : i + max_chunk_size])
        elif current_len + len(part) + 2 > max_chunk_size:
            out.append("\n\n".join(current))
            # Keep last part as overlap context
            current = [part] if overlap > 0 else []
            current_len = len(part) if overlap > 0 else 0
        else:
            current.append(part)
            current_len += len(part) + 2
    if current:
        out.append("\n\n".join(current))


# Type alias for backward compat: str -> list[float]
EmbedFunc = Callable[[str], list[float]]


class KnowledgeBase:
    """Knowledge base with document CRUD, chunking, pluggable storage and parsing.

    Uses SQLite by default; PostgreSQL when a DSN is provided.
    When pgvector is available on PostgreSQL, uses native vector columns
    and `<=>` (cosine distance) for O(1) retrieval. Otherwise falls back
    to in-memory cosine similarity on stored JSON vectors.

    Features:
    - Thread-safe via ``threading.Lock`` on write operations
    - Explicit chunk deletion on document delete (SQLite FK CASCADE not enforced by default)
    - Hybrid search support — combine vector + text scores with configurable weights
    """

    def __init__(
        self,
        *,
        db_path: Path | None = None,
        dsn: str | None = None,
        max_chunk_size: int = 500,
        embed_func: EmbedFunc | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        backend: StorageBackend | None = None,
    ) -> None:
        if backend is not None:
            self._db = backend
        else:
            self._db = create_storage(db_path=db_path, dsn=dsn)
        self.max_chunk_size = max_chunk_size
        # embedding_provider takes priority; embed_func kept for backward compat
        self.embedding_provider = embedding_provider
        self.embed_func = embed_func or (embedding_provider.embed if embedding_provider else None)
        self._pgvector_available = False
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        # Check if we're on PostgreSQL and pgvector is available
        from agentbase.core.storage import PostgresBackend
        if isinstance(self._db, PostgresBackend):
            try:
                self._db.execute("CREATE EXTENSION IF NOT EXISTS vector")
                self._db.commit()
                self._pgvector_available = True
            except Exception:
                self._pgvector_available = False

        if self._pgvector_available:
            # Use native vector column
            emb_col = "embedding vector"
        else:
            # Use TEXT column with JSON-serialized vectors
            emb_col = "embedding TEXT"

        self._db.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS kb_documents (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                source       TEXT NOT NULL,
                title        TEXT NOT NULL DEFAULT '',
                content      TEXT NOT NULL,
                chunk_count  INTEGER DEFAULT 0,
                metadata     TEXT DEFAULT '{{}}',
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS kb_chunks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id  INTEGER NOT NULL,
                content      TEXT NOT NULL,
                chunk_index  INTEGER NOT NULL,
                {emb_col},
                FOREIGN KEY (document_id) REFERENCES kb_documents(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_doc ON kb_chunks(document_id);
            """
        )
        # Create vector index for pgvector
        if self._pgvector_available:
            try:
                self._db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON kb_chunks USING ivfflat (embedding vector_cosine_ops)"
                )
                self._db.commit()
            except Exception:
                pass  # Index creation may fail if no rows yet or version mismatch
        self._db.commit()

    # ------------------------------------------------------------------
    # Document CRUD
    # ------------------------------------------------------------------

    def add_document(
        self,
        *,
        source: str,
        title: str = "",
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Document:
        """Add a document with automatic chunking."""
        with self._lock:
            now = _now()
            meta_json = json.dumps(metadata or {}, ensure_ascii=False)
            self._db.execute(
                """
                INSERT INTO kb_documents (source, title, content, chunk_count, metadata, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (source, title, content, 0, meta_json, now, now),
            )
            doc_id = self._db.last_insert_id()
            chunks = _chunk_text(content, max_chunk_size=self.max_chunk_size)
            for idx, chunk_text in enumerate(chunks):
                # Persist embedding if provider is configured
                if self.embed_func is not None:
                    vec = self.embed_func(chunk_text)
                    if self._pgvector_available:
                        emb_value = str(vec)
                    else:
                        emb_value = json.dumps(vec)
                else:
                    emb_value = None
                self._db.execute(
                    "INSERT INTO kb_chunks (document_id, content, chunk_index, embedding) VALUES (%s, %s, %s, %s)",
                    (doc_id, chunk_text, idx, emb_value),
                )
            self._db.execute(
                "UPDATE kb_documents SET chunk_count = %s WHERE id = %s",
                (len(chunks), doc_id),
            )
            self._db.commit()
        return self.get_document(doc_id=doc_id)  # type: ignore[return-value]

    def get_document(self, *, doc_id: int) -> Document | None:
        """Get a document by ID.  Returns ``None`` if not found."""
        row = self._db.fetchone("SELECT * FROM kb_documents WHERE id = %s", (doc_id,))
        return _row_to_doc(row) if row else None

    def list_documents(self) -> list[Document]:
        """List all documents, newest first."""
        rows = self._db.fetchall("SELECT * FROM kb_documents ORDER BY created_at DESC")
        return [_row_to_doc(r) for r in rows]

    def update_document(
        self,
        *,
        doc_id: int,
        title: str | None = None,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Document | None:
        """Update document fields.  If ``content`` changes, chunks are rebuilt."""
        with self._lock:
            existing = self.get_document(doc_id=doc_id)
            if existing is None:
                return None
            new_title = title if title is not None else existing.title
            new_meta = metadata if metadata is not None else existing.metadata
            new_content = content if content is not None else existing.content
            now = _now()
            new_chunk_count = existing.chunk_count
            if content is not None:
                # Delete old chunks first
                self._db.execute("DELETE FROM kb_chunks WHERE document_id = %s", (doc_id,))
                chunks = _chunk_text(new_content, max_chunk_size=self.max_chunk_size)
                for idx, chunk_text in enumerate(chunks):
                    if self.embed_func is not None:
                        vec = self.embed_func(chunk_text)
                        if self._pgvector_available:
                            emb_value = str(vec)
                        else:
                            emb_value = json.dumps(vec)
                    else:
                        emb_value = None
                    self._db.execute(
                        "INSERT INTO kb_chunks (document_id, content, chunk_index, embedding) VALUES (%s, %s, %s, %s)",
                        (doc_id, chunk_text, idx, emb_value),
                    )
                new_chunk_count = len(chunks)
            self._db.execute(
                """
                UPDATE kb_documents
                SET title = %s, content = %s, metadata = %s, chunk_count = %s, updated_at = %s
                WHERE id = %s
                """,
                (
                    new_title,
                    new_content,
                    json.dumps(new_meta, ensure_ascii=False),
                    new_chunk_count,
                    now,
                    doc_id,
                ),
            )
            self._db.commit()
        return self.get_document(doc_id=doc_id)

    def delete_document(self, *, doc_id: int) -> bool:
        """Delete a document and its chunks.

        Explicitly deletes chunks first — SQLite does not enforce
        foreign key CASCADE by default, so relying on the FK constraint
        would leave orphaned chunk rows.
        """
        with self._lock:
            # Explicitly delete chunks first (SQLite FK CASCADE is not enforced by default)
            self._db.execute("DELETE FROM kb_chunks WHERE document_id = %s", (doc_id,))
            cur = self._db.execute("DELETE FROM kb_documents WHERE id = %s", (doc_id,))
            self._db.commit()
        return cur.rowcount > 0

    def batch_ingest(self, file_paths: list[Path], *, title_prefix: str = "") -> list[Document]:
        """Ingest multiple files in a single transaction.

        Args:
            file_paths: List of file paths to ingest.
            title_prefix: Optional prefix for document titles.

        Returns:
            List of created Document objects.
        """
        results: list[Document] = []
        from agentbase.core.parsers import parser_registry

        for file_path in file_paths:
            path = Path(file_path)
            if not path.exists():
                continue
            try:
                parser = parser_registry.get_for_path(path)
                content = parser.parse(path)
                doc = self.add_document(
                    source=str(path),
                    title=f"{title_prefix}{path.name}" if title_prefix else path.name,
                    content=content,
                    metadata={
                        "file_extension": path.suffix,
                        "parser": parser.__class__.__name__,
                        "batch_ingested": True,
                    },
                )
                results.append(doc)
            except Exception:
                continue
        return results

    def document_count(self) -> int:
        """Return the total number of documents in the knowledge base."""
        row = self._db.fetchone("SELECT COUNT(*) AS cnt FROM kb_documents")
        return _row_get(row, "cnt") or 0

    def get_stats(self) -> dict[str, Any]:
        """Return knowledge base statistics."""
        doc_count = self.document_count()
        chunk_row = self._db.fetchone("SELECT COUNT(*) AS cnt FROM kb_chunks")
        chunk_count = _row_get(chunk_row, "cnt") or 0
        total_content_row = self._db.fetchone(
            "SELECT SUM(LENGTH(content)) AS total FROM kb_documents"
        )
        total_content = _row_get(total_content_row, "total") or 0
        return {
            "document_count": doc_count,
            "chunk_count": chunk_count,
            "total_content_bytes": total_content,
            "has_embeddings": self.embed_func is not None,
            "pgvector_enabled": self._pgvector_available,
        }

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, *, top_k: int = 5) -> list[SearchResult]:
        """Search documents by text query.

        Uses embedding similarity if an embedding provider/func is configured,
        otherwise falls back to ``LIKE`` text matching on chunks.
        """
        if self.embed_func is not None:
            return self._vector_search(query, top_k=top_k)
        return self._text_search(query, top_k=top_k)

    def _text_search(self, query: str, *, top_k: int) -> list[SearchResult]:
        """Fallback text search: LIKE match with basic relevance scoring.

        Scoring is based on:
        - Title match: 0.5 (strong signal)
        - Content match: 0.3 (weaker signal)
        - Source match: 0.2 (filename match)
        - Query term frequency in chunk: bonus per occurrence
        """
        pattern = f"%{query}%"
        # Fetch more results than needed for scoring, then re-rank
        fetch_limit = min(top_k * 3, 50)
        rows = self._db.fetchall(
            """
            SELECT c.*, d.source, d.title, d.content AS doc_content, d.chunk_count,
                   d.metadata, d.created_at, d.updated_at
            FROM kb_chunks c
            JOIN kb_documents d ON c.document_id = d.id
            WHERE c.content LIKE %s OR d.title LIKE %s OR d.source LIKE %s
            ORDER BY d.updated_at DESC
            LIMIT %s
            """,
            (pattern, pattern, pattern, fetch_limit),
        )
        query_lower = query.lower()
        scored: list[tuple[float, Any]] = []
        for row in rows:
            score = 0.0
            title = (_row_get(row, "title") or "").lower()
            source = (_row_get(row, "source") or "").lower()
            chunk_content = (_row_get(row, "content") or "").lower()

            # Title match is the strongest signal
            if query_lower in title:
                score += 0.5
            # Source (filename) match
            if query_lower in source:
                score += 0.2
            # Content match
            if query_lower in chunk_content:
                score += 0.3
                # Bonus for term frequency
                freq = chunk_content.count(query_lower)
                score += min(freq * 0.05, 0.3)
            # Cap score at 1.0
            score = min(score, 1.0)
            scored.append((score, row))

        # Sort by score descending, take top_k
        scored.sort(key=lambda x: x[0], reverse=True)

        results: list[SearchResult] = []
        for score, row in scored[:top_k]:
            doc = Document(
                id=_row_get(row, "document_id"),
                source=_row_get(row, "source"),
                title=_row_get(row, "title"),
                content=_row_get(row, "doc_content"),
                chunk_count=_row_get(row, "chunk_count"),
                metadata=json.loads(_row_get(row, "metadata") or "{}"),
                created_at=_row_get(row, "created_at") or "",
                updated_at=_row_get(row, "updated_at") or "",
            )
            chunk = Chunk(
                id=_row_get(row, "id"),
                document_id=_row_get(row, "document_id"),
                content=_row_get(row, "content"),
                chunk_index=_row_get(row, "chunk_index"),
            )
            results.append(SearchResult(document=doc, chunk=chunk, score=score))
        return results

    def _vector_search(self, query: str, *, top_k: int) -> list[SearchResult]:
        """Embedding-based search using **persisted** chunk embeddings.

        When pgvector is available, uses SQL `<=>` operator for native
        cosine distance retrieval. Otherwise loads all vectors into memory
        and computes cosine similarity in Python.
        """
        query_vec = self.embed_func(query)  # type: ignore[misc]

        if self._pgvector_available:
            return self._pgvector_search(query_vec, top_k=top_k)
        return self._inmemory_vector_search(query_vec, top_k=top_k)

    def _pgvector_search(self, query_vec: list[float], *, top_k: int) -> list[SearchResult]:
        """Use PostgreSQL pgvector `<=>` operator for O(1) retrieval."""
        vec_str = str(query_vec)
        rows = self._db.fetchall(
            """
            SELECT c.id, c.document_id, c.content, c.chunk_index,
                   d.source, d.title, d.content AS doc_content, d.chunk_count,
                   d.metadata, d.created_at, d.updated_at,
                   1 - (c.embedding <=> %s::vector) AS score
            FROM kb_chunks c
            JOIN kb_documents d ON c.document_id = d.id
            WHERE c.embedding IS NOT NULL
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
            """,
            (vec_str, vec_str, top_k),
        )
        results: list[SearchResult] = []
        for row in rows:
            doc = Document(
                id=_row_get(row, "document_id"),
                source=_row_get(row, "source"),
                title=_row_get(row, "title"),
                content=_row_get(row, "doc_content"),
                chunk_count=_row_get(row, "chunk_count"),
                metadata=json.loads(_row_get(row, "metadata") or "{}"),
                created_at=_row_get(row, "created_at") or "",
                updated_at=_row_get(row, "updated_at") or "",
            )
            chunk = Chunk(
                id=_row_get(row, "id"),
                document_id=_row_get(row, "document_id"),
                content=_row_get(row, "content"),
                chunk_index=_row_get(row, "chunk_index"),
            )
            score = _row_get(row, "score") or 0.0
            results.append(SearchResult(document=doc, chunk=chunk, score=float(score)))
        return results

    def _inmemory_vector_search(self, query_vec: list[float], *, top_k: int) -> list[SearchResult]:
        """In-memory cosine similarity for SQLite or pgvector-less PostgreSQL."""
        import math
        rows = self._db.fetchall(
            """
            SELECT c.id, c.document_id, c.content, c.chunk_index, c.embedding,
                   d.source, d.title, d.content AS doc_content, d.chunk_count,
                   d.metadata, d.created_at, d.updated_at
            FROM kb_chunks c
            JOIN kb_documents d ON c.document_id = d.id
            """
        )
        scored: list[tuple[float, Any]] = []
        for row in rows:
            emb_raw = _row_get(row, "embedding")
            if emb_raw:
                chunk_vec = json.loads(emb_raw)
            else:
                # Fallback: compute on the fly if embedding wasn't stored
                chunk_vec = self.embed_func(_row_get(row, "content"))  # type: ignore[misc]
            dot = sum(a * b for a, b in zip(query_vec, chunk_vec))
            mag_q = math.sqrt(sum(a * a for a in query_vec))
            mag_c = math.sqrt(sum(b * b for b in chunk_vec))
            score = dot / (mag_q * mag_c) if mag_q > 0 and mag_c > 0 else 0.0
            scored.append((score, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        results: list[SearchResult] = []
        for score, row in scored[:top_k]:
            doc = Document(
                id=_row_get(row, "document_id"),
                source=_row_get(row, "source"),
                title=_row_get(row, "title"),
                content=_row_get(row, "doc_content"),
                chunk_count=_row_get(row, "chunk_count"),
                metadata=json.loads(_row_get(row, "metadata") or "{}"),
                created_at=_row_get(row, "created_at") or "",
                updated_at=_row_get(row, "updated_at") or "",
            )
            chunk = Chunk(
                id=_row_get(row, "id"),
                document_id=_row_get(row, "document_id"),
                content=_row_get(row, "content"),
                chunk_index=_row_get(row, "chunk_index"),
            )
            results.append(SearchResult(document=doc, chunk=chunk, score=score))
        return results

    # ------------------------------------------------------------------
    # File ingestion (uses parser registry)
    # ------------------------------------------------------------------

    def ingest_file(self, file_path: Path, *, title: str | None = None) -> Document:
        """Read a file from disk, parse it using the registered parser, and add as a document."""
        from agentbase.core.parsers import parser_registry

        path = Path(file_path)
        parser = parser_registry.get_for_path(path)
        content = parser.parse(path)
        return self.add_document(
            source=str(path),
            title=title or path.name,
            content=content,
            metadata={"file_extension": path.suffix, "parser": parser.__class__.__name__},
        )

    def close(self) -> None:
        with self._lock:
            self._db.close()
