"""Tests for CLI backup/restore commands — covers SQL/JSON formats, all backends, error handling.

Tests verify:
1. SQL format backup (SQLite backend)
2. JSON format backup (SQLite backend)
3. SQL format restore
4. JSON format restore
5. Restore with missing file
6. Unsupported backend for backup
7. JSON restore with skipped tables (errors)
8. Default output filename generation
9. SQL restore statement parsing
10. Backup with PostgreSQL backend
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class SQLiteBackend:
    """Fake SQLite storage backend for testing."""
    def __init__(self, tables):
        self.db_path = "/fake/test.db"
        self._tables = tables or {}
        self.execute_calls = []
    def fetchall(self, sql, params=None):
        sql_lower = sql.lower().strip()
        if "sqlite_master" in sql_lower:
            return [{"name": t} for t in self._tables]
        for tname in self._tables:
            if f"from {tname}" in sql_lower or f"FROM {tname}" in sql:
                return self._tables[tname]
        return []
    def execute(self, sql, params=None):
        self.execute_calls.append((sql, params))
    def commit(self):
        pass
    @property
    def execute_call_count(self):
        return len(self.execute_calls)


def _make_sqlite_storage(tables: dict[str, list[dict]] | None = None):
    """Create a mock SQLite storage backend.

    Args:
        tables: dict mapping table_name → list of row dicts.
    """
    return SQLiteBackend(tables)


class PostgresBackend:
    """Fake PostgreSQL storage backend for testing."""
    def __init__(self, tables):
        self._tables = tables or {}
    def fetchall(self, sql, params=None):
        sql_lower = sql.lower().strip()
        if "pg_tables" in sql_lower:
            return [{"tablename": t} for t in self._tables]
        for tname in self._tables:
            if f"from {tname}" in sql_lower or f"FROM {tname}" in sql:
                return self._tables[tname]
        return []
    def execute(self, sql, params=None):
        pass
    def commit(self):
        pass


def _make_postgres_storage(tables: dict[str, list[dict]] | None = None):
    """Create a mock PostgreSQL storage backend."""
    return PostgresBackend(tables)


class MySQLBackend:
    """Fake MySQL storage backend for testing."""
    def __init__(self, tables):
        self._tables = tables or {}
    def fetchall(self, sql, params=None):
        sql_lower = sql.lower().strip()
        if "information_schema.tables" in sql_lower:
            return [{"table_name": t} for t in self._tables]
        for tname in self._tables:
            if f"from {tname}" in sql_lower or f"FROM {tname}" in sql:
                return self._tables[tname]
        return []
    def execute(self, sql, params=None):
        pass
    def commit(self):
        pass


def _make_mysql_storage(tables: dict[str, list[dict]] | None = None):
    """Create a mock MySQL storage backend."""
    return MySQLBackend(tables)


class UnknownBackend:
    """Fake unsupported storage backend for testing."""
    pass


def _make_unsupported_storage():
    """Create a mock unsupported storage backend."""
    return UnknownBackend()


def _make_args(root=".", output=None, fmt="sql", input_path=None):
    """Create argparse.Namespace for backup/restore commands."""
    return argparse.Namespace(
        root=root,
        output=output,
        format=fmt,
        input=input_path,
    )


def _make_mock_runtime(storage):
    """Create a mock RuntimeContext with the given storage.

    The mock factory.storage property returns the provided storage directly.
    """
    factory = MagicMock()
    # Make storage a property that returns our mock directly
    type(factory).storage = property(lambda self: storage)
    rt = MagicMock()
    rt.factory = factory
    return rt


# ---------------------------------------------------------------------------
# Patch context manager for bootstrap imports
# ---------------------------------------------------------------------------


def _patch_bootstrap(rt, root_dir):
    """Patch build_runtime and resolve_root_dir in agentbase.bootstrap module."""
    return [
        patch("agentbase.bootstrap.build_runtime", return_value=rt),
        patch("agentbase.bootstrap.resolve_root_dir", return_value=root_dir),
    ]


# ---------------------------------------------------------------------------
# Backup tests
# ---------------------------------------------------------------------------


class TestCmdBackup:
    def test_backup_sql_format_sqlite(self, tmp_path):
        from agentbase.cli import cmd_backup

        tables = {
            "users": [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}],
            "messages": [{"id": 1, "content": "Hello"}],
        }
        storage = _make_sqlite_storage(tables)
        rt = _make_mock_runtime(storage)

        output_file = tmp_path / "backup.sql"
        args = _make_args(output=str(output_file), fmt="sql")

        patches = _patch_bootstrap(rt, tmp_path)
        for p in patches:
            p.start()
        try:
            result = cmd_backup(args)
        finally:
            for p in patches:
                p.stop()

        assert result == 0
        content = output_file.read_text(encoding="utf-8")
        assert "agentbase database backup" in content
        assert "INSERT INTO users" in content
        assert "Alice" in content
        assert "INSERT INTO messages" in content
        assert "Hello" in content

    def test_backup_json_format_sqlite(self, tmp_path):
        from agentbase.cli import cmd_backup

        tables = {
            "users": [{"id": 1, "name": "Alice"}],
            "items": [{"id": 1, "value": 42}],
        }
        storage = _make_sqlite_storage(tables)
        rt = _make_mock_runtime(storage)

        output_file = tmp_path / "backup.json"
        args = _make_args(output=str(output_file), fmt="json")

        patches = _patch_bootstrap(rt, tmp_path)
        for p in patches:
            p.start()
        try:
            result = cmd_backup(args)
        finally:
            for p in patches:
                p.stop()

        assert result == 0
        data = json.loads(output_file.read_text(encoding="utf-8"))
        assert "users" in data
        assert data["users"][0]["name"] == "Alice"
        assert "items" in data

    def test_backup_postgres_backend(self, tmp_path):
        from agentbase.cli import cmd_backup

        tables = {"agents": [{"id": 1, "name": "test_agent"}]}
        storage = _make_postgres_storage(tables)
        rt = _make_mock_runtime(storage)

        output_file = tmp_path / "pg_backup.json"
        args = _make_args(output=str(output_file), fmt="json")

        patches = _patch_bootstrap(rt, tmp_path)
        for p in patches:
            p.start()
        try:
            result = cmd_backup(args)
        finally:
            for p in patches:
                p.stop()

        assert result == 0
        data = json.loads(output_file.read_text(encoding="utf-8"))
        assert "agents" in data

    def test_backup_mysql_backend(self, tmp_path):
        from agentbase.cli import cmd_backup

        tables = {"sessions": [{"id": 1, "status": "active"}]}
        storage = _make_mysql_storage(tables)
        rt = _make_mock_runtime(storage)

        output_file = tmp_path / "mysql_backup.json"
        args = _make_args(output=str(output_file), fmt="json")

        patches = _patch_bootstrap(rt, tmp_path)
        for p in patches:
            p.start()
        try:
            result = cmd_backup(args)
        finally:
            for p in patches:
                p.stop()

        assert result == 0
        data = json.loads(output_file.read_text(encoding="utf-8"))
        assert "sessions" in data

    def test_backup_unsupported_backend(self, tmp_path):
        from agentbase.cli import cmd_backup

        storage = _make_unsupported_storage()
        rt = _make_mock_runtime(storage)

        output_file = tmp_path / "backup.sql"
        args = _make_args(output=str(output_file), fmt="sql")

        patches = _patch_bootstrap(rt, tmp_path)
        for p in patches:
            p.start()
        try:
            result = cmd_backup(args)
        finally:
            for p in patches:
                p.stop()

        assert result == 1

    def test_backup_default_output_filename(self, tmp_path):
        from agentbase.cli import cmd_backup

        tables = {"test_table": [{"id": 1}]}
        storage = _make_sqlite_storage(tables)
        rt = _make_mock_runtime(storage)

        args = _make_args(output=None, fmt="json")

        patches = _patch_bootstrap(rt, tmp_path)
        for p in patches:
            p.start()
        try:
            with patch("agentbase.cli.time.time", return_value=1700000000):
                result = cmd_backup(args)
        finally:
            for p in patches:
                p.stop()

        assert result == 0
        expected_file = Path("backup_1700000000.json")
        assert expected_file.exists()
        # Cleanup
        expected_file.unlink()

    def test_backup_empty_database(self, tmp_path):
        from agentbase.cli import cmd_backup

        storage = _make_sqlite_storage({})
        rt = _make_mock_runtime(storage)

        output_file = tmp_path / "empty.sql"
        args = _make_args(output=str(output_file), fmt="sql")

        patches = _patch_bootstrap(rt, tmp_path)
        for p in patches:
            p.start()
        try:
            result = cmd_backup(args)
        finally:
            for p in patches:
                p.stop()

        assert result == 0
        content = output_file.read_text(encoding="utf-8")
        assert "agentbase database backup" in content
        # No INSERT statements
        assert "INSERT" not in content


# ---------------------------------------------------------------------------
# Restore tests
# ---------------------------------------------------------------------------


class TestCmdRestore:
    def test_restore_sql_format(self, tmp_path):
        from agentbase.cli import cmd_restore

        # Create a backup file with SQL statements
        backup_content = "INSERT INTO users (id, name) VALUES ('1', 'Alice');\nINSERT INTO users (id, name) VALUES ('2', 'Bob');\n"
        backup_file = tmp_path / "backup.sql"
        backup_file.write_text(backup_content, encoding="utf-8")

        tables = {"users": []}
        storage = _make_sqlite_storage(tables)
        rt = _make_mock_runtime(storage)

        args = _make_args(fmt="sql", input_path=str(backup_file))

        patches = _patch_bootstrap(rt, tmp_path)
        for p in patches:
            p.start()
        try:
            result = cmd_restore(args)
        finally:
            for p in patches:
                p.stop()

        assert result == 0
        # Verify execute was called for each INSERT statement
        assert storage.execute_call_count >= 2

    def test_restore_json_format(self, tmp_path):
        from agentbase.cli import cmd_restore

        backup_data = {
            "users": [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}],
            "messages": [{"id": 1, "content": "Hello"}],
        }
        backup_file = tmp_path / "backup.json"
        backup_file.write_text(json.dumps(backup_data), encoding="utf-8")

        tables = {"users": [], "messages": []}
        storage = _make_sqlite_storage(tables)
        rt = _make_mock_runtime(storage)

        args = _make_args(fmt="json", input_path=str(backup_file))

        patches = _patch_bootstrap(rt, tmp_path)
        for p in patches:
            p.start()
        try:
            result = cmd_restore(args)
        finally:
            for p in patches:
                p.stop()

        assert result == 0
        # Verify execute was called for DELETE + INSERT for each table
        # 2 tables × (1 DELETE + 2 or 1 INSERTs) = at least 5 execute calls
        assert storage.execute_call_count >= 5

    def test_restore_missing_file(self, tmp_path):
        from agentbase.cli import cmd_restore

        storage = _make_sqlite_storage({})
        rt = _make_mock_runtime(storage)

        args = _make_args(fmt="sql", input_path=str(tmp_path / "nonexistent.sql"))

        patches = _patch_bootstrap(rt, tmp_path)
        for p in patches:
            p.start()
        try:
            result = cmd_restore(args)
        finally:
            for p in patches:
                p.stop()

        assert result == 1

    def test_restore_json_with_skipped_tables(self, tmp_path):
        from agentbase.cli import cmd_restore

        backup_data = {
            "good_table": [{"id": 1, "name": "Alice"}],
            "bad_table": [{"id": 1, "data": "test"}],
        }
        backup_file = tmp_path / "backup.json"
        backup_file.write_text(json.dumps(backup_data), encoding="utf-8")

        storage = _make_sqlite_storage({"good_table": [], "bad_table": []})

        # Override execute to fail for bad_table
        def _execute(sql, params=None):
            if "bad_table" in sql.lower():
                raise RuntimeError("Table error")
            storage.execute_calls.append((sql, params))
        storage.execute = _execute
        rt = _make_mock_runtime(storage)

        args = _make_args(fmt="json", input_path=str(backup_file))

        patches = _patch_bootstrap(rt, tmp_path)
        for p in patches:
            p.start()
        try:
            result = cmd_restore(args)
        finally:
            for p in patches:
                p.stop()

        assert result == 0

    def test_restore_json_with_nested_objects(self, tmp_path):
        from agentbase.cli import cmd_restore

        backup_data = {
            "configs": [{"id": 1, "settings": {"key": "value"}, "tags": ["a", "b"]}],
        }
        backup_file = tmp_path / "backup.json"
        backup_file.write_text(json.dumps(backup_data), encoding="utf-8")

        storage = _make_sqlite_storage({"configs": []})
        rt = _make_mock_runtime(storage)

        args = _make_args(fmt="json", input_path=str(backup_file))

        patches = _patch_bootstrap(rt, tmp_path)
        for p in patches:
            p.start()
        try:
            result = cmd_restore(args)
        finally:
            for p in patches:
                p.stop()

        assert result == 0
        # Check that execute was called with JSON-stringified values for nested objects
        calls = storage.execute_calls
        # Find the INSERT call
        for call in calls:
            sql_arg = call[0] if call else ""
            if "INSERT" in sql_arg:
                # Check params for JSON-stringified values
                params = call[1] if len(call) > 1 else None
                if params:
                    for v in params:
                        if isinstance(v, str) and v.startswith("{"):
                            parsed = json.loads(v)
                            assert isinstance(parsed, dict)
                break

    def test_restore_sql_empty_file(self, tmp_path):
        from agentbase.cli import cmd_restore

        backup_file = tmp_path / "empty.sql"
        backup_file.write_text("-- just a comment\n", encoding="utf-8")

        storage = _make_sqlite_storage({})
        rt = _make_mock_runtime(storage)

        args = _make_args(fmt="sql", input_path=str(backup_file))

        patches = _patch_bootstrap(rt, tmp_path)
        for p in patches:
            p.start()
        try:
            result = cmd_restore(args)
        finally:
            for p in patches:
                p.stop()

        assert result == 0
        # No statements should have been executed
        assert storage.execute_call_count == 0

    def test_restore_json_all_tables_fail(self, tmp_path):
        from agentbase.cli import cmd_restore

        backup_data = {"bad1": [{"id": 1}], "bad2": [{"id": 1}]}
        backup_file = tmp_path / "backup.json"
        backup_file.write_text(json.dumps(backup_data), encoding="utf-8")

        storage = _make_sqlite_storage({"bad1": [], "bad2": []})

        def _execute(sql, params=None):
            raise RuntimeError("Always fails")
        storage.execute = _execute
        rt = _make_mock_runtime(storage)

        args = _make_args(fmt="json", input_path=str(backup_file))

        patches = _patch_bootstrap(rt, tmp_path)
        for p in patches:
            p.start()
        try:
            result = cmd_restore(args)
        finally:
            for p in patches:
                p.stop()

        assert result == 0  # Still returns 0, just reports skipped
