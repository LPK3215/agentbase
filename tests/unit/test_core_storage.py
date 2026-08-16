"""Unit tests for the storage backend abstraction."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

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


class TestSQLiteBackendExtras:
    """Cover last_insert_id, health_check, reconnect, transaction."""

    def test_last_insert_id(self, tmp_path):
        backend = SQLiteBackend(db_path=tmp_path / "test.db")
        backend.executescript(
            "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT);"
        )
        backend.execute("INSERT INTO items (name) VALUES (%s)", ("first",))
        backend.commit()
        assert backend.last_insert_id() >= 1
        backend.close()

    def test_health_check_returns_true(self, tmp_path):
        backend = SQLiteBackend(db_path=tmp_path / "test.db")
        assert backend.health_check() is True
        backend.close()

    def test_reconnect(self, tmp_path):
        backend = SQLiteBackend(db_path=tmp_path / "test.db")
        backend.executescript("CREATE TABLE IF NOT EXISTS t (id INTEGER);")
        backend.commit()
        backend.reconnect()
        # Should still work after reconnect
        backend.execute("INSERT INTO t (id) VALUES (%s)", (42,))
        backend.commit()
        row = backend.fetchone("SELECT * FROM t WHERE id = %s", (42,))
        assert row is not None
        backend.close()

    def test_transaction_success(self, tmp_path):
        backend = SQLiteBackend(db_path=tmp_path / "test.db")
        backend.executescript("CREATE TABLE IF NOT EXISTS t (id INTEGER);")
        with backend.transaction():
            backend.execute("INSERT INTO t (id) VALUES (%s)", (1,))
        # Should be committed
        row = backend.fetchone("SELECT * FROM t WHERE id = %s", (1,))
        assert row is not None
        backend.close()

    def test_transaction_rollback_on_error(self, tmp_path):
        backend = SQLiteBackend(db_path=tmp_path / "test.db")
        backend.executescript("CREATE TABLE IF NOT EXISTS t (id INTEGER);")
        with pytest.raises(RuntimeError, match="boom"):
            with backend.transaction():
                backend.execute("INSERT INTO t (id) VALUES (%s)", (1,))
                raise RuntimeError("boom")
        # Should be rolled back
        row = backend.fetchone("SELECT * FROM t WHERE id = %s", (1,))
        assert row is None
        backend.close()

    def test_execute_with_none_params(self, tmp_path):
        backend = SQLiteBackend(db_path=tmp_path / "test.db")
        backend.executescript("CREATE TABLE IF NOT EXISTS t (id INTEGER);")
        backend.execute("INSERT INTO t (id) VALUES (99)")
        backend.commit()
        row = backend.fetchone("SELECT * FROM t WHERE id = 99")
        assert row is not None
        backend.close()

    def test_fetchall_empty(self, tmp_path):
        backend = SQLiteBackend(db_path=tmp_path / "test.db")
        backend.executescript("CREATE TABLE IF NOT EXISTS t (id INTEGER);")
        rows = backend.fetchall("SELECT * FROM t")
        assert rows == []
        backend.close()


class TestMySQLBackendUnit:
    """Unit tests for MySQLBackend SQL conversion and DSN parsing (no real DB)."""

    def test_convert_sql_autoincrement(self):
        from agentbase.core.storage import MySQLBackend
        converted = MySQLBackend._convert_sql(
            "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT)"
        )
        assert "INTEGER AUTO_INCREMENT PRIMARY KEY" in converted

    def test_convert_sql_on_conflict(self):
        from agentbase.core.storage import MySQLBackend
        converted = MySQLBackend._convert_sql(
            "INSERT INTO t (id) VALUES (%s) ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name"
        )
        assert "ON DUPLICATE KEY UPDATE" in converted
        assert "VALUES(name)" in converted
        assert "EXCLUDED" not in converted

    def test_invalid_dsn_raises_value_error(self):
        """Invalid DSN should raise ValueError, not ImportError."""
        import sys
        from types import ModuleType
        from unittest.mock import MagicMock

        fake_pymysql = ModuleType("pymysql")
        fake_pymysql.cursors = ModuleType("pymysql.cursors")
        fake_pymysql.cursors.DictCursor = MagicMock()
        fake_pymysql.connect = MagicMock()

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            from agentbase.core.storage import MySQLBackend
            with pytest.raises(ValueError, match="Invalid MySQL DSN"):
                MySQLBackend(dsn="not-a-valid-dsn")

    def test_dsn_without_port(self):
        """DSN without port should default to 3306."""
        import sys
        from types import ModuleType
        from unittest.mock import MagicMock

        fake_pymysql = ModuleType("pymysql")
        fake_pymysql.cursors = ModuleType("pymysql.cursors")
        fake_pymysql.cursors.DictCursor = MagicMock()
        mock_connect = MagicMock()
        fake_pymysql.connect = mock_connect

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            from agentbase.core.storage import MySQLBackend
            MySQLBackend(dsn="mysql://user:pass@host/db")
            call_kwargs = mock_connect.call_args
            assert call_kwargs.kwargs["port"] == 3306


class TestPostgresBackendUnit:
    """Unit tests for PostgresBackend SQL conversion."""

    def test_convert_sql_autoincrement(self):
        from agentbase.core.storage import PostgresBackend
        converted = PostgresBackend._convert_sql(
            "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT)"
        )
        assert "SERIAL PRIMARY KEY" in converted

    def test_convert_sql_no_change_when_no_autoincrement(self):
        from agentbase.core.storage import PostgresBackend
        sql = "SELECT * FROM users WHERE id = %s"
        assert PostgresBackend._convert_sql(sql) == sql


# ---------------------------------------------------------------------------
# Supplementary tests for missing coverage — PostgresBackend with mocked psycopg
# ---------------------------------------------------------------------------


class TestPostgresBackendMocked:
    """Test PostgresBackend methods with mocked psycopg connection."""

    def _make_mock_pg(self):
        import sys
        from types import ModuleType

        fake_pg = ModuleType("psycopg")
        fake_pg.rows = ModuleType("psycopg.rows")
        fake_pg.rows.dict_row = MagicMock()
        fake_connect = MagicMock()
        fake_conn = MagicMock()
        fake_connect.return_value = fake_conn
        fake_pg.connect = fake_connect
        return fake_pg, fake_conn

    def test_init_and_connect(self):
        import sys
        from types import ModuleType

        fake_pg, fake_conn = self._make_mock_pg()
        with patch.dict(sys.modules, {"psycopg": fake_pg, "psycopg.rows": fake_pg.rows}):
            from agentbase.core.storage import PostgresBackend

            backend = PostgresBackend(dsn="postgresql://user:pass@host/db")
            assert backend._dsn == "postgresql://user:pass@host/db"
            backend.close()

    def test_executescript(self):
        import sys
        from types import ModuleType

        fake_pg, fake_conn = self._make_mock_pg()
        fake_cursor = MagicMock()
        fake_conn.cursor.return_value.__enter__ = MagicMock(return_value=fake_cursor)
        fake_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch.dict(sys.modules, {"psycopg": fake_pg, "psycopg.rows": fake_pg.rows}):
            from agentbase.core.storage import PostgresBackend

            backend = PostgresBackend(dsn="postgresql://user:pass@host/db")
            backend.executescript("CREATE TABLE t (id SERIAL PRIMARY KEY);")
            fake_conn.commit.assert_called()

    def test_execute(self):
        import sys
        from types import ModuleType

        fake_pg, fake_conn = self._make_mock_pg()
        fake_cursor = MagicMock()
        fake_conn.cursor.return_value = fake_cursor

        with patch.dict(sys.modules, {"psycopg": fake_pg, "psycopg.rows": fake_pg.rows}):
            from agentbase.core.storage import PostgresBackend

            backend = PostgresBackend(dsn="postgresql://user:pass@host/db")
            result = backend.execute("SELECT %s", (1,))
            assert result is not None

    def test_fetchone(self):
        import sys
        from types import ModuleType

        fake_pg, fake_conn = self._make_mock_pg()
        fake_cursor = MagicMock()
        fake_cursor.fetchone.return_value = {"id": 1}
        fake_conn.cursor.return_value.__enter__ = MagicMock(return_value=fake_cursor)
        fake_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch.dict(sys.modules, {"psycopg": fake_pg, "psycopg.rows": fake_pg.rows}):
            from agentbase.core.storage import PostgresBackend

            backend = PostgresBackend(dsn="postgresql://user:pass@host/db")
            row = backend.fetchone("SELECT * FROM t WHERE id = %s", (1,))
            assert row["id"] == 1

    def test_fetchall(self):
        import sys
        from types import ModuleType

        fake_pg, fake_conn = self._make_mock_pg()
        fake_cursor = MagicMock()
        fake_cursor.fetchall.return_value = [{"id": 1}, {"id": 2}]
        fake_conn.cursor.return_value.__enter__ = MagicMock(return_value=fake_cursor)
        fake_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch.dict(sys.modules, {"psycopg": fake_pg, "psycopg.rows": fake_pg.rows}):
            from agentbase.core.storage import PostgresBackend

            backend = PostgresBackend(dsn="postgresql://user:pass@host/db")
            rows = backend.fetchall("SELECT * FROM t")
            assert len(rows) == 2

    def test_commit(self):
        import sys
        from types import ModuleType

        fake_pg, fake_conn = self._make_mock_pg()
        with patch.dict(sys.modules, {"psycopg": fake_pg, "psycopg.rows": fake_pg.rows}):
            from agentbase.core.storage import PostgresBackend

            backend = PostgresBackend(dsn="postgresql://user:pass@host/db")
            backend.commit()
            fake_conn.commit.assert_called()

    def test_last_insert_id(self):
        import sys
        from types import ModuleType

        fake_pg, fake_conn = self._make_mock_pg()
        fake_cursor = MagicMock()
        fake_cursor.fetchone.return_value = {"id": 42}
        fake_conn.execute.return_value = fake_cursor

        with patch.dict(sys.modules, {"psycopg": fake_pg, "psycopg.rows": fake_pg.rows}):
            from agentbase.core.storage import PostgresBackend

            backend = PostgresBackend(dsn="postgresql://user:pass@host/db")
            assert backend.last_insert_id() == 42

    def test_health_check_true(self):
        import sys
        from types import ModuleType

        fake_pg, fake_conn = self._make_mock_pg()
        fake_cursor = MagicMock()
        fake_conn.cursor.return_value.__enter__ = MagicMock(return_value=fake_cursor)
        fake_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch.dict(sys.modules, {"psycopg": fake_pg, "psycopg.rows": fake_pg.rows}):
            from agentbase.core.storage import PostgresBackend

            backend = PostgresBackend(dsn="postgresql://user:pass@host/db")
            assert backend.health_check() is True

    def test_health_check_false_on_error(self):
        import sys
        from types import ModuleType

        fake_pg, fake_conn = self._make_mock_pg()
        fake_conn.cursor.side_effect = Exception("connection lost")

        with patch.dict(sys.modules, {"psycopg": fake_pg, "psycopg.rows": fake_pg.rows}):
            from agentbase.core.storage import PostgresBackend

            backend = PostgresBackend(dsn="postgresql://user:pass@host/db")
            assert backend.health_check() is False

    def test_reconnect(self):
        import sys
        from types import ModuleType

        fake_pg, fake_conn = self._make_mock_pg()
        with patch.dict(sys.modules, {"psycopg": fake_pg, "psycopg.rows": fake_pg.rows}):
            from agentbase.core.storage import PostgresBackend

            backend = PostgresBackend(dsn="postgresql://user:pass@host/db")
            backend.reconnect()
            # Should have closed old conn and created new
            fake_conn.close.assert_called()

    def test_transaction_success(self):
        import sys
        from types import ModuleType

        fake_pg, fake_conn = self._make_mock_pg()
        with patch.dict(sys.modules, {"psycopg": fake_pg, "psycopg.rows": fake_pg.rows}):
            from agentbase.core.storage import PostgresBackend

            backend = PostgresBackend(dsn="postgresql://user:pass@host/db")
            with backend.transaction():
                pass
            fake_conn.commit.assert_called()

    def test_transaction_rollback(self):
        import sys
        from types import ModuleType

        fake_pg, fake_conn = self._make_mock_pg()
        with patch.dict(sys.modules, {"psycopg": fake_pg, "psycopg.rows": fake_pg.rows}):
            from agentbase.core.storage import PostgresBackend

            backend = PostgresBackend(dsn="postgresql://user:pass@host/db")
            with pytest.raises(RuntimeError, match="fail"):
                with backend.transaction():
                    raise RuntimeError("fail")
            fake_conn.rollback.assert_called()

    def test_init_import_error(self):
        """When psycopg is not installed, raises ImportError."""
        import sys

        with patch.dict(sys.modules, {"psycopg": None}):
            from agentbase.core.storage import PostgresBackend
            with pytest.raises(ImportError, match="psycopg"):
                PostgresBackend(dsn="postgresql://user:pass@host/db")


# ---------------------------------------------------------------------------
# Supplementary tests for missing coverage — MySQLBackend methods
# ---------------------------------------------------------------------------


class TestMySQLBackendMocked:
    """Test MySQLBackend methods with mocked pymysql connection."""

    def _make_mock_mysql(self):
        import sys
        from types import ModuleType

        fake_pymysql = ModuleType("pymysql")
        fake_pymysql.cursors = ModuleType("pymysql.cursors")
        fake_pymysql.cursors.DictCursor = MagicMock()
        mock_connect = MagicMock()
        fake_pymysql.connect = mock_connect
        return fake_pymysql, mock_connect

    def test_init_with_port(self):
        import sys

        fake_pymysql, mock_connect = self._make_mock_mysql()
        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            from agentbase.core.storage import MySQLBackend

            backend = MySQLBackend(dsn="mysql://user:pass@host:3307/db")
            call_kwargs = mock_connect.call_args.kwargs
            assert call_kwargs["host"] == "host"
            assert call_kwargs["port"] == 3307
            assert call_kwargs["user"] == "user"
            assert call_kwargs["database"] == "db"

    def test_executescript(self):
        import sys

        fake_pymysql, mock_connect = self._make_mock_mysql()
        fake_conn = mock_connect.return_value
        fake_cursor = MagicMock()
        fake_conn.cursor.return_value.__enter__ = MagicMock(return_value=fake_cursor)
        fake_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            from agentbase.core.storage import MySQLBackend

            backend = MySQLBackend(dsn="mysql://user:pass@host:3306/db")
            backend.executescript("CREATE TABLE t (id INTEGER); CREATE TABLE u (id INTEGER);")
            fake_conn.commit.assert_called()

    def test_execute(self):
        import sys

        fake_pymysql, mock_connect = self._make_mock_mysql()
        fake_conn = mock_connect.return_value
        fake_cursor = MagicMock()
        fake_conn.cursor.return_value = fake_cursor

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            from agentbase.core.storage import MySQLBackend

            backend = MySQLBackend(dsn="mysql://user:pass@host:3306/db")
            result = backend.execute("SELECT %s", (1,))
            assert result is not None

    def test_fetchone(self):
        import sys

        fake_pymysql, mock_connect = self._make_mock_mysql()
        fake_conn = mock_connect.return_value
        fake_cursor = MagicMock()
        fake_cursor.fetchone.return_value = {"id": 1}
        fake_conn.cursor.return_value.__enter__ = MagicMock(return_value=fake_cursor)
        fake_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            from agentbase.core.storage import MySQLBackend

            backend = MySQLBackend(dsn="mysql://user:pass@host:3306/db")
            row = backend.fetchone("SELECT * FROM t WHERE id = %s", (1,))
            assert row["id"] == 1

    def test_fetchall(self):
        import sys

        fake_pymysql, mock_connect = self._make_mock_mysql()
        fake_conn = mock_connect.return_value
        fake_cursor = MagicMock()
        fake_cursor.fetchall.return_value = [{"id": 1}, {"id": 2}]
        fake_conn.cursor.return_value.__enter__ = MagicMock(return_value=fake_cursor)
        fake_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            from agentbase.core.storage import MySQLBackend

            backend = MySQLBackend(dsn="mysql://user:pass@host:3306/db")
            rows = backend.fetchall("SELECT * FROM t")
            assert len(rows) == 2

    def test_commit(self):
        import sys

        fake_pymysql, mock_connect = self._make_mock_mysql()
        fake_conn = mock_connect.return_value

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            from agentbase.core.storage import MySQLBackend

            backend = MySQLBackend(dsn="mysql://user:pass@host:3306/db")
            backend.commit()
            fake_conn.commit.assert_called()

    def test_close(self):
        import sys

        fake_pymysql, mock_connect = self._make_mock_mysql()
        fake_conn = mock_connect.return_value

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            from agentbase.core.storage import MySQLBackend

            backend = MySQLBackend(dsn="mysql://user:pass@host:3306/db")
            backend.close()
            fake_conn.close.assert_called()
            assert backend._conn is None

    def test_close_already_closed(self):
        import sys

        fake_pymysql, mock_connect = self._make_mock_mysql()
        fake_conn = mock_connect.return_value
        fake_conn.close.side_effect = Exception("already closed")

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            from agentbase.core.storage import MySQLBackend

            backend = MySQLBackend(dsn="mysql://user:pass@host:3306/db")
            backend.close()  # should not raise
            assert backend._conn is None

    def test_last_insert_id(self):
        import sys

        fake_pymysql, mock_connect = self._make_mock_mysql()
        fake_conn = mock_connect.return_value
        fake_conn.insert_id.return_value = 99

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            from agentbase.core.storage import MySQLBackend

            backend = MySQLBackend(dsn="mysql://user:pass@host:3306/db")
            assert backend.last_insert_id() == 99

    def test_health_check_true(self):
        import sys

        fake_pymysql, mock_connect = self._make_mock_mysql()
        fake_conn = mock_connect.return_value
        fake_cursor = MagicMock()
        fake_conn.cursor.return_value.__enter__ = MagicMock(return_value=fake_cursor)
        fake_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            from agentbase.core.storage import MySQLBackend

            backend = MySQLBackend(dsn="mysql://user:pass@host:3306/db")
            assert backend.health_check() is True

    def test_health_check_false_on_error(self):
        import sys

        fake_pymysql, mock_connect = self._make_mock_mysql()
        fake_conn = mock_connect.return_value
        fake_conn.cursor.side_effect = Exception("connection lost")

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            from agentbase.core.storage import MySQLBackend

            backend = MySQLBackend(dsn="mysql://user:pass@host:3306/db")
            assert backend.health_check() is False

    def test_reconnect(self):
        import sys

        fake_pymysql, mock_connect = self._make_mock_mysql()
        fake_conn = mock_connect.return_value

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            from agentbase.core.storage import MySQLBackend

            backend = MySQLBackend(dsn="mysql://user:pass@host:3306/db")
            backend.reconnect()
            # Should have called close on old connection
            fake_conn.close.assert_called()

    def test_transaction_success(self):
        import sys

        fake_pymysql, mock_connect = self._make_mock_mysql()
        fake_conn = mock_connect.return_value

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            from agentbase.core.storage import MySQLBackend

            backend = MySQLBackend(dsn="mysql://user:pass@host:3306/db")
            with backend.transaction():
                pass
            fake_conn.commit.assert_called()

    def test_transaction_rollback(self):
        import sys

        fake_pymysql, mock_connect = self._make_mock_mysql()
        fake_conn = mock_connect.return_value

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            from agentbase.core.storage import MySQLBackend

            backend = MySQLBackend(dsn="mysql://user:pass@host:3306/db")
            with pytest.raises(RuntimeError, match="fail"):
                with backend.transaction():
                    raise RuntimeError("fail")
            fake_conn.rollback.assert_called()

    def test_init_import_error(self):
        """When pymysql is not installed, raises ImportError."""
        import sys

        with patch.dict(sys.modules, {"pymysql": None}):
            from agentbase.core.storage import MySQLBackend
            with pytest.raises(ImportError, match="pymysql"):
                MySQLBackend(dsn="mysql://user:pass@host:3306/db")


# ---------------------------------------------------------------------------
# Supplementary tests — create_storage branches
# ---------------------------------------------------------------------------


class TestCreateStorageBranches:
    def test_create_mysql(self):
        import sys
        from types import ModuleType

        fake_pymysql = ModuleType("pymysql")
        fake_pymysql.cursors = ModuleType("pymysql.cursors")
        fake_pymysql.cursors.DictCursor = MagicMock()
        fake_pymysql.connect = MagicMock()

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            from agentbase.core.storage import create_storage, MySQLBackend

            backend = create_storage(dsn="mysql://user:pass@host:3306/db")
            assert isinstance(backend, MySQLBackend)

    def test_create_postgres(self):
        import sys
        from types import ModuleType

        fake_pg = ModuleType("psycopg")
        fake_pg.rows = ModuleType("psycopg.rows")
        fake_pg.rows.dict_row = MagicMock()
        fake_pg.connect = MagicMock(return_value=MagicMock())

        with patch.dict(sys.modules, {"psycopg": fake_pg, "psycopg.rows": fake_pg.rows}):
            from agentbase.core.storage import create_storage, PostgresBackend

            backend = create_storage(dsn="postgresql://user:pass@host/db")
            assert isinstance(backend, PostgresBackend)


# ---------------------------------------------------------------------------
# Supplementary tests — SQLite health_check error path
# ---------------------------------------------------------------------------


class TestSQLiteBackendHealthCheckError:
    def test_health_check_false_on_error(self, tmp_path):
        from agentbase.core.storage import SQLiteBackend

        backend = SQLiteBackend(db_path=tmp_path / "test.db")
        # Mock the connection to raise on execute
        backend._conn = MagicMock()
        backend._conn.execute.side_effect = Exception("dead connection")
        assert backend.health_check() is False

    def test_reconnect_close_error(self, tmp_path):
        from agentbase.core.storage import SQLiteBackend
        from unittest.mock import MagicMock, patch

        backend = SQLiteBackend(db_path=tmp_path / "test.db")
        # Replace connection with a mock that raises on close
        mock_conn = MagicMock()
        mock_conn.close.side_effect = Exception("close failed")
        backend._conn = mock_conn
        # Mock sqlite3.connect to return a working connection
        import sqlite3
        with patch.object(sqlite3, "connect", return_value=sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)):
            # Should not raise even though close fails
            backend.reconnect()
        # New connection should work
        backend.execute("SELECT 1")
        backend.close()
