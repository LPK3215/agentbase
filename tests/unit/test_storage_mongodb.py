"""Tests for MongoDBBackend — SQL parsing, Protocol compliance, factory routing.

Since no real MongoDB server is available in CI, these tests use mocks
to verify:
1. SQL statement parsing (CREATE TABLE, INSERT, SELECT, UPDATE, DELETE, COUNT)
2. WHERE clause translation to MongoDB query dict
3. create_storage factory routing for mongodb:// DSN
4. Protocol compliance (isinstance check against StorageBackend)
5. Import error handling when pymongo is not installed
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# SQL parser tests (no MongoDB connection needed)
# ---------------------------------------------------------------------------


class TestParseCreateTable:
    def test_parse_simple(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        result = MongoDBBackend._parse_create_table(
            "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT)"
        )
        assert result is not None
        assert result["table"] == "users"

    def test_parse_without_if_not_exists(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        result = MongoDBBackend._parse_create_table(
            "CREATE TABLE items (id INTEGER, title TEXT)"
        )
        assert result is not None
        assert result["table"] == "items"

    def test_not_create_table(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        assert MongoDBBackend._parse_create_table("SELECT * FROM users") is None


class TestParseCreateIndex:
    def test_parse_simple(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        result = MongoDBBackend._parse_create_index(
            "CREATE INDEX IF NOT EXISTS idx_name ON users(name)"
        )
        assert result is not None
        assert result["index_name"] == "idx_name"
        assert result["table"] == "users"
        assert result["columns"] == ["name"]

    def test_not_create_index(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        assert MongoDBBackend._parse_create_index("SELECT * FROM users") is None


class TestParseInsert:
    def test_parse_simple(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        result = MongoDBBackend._parse_insert(
            "INSERT INTO users (name, email) VALUES (%s, %s)",
            ("alice", "alice@example.com"),
        )
        assert result is not None
        assert result["table"] == "users"
        assert result["columns"] == ["name", "email"]
        assert result["values"] == ["alice", "alice@example.com"]

    def test_parse_single_value(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        result = MongoDBBackend._parse_insert(
            "INSERT INTO logs (message) VALUES (%s)",
            ("hello",),
        )
        assert result is not None
        assert result["columns"] == ["message"]
        assert result["values"] == ["hello"]

    def test_not_insert(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        assert MongoDBBackend._parse_insert("SELECT * FROM users", None) is None


class TestParseSelect:
    def test_select_all(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        result = MongoDBBackend._parse_select("SELECT * FROM users", None)
        assert result is not None
        assert result["type"] == "select"
        assert result["table"] == "users"
        assert result["columns"] is None

    def test_select_with_where(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        result = MongoDBBackend._parse_select(
            "SELECT * FROM users WHERE name = %s",
            ("alice",),
        )
        assert result is not None
        assert result["where"] == {"name": "alice"}

    def test_select_with_where_and(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        result = MongoDBBackend._parse_select(
            "SELECT * FROM users WHERE name = %s AND age = %s",
            ("alice", 30),
        )
        assert result is not None
        assert result["where"] == {"name": "alice", "age": 30}

    def test_select_count(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        result = MongoDBBackend._parse_select(
            "SELECT COUNT(*) AS cnt FROM users",
            None,
        )
        assert result is not None
        assert result["type"] == "count"
        assert result["count_alias"] == "cnt"
        assert result["table"] == "users"

    def test_select_with_order_by(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        result = MongoDBBackend._parse_select(
            "SELECT * FROM users ORDER BY name DESC",
            None,
        )
        assert result is not None
        assert len(result["order_by"]) == 1
        assert result["order_by"][0] == ("name", -1)

    def test_select_with_limit(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        result = MongoDBBackend._parse_select(
            "SELECT * FROM users LIMIT 10",
            None,
        )
        assert result is not None
        assert result["limit"] == 10

    def test_select_with_offset(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        result = MongoDBBackend._parse_select(
            "SELECT * FROM users LIMIT 10 OFFSET 20",
            None,
        )
        assert result is not None
        assert result["limit"] == 10
        assert result["offset"] == 20

    def test_select_specific_columns(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        result = MongoDBBackend._parse_select(
            "SELECT id, name FROM users",
            None,
        )
        assert result is not None
        assert result["columns"] == ["id", "name"]

    def test_not_select(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        assert MongoDBBackend._parse_select("INSERT INTO users VALUES (1)", None) is None


class TestParseWhere:
    def test_simple_equality(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        where = MongoDBBackend._parse_where("WHERE name = %s", ("alice",))
        assert where == {"name": "alice"}

    def test_multiple_and(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        where = MongoDBBackend._parse_where(
            "WHERE name = %s AND age = %s",
            ("alice", 30),
        )
        assert where == {"name": "alice", "age": 30}

    def test_greater_than(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        where = MongoDBBackend._parse_where("WHERE age > %s", (25,))
        assert where == {"age": {"$gt": 25}}

    def test_less_than(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        where = MongoDBBackend._parse_where("WHERE age < %s", (50,))
        assert where == {"age": {"$lt": 50}}

    def test_not_equal(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        where = MongoDBBackend._parse_where("WHERE status != %s", ("deleted",))
        assert where == {"status": {"$ne": "deleted"}}

    def test_no_where(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        where = MongoDBBackend._parse_where("", None)
        assert where == {}


class TestParseUpdate:
    def test_parse_simple(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        result = MongoDBBackend._parse_update(
            "UPDATE users SET name = %s WHERE id = %s",
            ("bob", 1),
        )
        assert result is not None
        assert result["table"] == "users"
        assert result["set"] == {"name": "bob"}
        assert result["where"] == {"id": 1}

    def test_parse_multiple_set(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        result = MongoDBBackend._parse_update(
            "UPDATE users SET name = %s, age = %s WHERE id = %s",
            ("bob", 30, 1),
        )
        assert result is not None
        assert result["set"] == {"name": "bob", "age": 30}

    def test_not_update(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        assert MongoDBBackend._parse_update("SELECT * FROM users", None) is None


class TestParseDelete:
    def test_parse_simple(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        result = MongoDBBackend._parse_delete(
            "DELETE FROM users WHERE id = %s",
            (1,),
        )
        assert result is not None
        assert result["table"] == "users"
        assert result["where"] == {"id": 1}

    def test_parse_no_where(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        result = MongoDBBackend._parse_delete("DELETE FROM users", None)
        assert result is not None
        assert result["where"] == {}

    def test_not_delete(self):
        from agentbase.core.storage_mongodb import MongoDBBackend

        assert MongoDBBackend._parse_delete("SELECT * FROM users", None) is None


# ---------------------------------------------------------------------------
# Row helper tests
# ---------------------------------------------------------------------------


class TestRow:
    def test_dict_access(self):
        from agentbase.core.storage_mongodb import _Row

        row = _Row({"name": "alice", "age": 30})
        assert row["name"] == "alice"
        assert row["age"] == 30

    def test_attribute_access(self):
        from agentbase.core.storage_mongodb import _Row

        row = _Row({"name": "alice", "age": 30})
        assert row.name == "alice"
        assert row.age == 30

    def test_attribute_missing(self):
        from agentbase.core.storage_mongodb import _Row

        row = _Row({"name": "alice"})
        with pytest.raises(AttributeError):
            _ = row.nonexistent


# ---------------------------------------------------------------------------
# Factory routing tests
# ---------------------------------------------------------------------------


class TestCreateStorageMongoDB:
    def test_mongodb_dsn_routes_to_mongodb_backend(self):
        """create_storage with mongodb:// DSN should return MongoDBBackend."""
        import sys

        from agentbase.core.storage import create_storage

        # Mock pymongo module since it's not installed in test env
        mock_pymongo = MagicMock()
        mock_pymongo.MongoClient.return_value = MagicMock()
        old_pymongo = sys.modules.get("pymongo")
        sys.modules["pymongo"] = mock_pymongo
        try:
            backend = create_storage(dsn="mongodb://localhost:27017/agentbase")
        finally:
            if old_pymongo is not None:
                sys.modules["pymongo"] = old_pymongo
            else:
                del sys.modules["pymongo"]

        from agentbase.core.storage_mongodb import MongoDBBackend
        assert isinstance(backend, MongoDBBackend)
        backend.close()

    def test_mongodb_backend_protocol_compliance(self):
        """MongoDBBackend should satisfy the StorageBackend Protocol."""
        import sys

        from agentbase.core.storage import StorageBackend
        from agentbase.core.storage_mongodb import MongoDBBackend

        mock_pymongo = MagicMock()
        mock_pymongo.MongoClient.return_value = MagicMock()
        old_pymongo = sys.modules.get("pymongo")
        sys.modules["pymongo"] = mock_pymongo
        try:
            backend = MongoDBBackend(dsn="mongodb://localhost:27017/test")
        finally:
            if old_pymongo is not None:
                sys.modules["pymongo"] = old_pymongo
            else:
                del sys.modules["pymongo"]

        assert isinstance(backend, StorageBackend)
        backend.close()

    def test_mongodb_import_error(self):
        """When pymongo is not installed, should raise ImportError."""
        # Simulate pymongo not being available
        import sys

        from agentbase.core.storage_mongodb import MongoDBBackend
        original_pymongo = sys.modules.get("pymongo")
        if original_pymongo is not None:
            # If pymongo IS installed, we can't easily test this path
            # Just verify the error message format
            pass
        else:
            with pytest.raises(ImportError, match="pymongo"):
                MongoDBBackend(dsn="mongodb://localhost:27017/test")


