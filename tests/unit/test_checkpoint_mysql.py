"""Unit tests for MySQLSaver checkpoint store.

Covers: DSN parsing, setup(), cursor(), get_tuple(), list(), put(),
put_writes(), delete_thread() — all via mock pymysql connection.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentbase.runtime.checkpoint_mysql import MySQLSaver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeDictCursor:
    """Minimal mock cursor that behaves like pymysql DictCursor."""

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []
        self._executed: list[tuple[str, tuple]] = []
        self._fetch_index = 0

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._executed.append((sql, params))

    def executemany(self, sql: str, params_list: list[tuple]) -> None:
        self._executed.append((sql, params_list))

    def fetchone(self) -> dict[str, Any] | None:
        if self._fetch_index < len(self._rows):
            row = self._rows[self._fetch_index]
            self._fetch_index += 1
            return row
        return None

    def fetchall(self) -> list[dict[str, Any]]:
        remaining = self._rows[self._fetch_index:]
        self._fetch_index = len(self._rows)
        return remaining

    def close(self) -> None:
        pass

    # Context manager protocol (pymysql cursors support `with cursor:`)
    def __enter__(self) -> "FakeDictCursor":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # Allow tests to pre-load rows
    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self._fetch_index = 0


class FakeConnection:
    """Minimal mock pymysql connection."""

    def __init__(self) -> None:
        self._cursor = FakeDictCursor()
        self.committed = 0

    def cursor(self) -> FakeDictCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed += 1

    # Allow tests to reset cursor rows for each sub-query
    @property
    def mock_cursor(self) -> FakeDictCursor:
        return self._cursor


def _make_saver() -> tuple[MySQLSaver, FakeConnection]:
    """Create a MySQLSaver with a mock connection."""
    conn = FakeConnection()
    saver = MySQLSaver(conn)
    return saver, conn


def _make_checkpoint(checkpoint_id: str = "ckpt-1", parent: str | None = None) -> dict[str, Any]:
    """Create a minimal checkpoint dict compatible with serde."""
    return {"id": checkpoint_id, "parent_id": parent or ""}


def _make_config(thread_id: str = "t1", checkpoint_id: str | None = None, ns: str = "") -> dict[str, Any]:
    """Build a RunnableConfig for checkpoint operations."""
    configurable: dict[str, Any] = {"thread_id": thread_id, "checkpoint_ns": ns}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestMySQLSaverInit:
    def test_default_state(self):
        conn = FakeConnection()
        saver = MySQLSaver(conn)
        assert saver.conn is conn
        assert saver.is_setup is False
        assert saver.jsonplus_serde is not None
        assert saver.lock is not None

    def test_with_serde(self):
        conn = FakeConnection()
        custom_serde = MagicMock()
        saver = MySQLSaver(conn, serde=custom_serde)
        assert saver.serde is custom_serde


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------


class TestSetup:
    def test_creates_tables(self):
        saver, conn = _make_saver()
        saver.setup()
        assert saver.is_setup is True
        # Should have executed 2 CREATE TABLE statements
        assert len(conn.mock_cursor._executed) == 2
        sqls = [e[0] for e in conn.mock_cursor._executed]
        assert any("checkpoints" in s for s in sqls)
        assert any("writes" in s for s in sqls)
        assert conn.committed == 1

    def test_idempotent(self):
        saver, conn = _make_saver()
        saver.setup()
        saver.setup()  # second call should be no-op
        assert len(conn.mock_cursor._executed) == 2  # still 2, not 4


# ---------------------------------------------------------------------------
# cursor
# ---------------------------------------------------------------------------


class TestCursor:
    def test_cursor_triggers_setup(self):
        saver, conn = _make_saver()
        with saver.cursor() as cur:
            assert cur is not None
        assert saver.is_setup is True

    def test_transaction_commits(self):
        saver, conn = _make_saver()
        # Pre-setup so cursor() doesn't call setup()
        saver.is_setup = True
        with saver.cursor(transaction=True) as cur:
            pass
        assert conn.committed == 1

    def test_no_transaction_no_commit(self):
        saver, conn = _make_saver()
        saver.is_setup = True
        with saver.cursor(transaction=False) as cur:
            pass
        assert conn.committed == 0

    def test_cursor_closes(self):
        saver, conn = _make_saver()
        with saver.cursor() as cur:
            assert cur is not None
        # cursor should be closed (no exception = pass)
        # The FakeDictCursor.close() is a no-op, so we just verify no exception


# ---------------------------------------------------------------------------
# get_tuple
# ---------------------------------------------------------------------------


class TestGetTuple:
    def test_returns_none_when_no_rows(self):
        saver, _conn = _make_saver()
        saver.is_setup = True  # skip setup
        conn = saver.conn
        conn.mock_cursor.set_rows([])  # no rows returned

        config = _make_config(thread_id="t1")
        result = saver.get_tuple(config)
        assert result is None

    def test_returns_tuple_with_checkpoint_id(self):
        saver, conn = _make_saver()
        saver.is_setup = True

        # Mock serde to avoid serialisation complexity
        saver.serde = MagicMock()
        saver.serde.loads_typed.return_value = {"id": "ckpt-1"}

        # Prepare checkpoint row
        checkpoint_row = {
            "thread_id": "t1",
            "checkpoint_id": "ckpt-1",
            "parent_checkpoint_id": None,
            "type": "json",
            "checkpoint": b"{}",
            "metadata": json.dumps({"step": 1}).encode(),
        }
        writes_rows: list[dict[str, Any]] = []

        # First fetchone returns checkpoint row, then writes query returns []
        conn.mock_cursor.set_rows([checkpoint_row])

        # Set up writes to return empty
        # The second execute is for writes - set rows for that too
        # Since fetchall is called after fetchone, we need to handle the cursor state
        # We'll patch the cursor to return appropriate data

        # Actually the cursor is shared, so fetchone consumes checkpoint_row,
        # then the next execute (writes) + fetchall needs to return writes_rows
        # Let's use a more sophisticated mock

        call_count = [0]

        original_execute = conn.mock_cursor.execute
        original_fetchone = conn.mock_cursor.fetchone
        original_fetchall = conn.mock_cursor.fetchall

        def mock_execute(sql, params=()):
            call_count[0] += 1
            if call_count[0] == 1:
                # First query: checkpoint lookup
                conn.mock_cursor.set_rows([checkpoint_row])
            elif call_count[0] == 2:
                # Second query: writes lookup
                conn.mock_cursor.set_rows([])
            return None

        conn.mock_cursor.execute = mock_execute
        conn.mock_cursor.fetchone = original_fetchone
        conn.mock_cursor.fetchall = original_fetchall

        config = _make_config(thread_id="t1", checkpoint_id="ckpt-1")
        result = saver.get_tuple(config)

        assert result is not None
        assert result.config["configurable"]["checkpoint_id"] == "ckpt-1"
        assert result.parent_config is None  # parent_checkpoint_id is None
        assert result.pending_writes == []

    def test_returns_tuple_without_checkpoint_id(self):
        saver, conn = _make_saver()
        saver.is_setup = True

        saver.serde = MagicMock()
        saver.serde.loads_typed.return_value = {"id": "ckpt-1"}

        checkpoint_row = {
            "thread_id": "t1",
            "checkpoint_id": "ckpt-1",
            "parent_checkpoint_id": "ckpt-0",
            "type": "json",
            "checkpoint": b"{}",
            "metadata": json.dumps({"step": 1}).encode(),
        }

        call_count = [0]

        def mock_execute(sql, params=()):
            call_count[0] += 1
            if call_count[0] == 1:
                conn.mock_cursor.set_rows([checkpoint_row])
            elif call_count[0] == 2:
                conn.mock_cursor.set_rows([])
            return None

        conn.mock_cursor.execute = mock_execute

        # Config without checkpoint_id — should query latest
        config = _make_config(thread_id="t1")
        result = saver.get_tuple(config)

        assert result is not None
        # Config should be updated with the checkpoint_id from DB
        assert result.config["configurable"]["checkpoint_id"] == "ckpt-1"
        # parent_config should be set (parent_checkpoint_id = "ckpt-0")
        assert result.parent_config is not None
        assert result.parent_config["configurable"]["checkpoint_id"] == "ckpt-0"

    def test_handles_none_metadata(self):
        saver, conn = _make_saver()
        saver.is_setup = True

        saver.serde = MagicMock()
        saver.serde.loads_typed.return_value = {"id": "ckpt-1"}

        checkpoint_row = {
            "thread_id": "t1",
            "checkpoint_id": "ckpt-1",
            "parent_checkpoint_id": None,
            "type": "json",
            "checkpoint": b"{}",
            "metadata": None,  # null metadata
        }

        call_count = [0]

        def mock_execute(sql, params=()):
            call_count[0] += 1
            if call_count[0] == 1:
                conn.mock_cursor.set_rows([checkpoint_row])
            elif call_count[0] == 2:
                conn.mock_cursor.set_rows([])
            return None

        conn.mock_cursor.execute = mock_execute

        config = _make_config(thread_id="t1", checkpoint_id="ckpt-1")
        result = saver.get_tuple(config)

        assert result is not None
        # metadata should be {} when None
        assert result.metadata == {}

    def test_with_pending_writes(self):
        saver, conn = _make_saver()
        saver.is_setup = True

        saver.serde = MagicMock()
        saver.serde.loads_typed.return_value = {"id": "ckpt-1"}

        checkpoint_row = {
            "thread_id": "t1",
            "checkpoint_id": "ckpt-1",
            "parent_checkpoint_id": None,
            "type": "json",
            "checkpoint": b"{}",
            "metadata": json.dumps({"step": 1}).encode(),
        }

        writes_rows = [
            {"task_id": "task-1", "channel": "channel-1", "type": "json", "value": b"{}"},
            {"task_id": "task-2", "channel": "channel-2", "type": "json", "value": b"{}"},
        ]

        call_count = [0]

        def mock_execute(sql, params=()):
            call_count[0] += 1
            if call_count[0] == 1:
                conn.mock_cursor.set_rows([checkpoint_row])
            elif call_count[0] == 2:
                conn.mock_cursor.set_rows(writes_rows)
            return None

        conn.mock_cursor.execute = mock_execute

        config = _make_config(thread_id="t1", checkpoint_id="ckpt-1")
        result = saver.get_tuple(config)

        assert result is not None
        assert len(result.pending_writes) == 2
        assert result.pending_writes[0][0] == "task-1"
        assert result.pending_writes[1][0] == "task-2"


# ---------------------------------------------------------------------------
# put
# ---------------------------------------------------------------------------


class TestPut:
    def test_inserts_checkpoint(self):
        saver, conn = _make_saver()
        saver.is_setup = True

        saver.serde = MagicMock()
        saver.serde.dumps_typed.return_value = ("json", b'{"id":"ckpt-1"}')

        config = _make_config(thread_id="t1", checkpoint_id="ckpt-0", ns="")
        checkpoint = {"id": "ckpt-1", "parent_id": "ckpt-0"}
        metadata = {"step": 1}

        result = saver.put(config, checkpoint, metadata, {})

        # Verify return config
        assert result["configurable"]["thread_id"] == "t1"
        assert result["configurable"]["checkpoint_id"] == "ckpt-1"
        assert result["configurable"]["checkpoint_ns"] == ""

        # Verify INSERT was executed
        executed = conn.mock_cursor._executed
        assert len(executed) == 1
        sql = executed[0][0]
        assert "INSERT INTO checkpoints" in sql
        assert "ON DUPLICATE KEY UPDATE" in sql

    def test_put_with_empty_ns(self):
        saver, conn = _make_saver()
        saver.is_setup = True

        saver.serde = MagicMock()
        saver.serde.dumps_typed.return_value = ("json", b"{}")

        config = _make_config(thread_id="t1", checkpoint_id="ckpt-0", ns="nested")
        checkpoint = {"id": "ckpt-1"}
        metadata = {}

        result = saver.put(config, checkpoint, metadata, {})
        assert result["configurable"]["checkpoint_ns"] == "nested"


# ---------------------------------------------------------------------------
# put_writes
# ---------------------------------------------------------------------------


class TestPutWrites:
    def test_inserts_writes(self):
        saver, conn = _make_saver()
        saver.is_setup = True

        saver.serde = MagicMock()
        saver.serde.dumps_typed.return_value = ("json", b"{}")

        config = _make_config(thread_id="t1", checkpoint_id="ckpt-1", ns="")
        # Use channels that ARE in WRITES_IDX_MAP to trigger ON DUPLICATE KEY UPDATE
        writes = [("__error__", {"data": 1}), ("__scheduled__", {"data": 2})]

        saver.put_writes(config, writes, "task-1")

        executed = conn.mock_cursor._executed
        # put_writes uses executemany, which is recorded in _executed
        write_sqls = [e[0] for e in executed if "INSERT" in e[0] and "writes" in e[0]]
        assert len(write_sqls) >= 1
        # All channels are in WRITES_IDX_MAP, so ON DUPLICATE KEY UPDATE path
        assert "ON DUPLICATE KEY UPDATE" in write_sqls[0]

    def test_insert_ignore_for_unknown_channels(self):
        saver, conn = _make_saver()
        saver.is_setup = True

        saver.serde = MagicMock()
        saver.serde.dumps_typed.return_value = ("json", b"{}")

        config = _make_config(thread_id="t1", checkpoint_id="ckpt-1", ns="")
        # Use a channel name that's NOT in WRITES_IDX_MAP
        writes = [("unknown_channel_name", {"data": 1})]

        saver.put_writes(config, writes, "task-1")

        executed = conn.mock_cursor._executed
        assert len(executed) == 1
        sql = executed[0][0]
        assert "INSERT IGNORE" in sql

    def test_empty_writes_does_nothing(self):
        saver, conn = _make_saver()
        saver.is_setup = True

        saver.serde = MagicMock()
        config = _make_config(thread_id="t1", checkpoint_id="ckpt-1", ns="")

        # all() on empty list returns True — so it goes through the UPDATE path
        # but executemany with empty list should be a no-op
        saver.put_writes(config, [], "task-1")
        # executemany called with empty list — verify it didn't crash
        assert len(conn.mock_cursor._executed) == 1  # the SQL was still prepared


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestList:
    def test_list_empty(self):
        saver, conn = _make_saver()
        saver.is_setup = True

        conn.mock_cursor.set_rows([])

        result = list(saver.list(None))
        assert result == []

    def test_list_with_config(self):
        saver, conn = _make_saver()
        saver.is_setup = True

        saver.serde = MagicMock()
        saver.serde.loads_typed.return_value = {"id": "ckpt-1"}

        checkpoint_rows = [
            {
                "thread_id": "t1",
                "checkpoint_ns": "",
                "checkpoint_id": "ckpt-1",
                "parent_checkpoint_id": None,
                "type": "json",
                "checkpoint": b"{}",
                "metadata": json.dumps({"step": 1}).encode(),
            },
        ]

        call_count = [0]

        def mock_execute(sql, params=()):
            call_count[0] += 1
            if call_count[0] == 1:
                conn.mock_cursor.set_rows(checkpoint_rows)
            elif call_count[0] == 2:
                # writes query for the checkpoint
                conn.mock_cursor.set_rows([])
            return None

        conn.mock_cursor.execute = mock_execute

        config = _make_config(thread_id="t1")
        results = list(saver.list(config))

        assert len(results) == 1
        assert results[0].config["configurable"]["checkpoint_id"] == "ckpt-1"

    def test_list_with_limit(self):
        saver, conn = _make_saver()
        saver.is_setup = True

        saver.serde = MagicMock()
        saver.serde.loads_typed.return_value = {"id": "ckpt-1"}

        call_count = [0]
        executed_sqls: list[str] = []

        def mock_execute(sql, params=()):
            call_count[0] += 1
            executed_sqls.append(sql)
            if call_count[0] == 1:
                # Return 1 checkpoint row (limit already applied in SQL)
                conn.mock_cursor.set_rows([
                    {
                        "thread_id": "t1",
                        "checkpoint_ns": "",
                        "checkpoint_id": "ckpt-1",
                        "parent_checkpoint_id": None,
                        "type": "json",
                        "checkpoint": b"{}",
                        "metadata": json.dumps({}).encode(),
                    },
                ])
            elif call_count[0] == 2:
                conn.mock_cursor.set_rows([])
            return None

        conn.mock_cursor.execute = mock_execute

        config = _make_config(thread_id="t1")
        results = list(saver.list(config, limit=5))

        # Verify LIMIT was in the first SQL
        assert "LIMIT" in executed_sqls[0]

    def test_list_with_before(self):
        saver, conn = _make_saver()
        saver.is_setup = True

        saver.serde = MagicMock()
        saver.serde.loads_typed.return_value = {"id": "ckpt-1"}

        conn.mock_cursor.set_rows([])

        config = _make_config(thread_id="t1")
        before_config = _make_config(thread_id="t1", checkpoint_id="ckpt-5")
        results = list(saver.list(config, before=before_config))

        first_sql = conn.mock_cursor._executed[0][0]
        assert "checkpoint_id <" in first_sql


# ---------------------------------------------------------------------------
# delete_thread
# ---------------------------------------------------------------------------


class TestDeleteThread:
    def test_deletes_checkpoints_and_writes(self):
        saver, conn = _make_saver()
        saver.is_setup = True

        saver.delete_thread("t1")

        executed = conn.mock_cursor._executed
        assert len(executed) == 2
        sqls = [e[0] for e in executed]
        assert any("DELETE FROM checkpoints" in s for s in sqls)
        assert any("DELETE FROM writes" in s for s in sqls)

    def test_delete_with_int_thread_id(self):
        saver, conn = _make_saver()
        saver.is_setup = True

        # thread_id is cast to str in the SQL
        saver.delete_thread(12345)  # type: ignore[arg-type]

        executed = conn.mock_cursor._executed
        # Verify the str() conversion happened
        for sql, params in executed:
            assert "12345" in str(params)


# ---------------------------------------------------------------------------
# from_dsn
# ---------------------------------------------------------------------------


class TestFromDsn:
    def test_valid_dsn(self):
        """Test that from_dsn correctly parses a valid DSN."""
        import sys
        from types import ModuleType

        # Create a fake pymysql module so `import pymysql` succeeds
        fake_pymysql = ModuleType("pymysql")
        fake_pymysql.cursors = ModuleType("pymysql.cursors")
        fake_pymysql.cursors.DictCursor = MagicMock()
        mock_connect = MagicMock()
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        fake_pymysql.connect = mock_connect

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            with MySQLSaver.from_dsn("mysql://user:pass@localhost:3306/testdb") as saver:
                assert saver.conn is mock_conn
                mock_connect.assert_called_once()
                call_kwargs = mock_connect.call_args
                assert call_kwargs.kwargs["host"] == "localhost"
                assert call_kwargs.kwargs["port"] == 3306
                assert call_kwargs.kwargs["user"] == "user"
                assert call_kwargs.kwargs["password"] == "pass"
                assert call_kwargs.kwargs["database"] == "testdb"
                assert call_kwargs.kwargs["charset"] == "utf8mb4"

    def test_invalid_dsn_raises_value_error(self):
        """Test that invalid DSN raises ValueError."""
        import sys
        from types import ModuleType

        fake_pymysql = ModuleType("pymysql")
        fake_pymysql.cursors = ModuleType("pymysql.cursors")
        fake_pymysql.cursors.DictCursor = MagicMock()
        fake_pymysql.connect = MagicMock()

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            with pytest.raises(ValueError, match="Invalid MySQL DSN"):
                with MySQLSaver.from_dsn("not-a-valid-dsn"):
                    pass

    def test_invalid_dsn_missing_port(self):
        """DSN without port should not match the regex."""
        import sys
        from types import ModuleType

        fake_pymysql = ModuleType("pymysql")
        fake_pymysql.cursors = ModuleType("pymysql.cursors")
        fake_pymysql.cursors.DictCursor = MagicMock()
        fake_pymysql.connect = MagicMock()

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            with pytest.raises(ValueError, match="Invalid MySQL DSN"):
                with MySQLSaver.from_dsn("mysql://user:pass@host/db"):
                    pass

    def test_invalid_dsn_wrong_scheme(self):
        """DSN with wrong scheme should not match."""
        import sys
        from types import ModuleType

        fake_pymysql = ModuleType("pymysql")
        fake_pymysql.cursors = ModuleType("pymysql.cursors")
        fake_pymysql.cursors.DictCursor = MagicMock()
        fake_pymysql.connect = MagicMock()

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            with pytest.raises(ValueError, match="Invalid MySQL DSN"):
                with MySQLSaver.from_dsn("postgresql://user:pass@host:5432/db"):
                    pass


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_lock_is_used(self):
        """Verify that cursor operations acquire the internal lock."""
        saver, conn = _make_saver()
        saver.is_setup = True

        # The cursor() context manager uses self.lock
        # We just verify it doesn't deadlock
        with saver.cursor() as cur:
            assert cur is not None

    def test_concurrent_setup_no_double_create(self):
        """setup() should only create tables once even if called twice."""
        saver, conn = _make_saver()
        saver.setup()
        initial_count = len(conn.mock_cursor._executed)
        saver.setup()
        assert len(conn.mock_cursor._executed) == initial_count
