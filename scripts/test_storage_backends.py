"""Comprehensive test for all 3 storage backends: SQLite, PostgreSQL, MySQL.

Run directly:  python scripts/test_storage_backends.py
Or via pytest: pytest scripts/test_storage_backends.py -v
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

import pytest

# ── Helpers ──────────────────────────────────────────────────────────────

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
SKIP = "\033[93mSKIP\033[0m"

results: list[tuple[str, str, str]] = []  # (backend, test_name, status)


def run_test(backend_name: str, test_name: str, fn):
    try:
        fn()
        results.append((backend_name, test_name, PASS))
        print(f"  [{PASS}] {backend_name} :: {test_name}")
    except Exception:
        results.append((backend_name, test_name, FAIL))
        print(f"  [{FAIL}] {backend_name} :: {test_name}")
        traceback.print_exc()


def skip_test(backend_name: str, test_name: str, reason: str):
    results.append((backend_name, test_name, SKIP))
    print(f"  [{SKIP}] {backend_name} :: {test_name}  ({reason})")


# ── Common test suite ────────────────────────────────────────────────────

def _suite_crud(backend, label: str):
    """Run CRUD tests on a backend instance."""

    def test_create_and_insert():
        backend.executescript(
            "CREATE TABLE IF NOT EXISTS items ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  name TEXT NOT NULL,"
            "  value REAL,"
            "  tags TEXT"
            ");"
        )
        backend.commit()

    def test_insert_and_fetch():
        backend.execute(
            "INSERT INTO items (name, value, tags) VALUES (%s, %s, %s)",
            ("alpha", 1.5, "test"),
        )
        backend.execute(
            "INSERT INTO items (name, value, tags) VALUES (%s, %s, %s)",
            ("beta", 2.5, "prod"),
        )
        backend.commit()
        row = backend.fetchone("SELECT * FROM items WHERE name = %s", ("alpha",))
        assert row is not None, "fetchone returned None"
        assert row["name"] == "alpha"
        assert row["value"] == 1.5

    def test_fetchall():
        rows = backend.fetchall("SELECT * FROM items ORDER BY name")
        assert len(rows) >= 2, f"expected >=2 rows, got {len(rows)}"
        names = [r["name"] for r in rows]
        assert "alpha" in names
        assert "beta" in names

    def test_update():
        backend.execute(
            "UPDATE items SET value = %s WHERE name = %s",
            (9.99, "alpha"),
        )
        backend.commit()
        row = backend.fetchone("SELECT * FROM items WHERE name = %s", ("alpha",))
        assert row is not None
        assert row["value"] == 9.99

    def test_delete():
        backend.execute("DELETE FROM items WHERE name = %s", ("beta",))
        backend.commit()
        row = backend.fetchone("SELECT * FROM items WHERE name = %s", ("beta",))
        assert row is None

    def test_count():
        backend.execute(
            "INSERT INTO items (name, value, tags) VALUES (%s, %s, %s)",
            ("gamma", 3.0, "test"),
        )
        backend.commit()
        row = backend.fetchone("SELECT COUNT(*) AS cnt FROM items WHERE tags = %s", ("test",))
        assert row is not None
        assert row["cnt"] >= 2, f"expected >=2 test items, got {row['cnt']}"

    def test_cleanup():
        backend.execute("DROP TABLE IF EXISTS items")
        backend.commit()

    for fn in [
        test_create_and_insert,
        test_insert_and_fetch,
        test_fetchall,
        test_update,
        test_delete,
        test_count,
        test_cleanup,
    ]:
        run_test(label, fn.__name__, fn)


def _suite_memory_integration(backend, label: str):
    """Test MemoryManager with the given backend."""
    from agentbase.core.memory import MemoryManager

    mgr = MemoryManager(backend=backend)

    def test_save_and_get():
        mgr.save(agent_name="test", key="m1", content="hello world", tags=["greeting"])
        mem = mgr.get(agent_name="test", key="m1")
        assert mem is not None
        assert mem.content == "hello world"

    def test_list():
        mgr.save(agent_name="test", key="m2", content="second memory")
        memories = mgr.list(agent_name="test")
        assert len(memories) >= 2

    def test_search():
        results = mgr.search(agent_name="test", query="hello")
        assert len(results) >= 1
        assert results[0].content == "hello world"

    def test_delete():
        mgr.delete(agent_name="test", key="m2")
        with pytest.raises(KeyError):
            mgr.get(agent_name="test", key="m2")

    def test_cleanup():
        mgr.delete(agent_name="test", key="m1")
        mgr.close()

    for fn in [
        test_save_and_get,
        test_list,
        test_search,
        test_delete,
        test_cleanup,
    ]:
        run_test(label, f"memory_{fn.__name__}", fn)


def _suite_knowledge_integration(backend, label: str):
    """Test KnowledgeBase with the given backend."""
    from agentbase.core.knowledge import KnowledgeBase

    kb = KnowledgeBase(backend=backend)

    def test_add_doc():
        doc = kb.add_document(
            source="test.txt", title="Test Doc", content="The quick brown fox jumps over the lazy dog."
        )
        assert doc.title == "Test Doc"

    def test_search():
        results = kb.search("quick brown fox")
        assert len(results) > 0

    def test_list():
        docs = kb.list_documents()
        assert len(docs) >= 1

    def test_cleanup():
        docs = kb.list_documents()
        for d in docs:
            kb.delete_document(doc_id=d.id)
        kb.close()

    for fn in [
        test_add_doc,
        test_search,
        test_list,
        test_cleanup,
    ]:
        run_test(label, f"kb_{fn.__name__}", fn)


# ── SQLite ───────────────────────────────────────────────────────────────

def test_sqlite():
    from agentbase.core.storage import SQLiteBackend, create_storage

    print("\n[SQLite]")

    def test_factory():
        b = create_storage(db_path=Path(":memory:"))
        assert isinstance(b, SQLiteBackend)
        b.close()

    def test_placeholder():
        b = SQLiteBackend(db_path=Path(":memory:"))
        assert b._convert_sql("SELECT %s FROM t WHERE x = %s") == "SELECT ? FROM t WHERE x = ?"
        b.close()

    run_test("SQLite", "factory", test_factory)
    run_test("SQLite", "placeholder_conversion", test_placeholder)

    backend = SQLiteBackend(db_path=Path(":memory:"))
    _suite_crud(backend, "SQLite")
    backend.close()

    # Integration
    backend = SQLiteBackend(db_path=Path(":memory:"))
    _suite_memory_integration(backend, "SQLite")
    backend.close()

    backend = SQLiteBackend(db_path=Path(":memory:"))
    _suite_knowledge_integration(backend, "SQLite")
    backend.close()


# ── PostgreSQL ──────────────────────────────────────────────────────────

def test_postgres():
    print("\n[PostgreSQL]")
    try:
        import psycopg  # noqa: F401
    except ImportError:
        skip_test("PostgreSQL", "all", "psycopg not installed")
        return

    DSN = "postgresql://agentbase:agentbase@127.0.0.1:5432/agentbase"

    from agentbase.core.storage import PostgresBackend, create_storage

    def test_factory():
        b = create_storage(dsn=DSN)
        assert isinstance(b, PostgresBackend)
        b.close()

    def test_sql_conversion():
        b = PostgresBackend(dsn=DSN)
        converted = b._convert_sql(
            "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)"
        )
        assert "SERIAL" in converted
        assert "AUTOINCREMENT" not in converted
        b.close()

    run_test("PostgreSQL", "factory", test_factory)
    run_test("PostgreSQL", "sql_conversion", test_sql_conversion)

    # Clean up any leftover table
    backend = PostgresBackend(dsn=DSN)
    backend.execute("DROP TABLE IF EXISTS items")
    backend.commit()
    _suite_crud(backend, "PostgreSQL")
    backend.close()

    # Integration
    backend = PostgresBackend(dsn=DSN)
    backend.execute("DROP TABLE IF EXISTS agent_memory")
    backend.commit()
    _suite_memory_integration(backend, "PostgreSQL")
    backend.close()

    backend = PostgresBackend(dsn=DSN)
    backend.execute("DROP TABLE IF EXISTS kb_documents, kb_chunks")
    backend.commit()
    _suite_knowledge_integration(backend, "PostgreSQL")
    backend.close()


# ── MySQL ───────────────────────────────────────────────────────────────

def test_mysql():
    print("\n[MySQL]")
    try:
        import pymysql  # noqa: F401
    except ImportError:
        skip_test("MySQL", "all", "pymysql not installed")
        return

    DSN = "mysql://agentbase:agentbase@127.0.0.1:3307/agentbase"

    from agentbase.core.storage import MySQLBackend, create_storage

    # Check if MySQL server is reachable
    try:
        backend = MySQLBackend(dsn=DSN)
        backend.close()
    except Exception as exc:
        skip_test("MySQL", "all", f"MySQL server not reachable: {exc}")
        return

    def test_factory():
        b = create_storage(dsn=DSN)
        assert isinstance(b, MySQLBackend)
        b.close()

    def test_sql_conversion():
        b = MySQLBackend(dsn=DSN)
        converted = b._convert_sql(
            "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)"
        )
        assert "AUTO_INCREMENT" in converted
        assert "AUTOINCREMENT" not in converted
        b.close()

    run_test("MySQL", "factory", test_factory)
    run_test("MySQL", "sql_conversion", test_sql_conversion)

    # Clean up any leftover table
    backend = MySQLBackend(dsn=DSN)
    backend.execute("DROP TABLE IF EXISTS items")
    backend.commit()
    _suite_crud(backend, "MySQL")
    backend.close()

    # Integration
    backend = MySQLBackend(dsn=DSN)
    backend.execute("DROP TABLE IF EXISTS agent_memory")
    backend.commit()
    _suite_memory_integration(backend, "MySQL")
    backend.close()

    backend = MySQLBackend(dsn=DSN)
    backend.execute("DROP TABLE IF EXISTS kb_chunks")
    backend.execute("DROP TABLE IF EXISTS kb_documents")
    backend.commit()
    _suite_knowledge_integration(backend, "MySQL")
    backend.close()


# ── Main ────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Storage Backend Verification: SQLite / PostgreSQL / MySQL")
    print("=" * 60)

    test_sqlite()
    test_postgres()
    test_mysql()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    total = len(results)
    passed = sum(1 for _, _, s in results if s == PASS)
    failed = sum(1 for _, _, s in results if s == FAIL)
    skipped = sum(1 for _, _, s in results if s == SKIP)

    for backend_name in ["SQLite", "PostgreSQL", "MySQL"]:
        backend_tests = [(n, s) for b, n, s in results if b == backend_name]
        if not backend_tests:
            continue
        bp = sum(1 for _, s in backend_tests if s == PASS)
        bf = sum(1 for _, s in backend_tests if s == FAIL)
        bs = sum(1 for _, s in backend_tests if s == SKIP)
        status = PASS if bf == 0 else FAIL
        print(f"  {backend_name:12s}: {bp} passed, {bf} failed, {bs} skipped  [{status}]")

    print(f"\n  Total: {passed} passed, {failed} failed, {skipped} skipped / {total}")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