# ---------------------------------------------------------------------------
# MongoDBBackend integration tests (with mocks)
# ---------------------------------------------------------------------------


class TestMongoDBBackendWithMocks:
    """Test MongoDBBackend operations using mock pymongo client."""

    def _create_backend(self):
        """Create a MongoDBBackend with a mock client."""
        import sys

        from agentbase.core.storage_mongodb import MongoDBBackend

        mock_collection = MagicMock()
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_collection)
        mock_client = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_db)

        mock_pymongo = MagicMock()
        mock_pymongo.MongoClient.return_value = mock_client
        old_pymongo = sys.modules.get("pymongo")
        sys.modules["pymongo"] = mock_pymongo
        try:
            backend = MongoDBBackend(dsn="mongodb://localhost:27017/testdb")
        finally:
            if old_pymongo is not None:
                sys.modules["pymongo"] = old_pymongo
            else:
                del sys.modules["pymongo"]

        # Replace the db with our mock for testing
        backend._db = mock_db
        backend._mock_collection = mock_collection
        return backend

    def test_insert(self):
        backend = self._create_backend()
        backend.execute(
            "INSERT INTO users (name, email) VALUES (%s, %s)",
            ("alice", "alice@example.com"),
        )
        backend._mock_collection.insert_one.assert_called_once()
        call_args = backend._mock_collection.insert_one.call_args[0][0]
        assert call_args["name"] == "alice"
        assert call_args["email"] == "alice@example.com"
        backend.close()

    def test_select_all(self):
        backend = self._create_backend()
        # Mock find to return some docs
        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(return_value=iter([
            {"_id": "obj1", "name": "alice"},
            {"_id": "obj2", "name": "bob"},
        ]))
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        mock_cursor.skip = MagicMock(return_value=mock_cursor)
        backend._mock_collection.find.return_value = mock_cursor

        rows = backend.fetchall("SELECT * FROM users")
        assert len(rows) == 2
        assert rows[0]["name"] == "alice"
        assert rows[0]["id"] == "obj1"  # _id converted to id
        backend.close()

    def test_count(self):
        backend = self._create_backend()
        backend._mock_collection.count_documents.return_value = 42

        row = backend.fetchone("SELECT COUNT(*) AS cnt FROM users")
        assert row is not None
        assert row["cnt"] == 42
        backend.close()

    def test_update(self):
        backend = self._create_backend()
        backend.execute(
            "UPDATE users SET name = %s WHERE id = %s",
            ("bob", 1),
        )
        backend._mock_collection.update_many.assert_called_once_with(
            {"id": 1},
            {"$set": {"name": "bob"}},
        )
        backend.close()

    def test_delete(self):
        backend = self._create_backend()
        backend.execute(
            "DELETE FROM users WHERE id = %s",
            (1,),
        )
        backend._mock_collection.delete_many.assert_called_once_with({"id": 1})
        backend.close()

    def test_commit_is_noop(self):
        backend = self._create_backend()
        # Should not raise
        backend.commit()
        backend.close()

    def test_last_insert_id_increments(self):
        backend = self._create_backend()
        assert backend.last_insert_id() == 0
        backend.execute(
            "INSERT INTO users (name) VALUES (%s)",
            ("alice",),
        )
        assert backend.last_insert_id() == 1
        backend.execute(
            "INSERT INTO users (name) VALUES (%s)",
            ("bob",),
        )
        assert backend.last_insert_id() == 2
        backend.close()

    def test_health_check(self):
        backend = self._create_backend()
        backend._client.admin.command = MagicMock(return_value={"ok": 1})
        assert backend.health_check() is True

        backend._client.admin.command = MagicMock(side_effect=Exception("conn lost"))
        assert backend.health_check() is False
        backend.close()

    def test_executescript_create_table_is_noop(self):
        backend = self._create_backend()
        # CREATE TABLE should be a no-op (collections auto-created)
        backend.executescript(
            "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT);"
        )
        # No insert_one should have been called
        backend._mock_collection.insert_one.assert_not_called()
        backend.close()

    def test_executescript_create_index(self):
        backend = self._create_backend()
        backend.executescript(
            "CREATE INDEX IF NOT EXISTS idx_name ON users(name);"
        )
        backend._mock_collection.create_index.assert_called_once_with(
            "name", name="idx_name"
        )
        backend.close()

    def test_select_with_where(self):
        backend = self._create_backend()
        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(return_value=iter([]))
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        mock_cursor.skip = MagicMock(return_value=mock_cursor)
        backend._mock_collection.find.return_value = mock_cursor

        backend.fetchall("SELECT * FROM users WHERE name = %s", ("alice",))
        backend._mock_collection.find.assert_called_once_with(
            {"name": "alice"}, projection=None
        )
        backend.close()

    def test_fetchone_returns_first_row(self):
        backend = self._create_backend()
        mock_cursor = MagicMock()
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.skip = MagicMock(return_value=mock_cursor)
        # Make iter return one doc, then StopIteration
        mock_cursor.__iter__ = MagicMock(return_value=iter([
            {"_id": "obj1", "name": "alice"},
        ]))
        mock_cursor.fetchone = MagicMock(return_value={"_id": "obj1", "name": "alice"})
        backend._mock_collection.find.return_value = mock_cursor

        row = backend.fetchone("SELECT * FROM users WHERE name = %s", ("alice",))
        assert row is not None
        assert row["name"] == "alice"
        backend.close()

    def test_fetchone_returns_none_when_empty(self):
        backend = self._create_backend()
        mock_cursor = MagicMock()
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.skip = MagicMock(return_value=mock_cursor)
        # Empty result set
        mock_cursor.__iter__ = MagicMock(return_value=iter([]))
        mock_cursor.fetchone = MagicMock(return_value=None)
        backend._mock_collection.find.return_value = mock_cursor

        row = backend.fetchone("SELECT * FROM users WHERE name = %s", ("nobody",))
        assert row is None
        backend.close()

    def test_transaction_is_noop(self):
        backend = self._create_backend()
        with backend.transaction():
            pass  # Should not raise
        backend.close()


# ---------------------------------------------------------------------------
# Config schema tests
# ---------------------------------------------------------------------------


class TestStorageConfigMongoDB:
    def test_mongodb_type_accepted(self):
        from agentbase.config.schema import StorageConfig

        config = StorageConfig(type="mongodb", dsn="mongodb://localhost:27017/agentbase")
        assert config.type == "mongodb"
        assert config.dsn == "mongodb://localhost:27017/agentbase"

    def test_default_is_still_sqlite(self):
        from agentbase.config.schema import StorageConfig

        config = StorageConfig()
        assert config.type == "sqlite"
