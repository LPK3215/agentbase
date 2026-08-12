"""Unit tests for the storage backend abstraction."""
from __future__ import annotations

import pytest

from agentbase.core.storage import (
    SQLiteBackend,
    StorageBackend,
    create_storage,
)


class TestSQLiteBackend:
    def test_is_storage_backend(self, tmp_path):
        backend = SQLiteBackend(db_path=tmp_path / "test.db")
        assert isinstance(backend, StorageBackend)
        backend.close()

    def test_create_table_and_query(self, tmp_path):
        backend = SQLiteBackend(db_path=tmp_path / "test.db")
        backend.executescript(
            "CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, name TEXT);"
        )
        backend.execute(
            "INSERT INTO test (name) VALUES (%s)", ("alice",)
        )
        backend.commit()

        row = backend.fetchone("SELECT * FROM test WHERE name = %s", ("alice",))
        assert row is not None
        assert row["name"] == "alice"

        rows = backend.fetchall("SELECT * FROM test")
        assert len(rows) == 1
        backend.close()

    def test_placeholder_conversion(self, tmp_path):
        backend = SQLiteBackend(db_path=tmp_path / "test.db")
        # %s should be converted to ? internally
        assert backend._convert_sql("SELECT %s") == "SELECT ?"
        assert backend._convert_sql("VALUES (%s, %s)") == "VALUES (?, ?)"
        backend.close()

    def test_executescript(self, tmp_path):
        backend = SQLiteBackend(db_path=tmp_path / "test.db")
        backend.executescript(
            """
            CREATE TABLE IF NOT EXISTS a (id INTEGER);
            CREATE TABLE IF NOT EXISTS b (id INTEGER);
            """
        )
        backend.commit()
        # Tables should exist
        row = backend.fetchone("SELECT name FROM sqlite_master WHERE type='table' AND name='a'")
        assert row is not None
        backend.close()


class TestCreateStorage:
    def test_create_with_db_path(self, tmp_path):
        backend = create_storage(db_path=tmp_path / "test.db")
        assert isinstance(backend, SQLiteBackend)
        backend.close()

    def test_create_in_memory(self):
        backend = create_storage()
        assert isinstance(backend, SQLiteBackend)
        backend.close()

    def test_create_with_dsn(self, tmp_path):
        """Test that create_storage returns PostgresBackend when DSN is given."""
        from agentbase.core.storage import PostgresBackend, create_storage

        # Check if psycopg is available first
        try:
            import psycopg  # noqa: F401
            psycopg_available = True
        except ImportError:
            psycopg_available = False

        if psycopg_available:
            # Don't actually connect — just verify the class would be selected
            # by checking that create_storage doesn't raise ImportError at the
            # import level. We can't connect to a real server in tests.
            import unittest.mock as mock
            with mock.patch("psycopg.connect") as mock_connect:
                mock_conn = mock.MagicMock()
                mock_conn.row_factory = None
                mock_connect.return_value = mock_conn
                backend = create_storage(dsn="postgresql://test:test@localhost/test")
                assert isinstance(backend, PostgresBackend)
                backend.close()
        else:
            # psycopg not installed — should raise ImportError
            with pytest.raises(ImportError, match="psycopg"):
                PostgresBackend(dsn="postgresql://test:test@localhost/test")


class TestStorageIntegrationWithMemory:
    """Verify that MemoryManager works correctly with the storage abstraction."""

    def test_memory_with_storage_backend(self, tmp_path):
        from agentbase.core.memory import MemoryManager
        from agentbase.core.storage import SQLiteBackend

        backend = SQLiteBackend(db_path=tmp_path / "mem.db")
        mgr = MemoryManager(backend=backend)
        mgr.save(agent_name="default", key="k1", content="hello")
        mem = mgr.get(agent_name="default", key="k1")
        assert mem.content == "hello"
        mgr.close()


class TestStorageIntegrationWithKnowledge:
    """Verify that KnowledgeBase works correctly with the storage abstraction."""

    def test_kb_with_storage_backend(self, tmp_path):
        from agentbase.core.knowledge import KnowledgeBase
        from agentbase.core.storage import SQLiteBackend

        backend = SQLiteBackend(db_path=tmp_path / "kb.db")
        kb = KnowledgeBase(backend=backend)
        doc = kb.add_document(source="test.md", title="Test", content="Hello world")
        assert doc.title == "Test"
        results = kb.search("Hello")
        assert len(results) > 0
        kb.close()
