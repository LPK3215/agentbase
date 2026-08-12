"""Persistent memory management — pluggable storage backend (SQLite / PostgreSQL).

Each memory record has a ``key``, free-form ``content``, optional ``tags``
and ``metadata``.  Memories are scoped per ``agent_name`` so multiple agents
sharing the same database don't collide.

Storage backend is chosen automatically:

- ``db_path=Path("data/memory.db")``  →  SQLite (dev / single-user)
- ``dsn="postgresql://..."``          →  PostgreSQL (prod / multi-user)

Usage::

    # SQLite (dev)
    mgr = MemoryManager(db_path=Path("data/memory.db"))

    # PostgreSQL (prod)
    mgr = MemoryManager(dsn="postgresql://user:pass@localhost/agentbase")

    mgr.save(agent_name="default", key="user_pref", content="prefers concise answers", tags=["preference"])
    record = mgr.get(agent_name="default", key="user_pref")
    results = mgr.search(agent_name="default", query="concise")
    mgr.delete(agent_name="default", key="user_pref")
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentbase.core.storage import StorageBackend, create_storage


@dataclass
class Memory:
    """A single memory record."""

    id: int | None
    agent_name: str
    key: str
    content: str
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_name": self.agent_name,
            "key": self.key,
            "content": self.content,
            "tags": self.tags,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_memory(row: Any) -> Memory:
    """Convert a DB row (sqlite3.Row or dict) to a Memory record."""
    def _get(key: str) -> Any:
        if hasattr(row, "__getitem__"):
            try:
                return row[key]
            except (KeyError, IndexError):
                return None
        return getattr(row, key, None)

    return Memory(
        id=_get("id"),
        agent_name=_get("agent_name"),
        key=_get("key"),
        content=_get("content"),
        tags=json.loads(_get("tags") or "[]"),
        metadata=json.loads(_get("metadata") or "{}"),
        created_at=_get("created_at") or "",
        updated_at=_get("updated_at") or "",
    )


class MemoryManager:
    """Persistent memory store with CRUD and text search.

    Uses SQLite by default; PostgreSQL when a DSN is provided.

    Thread safety:
        All write operations (``save``/``delete``/``batch_save``/``close``)
        are serialized with a re-entrant lock, so multiple threads can share
        one manager without corrupting the database.  Read operations
        (``get``/``list``/``search``/``count``) are lock-free for
        concurrency, matching the pattern used by :class:`KnowledgeBase`.
    """

    def __init__(
        self,
        *,
        db_path: Path | None = None,
        dsn: str | None = None,
        backend: StorageBackend | None = None,
    ) -> None:
        self._lock = threading.RLock()
        if backend is not None:
            self._db = backend
        else:
            self._db = create_storage(db_path=db_path, dsn=dsn)
        self._init_db()

    def _init_db(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_name  TEXT NOT NULL DEFAULT 'default',
                "key"       TEXT NOT NULL,
                content     TEXT NOT NULL,
                tags        TEXT DEFAULT '[]',
                metadata    TEXT DEFAULT '{}',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                UNIQUE(agent_name, "key")
            );
            CREATE INDEX IF NOT EXISTS idx_mem_agent ON memories(agent_name);
            CREATE INDEX IF NOT EXISTS idx_mem_tags  ON memories(tags);
            """
        )
        self._db.commit()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def _upsert(
        self,
        *,
        agent_name: str,
        key: str,
        content: str,
        tags: list[str],
        metadata: dict[str, Any],
        now: str,
    ) -> None:
        """Insert-or-update a single memory row (no commit)."""
        tags_json = json.dumps(tags, ensure_ascii=False)
        meta_json = json.dumps(metadata, ensure_ascii=False)
        # Use ON CONFLICT upsert — works on SQLite, PostgreSQL and MySQL
        self._db.execute(
            """
            INSERT INTO memories (agent_name, "key", content, tags, metadata, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (agent_name, "key") DO UPDATE SET
                content   = EXCLUDED.content,
                tags      = EXCLUDED.tags,
                metadata  = EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at
            """,
            (agent_name, key, content, tags_json, meta_json, now, now),
        )

    def save(
        self,
        *,
        agent_name: str = "default",
        key: str,
        content: str,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Memory:
        """Create or update a memory by ``(agent_name, key)``."""
        with self._lock:
            self._upsert(
                agent_name=agent_name,
                key=key,
                content=content,
                tags=tags or [],
                metadata=metadata or {},
                now=_now(),
            )
            self._db.commit()
        return self.get(agent_name=agent_name, key=key)

    def batch_save(
        self,
        *,
        agent_name: str = "default",
        entries: list[dict[str, Any]],
    ) -> int:
        """Atomically save many memories in a single transaction.

        Each entry in ``entries`` must be a mapping containing at least
        ``key`` (str) and ``content`` (str); ``tags`` (list[str]) and
        ``metadata`` (dict) are optional.

        All entries are committed together: if any single entry fails to
        validate or write, the whole batch is rolled back and no partial
        writes survive.  Returns the number of memories saved.

        Raises:
            ValueError: if ``entries`` is not a list or any entry is
                missing ``key``/``content`` or uses a non-str value.
        """
        if not isinstance(entries, list):
            raise ValueError("entries must be a list of dicts")
        if not entries:
            return 0
        normalized: list[tuple[str, str, list[str], dict[str, Any]]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(f"invalid memory entry: expected dict, got {type(entry).__name__}")
            key = entry.get("key")
            content = entry.get("content")
            if not isinstance(key, str) or not key.strip():
                raise ValueError("each memory entry requires a non-empty string 'key'")
            if not isinstance(content, str):
                raise ValueError(f"memory '{key}': 'content' must be a string")
            tags = entry.get("tags")
            metadata = entry.get("metadata")
            normalized.append(
                (
                    key,
                    content,
                    [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else [],
                    metadata if isinstance(metadata, dict) else {},
                )
            )
        now = _now()
        txn = getattr(self._db, "transaction", None)
        with self._lock:
            if txn is not None:
                # SQLite/Postgres/MySQL backends all expose a transaction
                # context manager; a failure anywhere rolls everything back.
                with txn():
                    for key, content, tags, metadata in normalized:
                        self._upsert(
                            agent_name=agent_name,
                            key=key,
                            content=content,
                            tags=tags,
                            metadata=metadata,
                            now=now,
                        )
            else:
                # Fallback for custom backends without transaction support:
                # plain sequential upserts, committed in one batch.
                for key, content, tags, metadata in normalized:
                    self._upsert(
                        agent_name=agent_name,
                        key=key,
                        content=content,
                        tags=tags,
                        metadata=metadata,
                        now=now,
                    )
                self._db.commit()
        return len(normalized)

    def get(self, *, agent_name: str = "default", key: str) -> Memory:
        """Retrieve a single memory.  Raises ``KeyError`` if not found."""
        row = self._db.fetchone(
            "SELECT * FROM memories WHERE agent_name = %s AND \"key\" = %s",
            (agent_name, key),
        )
        if row is None:
            raise KeyError(f"Memory not found: agent={agent_name} key={key}")
        return _row_to_memory(row)

    def list(self, *, agent_name: str | None = None, tag: str | None = None) -> list[Memory]:
        """List memories, optionally filtered by agent and/or tag."""
        sql = "SELECT * FROM memories"
        conditions: list[str] = []
        params: list[Any] = []
        if agent_name is not None:
            conditions.append("agent_name = %s")
            params.append(agent_name)
        if tag is not None:
            conditions.append("tags LIKE %s")
            params.append(f'%"{tag}"%')
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY updated_at DESC"
        rows = self._db.fetchall(sql, params)
        return [_row_to_memory(r) for r in rows]

    def search(self, *, agent_name: str | None = None, query: str) -> list[Memory]:
        """Case-insensitive full-text search across ``content`` and ``key``."""
        sql = "SELECT * FROM memories WHERE (content LIKE %s OR \"key\" LIKE %s)"
        params: list[Any] = [f"%{query}%", f"%{query}%"]
        if agent_name is not None:
            sql += " AND agent_name = %s"
            params.append(agent_name)
        sql += " ORDER BY updated_at DESC"
        rows = self._db.fetchall(sql, params)
        return [_row_to_memory(r) for r in rows]

    def delete(self, *, agent_name: str = "default", key: str) -> bool:
        """Delete a memory.  Returns ``True`` if deleted, ``False`` if not found."""
        with self._lock:
            cur = self._db.execute(
                "DELETE FROM memories WHERE agent_name = %s AND \"key\" = %s",
                (agent_name, key),
            )
            self._db.commit()
        return cur.rowcount > 0

    def count(self, *, agent_name: str | None = None) -> int:
        """Return the total number of memories, optionally filtered by agent."""
        if agent_name is not None:
            row = self._db.fetchone(
                'SELECT COUNT(*) AS cnt FROM memories WHERE agent_name = %s',
                (agent_name,),
            )
        else:
            row = self._db.fetchone("SELECT COUNT(*) AS cnt FROM memories")
        if row is None:
            return 0
        # Handle both sqlite3.Row and dict
        try:
            return row["cnt"]
        except (KeyError, IndexError):
            return getattr(row, "cnt", 0)

    def get_or_create(
        self,
        *,
        agent_name: str = "default",
        key: str,
        default_content: str = "",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Memory:
        """Get a memory by key, or create it with defaults if not found."""
        try:
            return self.get(agent_name=agent_name, key=key)
        except KeyError:
            return self.save(
                agent_name=agent_name,
                key=key,
                content=default_content,
                tags=tags,
                metadata=metadata,
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()
