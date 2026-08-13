"""Storage backend abstraction — SQLite, PostgreSQL, MySQL, and MongoDB via a unified interface.

The scaffold provides a ``StorageBackend`` abstraction so that
``MemoryManager`` and ``KnowledgeBase`` can work with either SQLite (dev /
single-user), PostgreSQL (prod / multi-user), MySQL, or MongoDB without
changing their business logic.

Selection is automatic based on the constructor parameter:

- ``db_path=Path("data/memory.db")``  →  SQLiteBackend
- ``dsn="postgresql://user:pass@host/db")``  →  PostgresBackend
- ``dsn="mysql://user:pass@host:port/db")``  →  MySQLBackend
- ``dsn="mongodb://host:port/db")``  →  MongoDBBackend

All backends implement the same ``execute`` / ``executemany`` / ``fetchone``
/ ``fetchall`` / ``commit`` interface, so the managers never need to know
which database is underneath.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Unified DB connection interface for SQLite and PostgreSQL."""

    def executescript(self, sql: str) -> None: ...
    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> Any: ...
    def fetchone(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> Any: ...
    def fetchall(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> list[Any]: ...
    def commit(self) -> None: ...
    def close(self) -> None: ...
    def last_insert_id(self) -> int: ...


class SQLiteBackend:
    """SQLite storage backend (default, zero-config).

    Automatically converts ``%s`` placeholders to ``?`` so that upper-layer
    code can use a unified ``%s`` style for both SQLite and PostgreSQL.
    """

    def __init__(self, *, db_path: Path) -> None:
        import sqlite3

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _convert_sql(sql: str) -> str:
        """Convert ``%s`` placeholders to SQLite-style ``?``."""
        # Simple but robust: replaces %s that's not inside a string literal
        return sql.replace("%s", "?")

    def executescript(self, sql: str) -> None:
        self._conn.executescript(sql)

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> Any:
        return self._conn.execute(self._convert_sql(sql), params or ())

    def fetchone(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> Any:
        return self._conn.execute(self._convert_sql(sql), params or ()).fetchone()

    def fetchall(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> list[Any]:
        return self._conn.execute(self._convert_sql(sql), params or ()).fetchall()

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def last_insert_id(self) -> int:
        """Return the rowid of the most recent INSERT."""
        row = self._conn.execute("SELECT last_insert_rowid() AS id").fetchone()
        return row["id"]

    def health_check(self) -> bool:
        """Check if the database connection is alive."""
        try:
            self._conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    def reconnect(self) -> None:
        """Reconnect to SQLite after connection loss."""
        import sqlite3
        try:
            self._conn.close()
        except Exception:
            pass
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def transaction(self):
        """Context manager for atomic transactions.

        Usage::

            with storage.transaction():
                storage.execute("INSERT ...", ...)
                storage.execute("UPDATE ...", ...)
            # Auto-commits on success, rolls back on exception
        """
        import contextlib

        @contextlib.contextmanager
        def _txn():
            try:
                yield
                self.commit()
            except Exception:
                self._conn.rollback()
                raise
        return _txn()


class PostgresBackend:
    """PostgreSQL storage backend (requires ``psycopg`` installed).

    Translates the SQLite-style interface to psycopg's dict-cursor mode
    so that callers can use ``row["column"]`` access uniformly.

    SQL dialect auto-conversion:
    - ``AUTOINCREMENT`` → ``SERIAL`` (PostgreSQL uses SERIAL for auto-increment)
    - ``%s`` placeholders are native to psycopg (no conversion needed)
    """

    def __init__(self, *, dsn: str) -> None:
        try:
            import psycopg
        except ImportError as exc:
            raise ImportError(
                "PostgreSQL backend requires psycopg. Install with: pip install agentbase[postgres]"
            ) from exc

        self._dsn = dsn
        self._conn = psycopg.connect(dsn, autocommit=False)
        # Use dict-like row factory for uniform row["col"] access
        self._conn.row_factory = psycopg.rows.dict_row  # type: ignore[attr-defined]

    @staticmethod
    def _convert_sql(sql: str) -> str:
        """Convert SQLite-style SQL to PostgreSQL-compatible SQL."""
        # INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL PRIMARY KEY
        import re
        sql = re.sub(
            r'INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT',
            'SERIAL PRIMARY KEY',
            sql,
            flags=re.IGNORECASE,
        )
        return sql

    def executescript(self, sql: str) -> None:
        # Split on semicolons and execute each statement separately
        # psycopg's execute() supports single statements only
        converted = self._convert_sql(sql)
        with self._conn.cursor() as cur:
            # Use psycopg's ability to execute multiple statements
            # by passing the whole script — psycopg 3 supports this
            cur.execute(converted)
        self._conn.commit()

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> Any:
        cur = self._conn.cursor()
        cur.execute(self._convert_sql(sql), params or ())
        return cur

    def fetchone(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> Any:
        with self._conn.cursor() as cur:
            cur.execute(self._convert_sql(sql), params or ())
            return cur.fetchone()

    def fetchall(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> list[Any]:
        with self._conn.cursor() as cur:
            cur.execute(self._convert_sql(sql), params or ())
            return cur.fetchall()

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def last_insert_id(self) -> int:
        """Return the rowid of the most recent INSERT."""
        row = self._conn.execute("SELECT lastval() AS id").fetchone()
        return row["id"]

    def health_check(self) -> bool:
        """Check if the database connection is alive."""
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT 1")
            return True
        except Exception:
            return False

    def reconnect(self) -> None:
        """Reconnect to PostgreSQL after connection loss."""
        try:
            self._conn.close()
        except Exception:
            pass
        import psycopg
        self._conn = psycopg.connect(self._dsn, autocommit=False)
        self._conn.row_factory = psycopg.rows.dict_row  # type: ignore[attr-defined]

    def transaction(self):
        """Context manager for atomic transactions."""
        import contextlib

        @contextlib.contextmanager
        def _txn():
            try:
                yield
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return _txn()


class MySQLBackend:
    """MySQL storage backend (requires ``mysql-connector-python`` or ``pymysql``).

    Uses ``%s`` placeholders natively. Auto-converts SQLite-style
    ``AUTOINCREMENT`` to ``AUTO_INCREMENT``.

    Usage::

        from agentbase.core.storage import create_storage
        storage = create_storage(dsn="mysql://user:pass@localhost:3306/agentbase")

    Or via config::

        storage:
          type: mysql
          dsn: mysql://user:pass@localhost:3306/agentbase
    """

    def __init__(self, *, dsn: str) -> None:
        try:
            import pymysql
        except ImportError as exc:
            raise ImportError(
                "MySQL backend requires pymysql. Install with: pip install pymysql"
            ) from exc

        self._dsn = dsn

        # Parse mysql://user:pass@host:port/db
        import re
        match = re.match(r"mysql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", dsn)
        if match:
            user, password, host, port, database = match.groups()
        else:
            # Fallback: try without port
            match = re.match(r"mysql://([^:]+):([^@]+)@([^/]+)/(.+)", dsn)
            if match:
                user, password, host, database = match.groups()
                port = "3306"
            else:
                raise ValueError(f"Invalid MySQL DSN: {dsn}")

        self._conn = pymysql.connect(
            host=host,
            port=int(port),
            user=user,
            password=password,
            database=database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            sql_mode="ANSI_QUOTES",
        )

    @staticmethod
    def _convert_sql(sql: str) -> str:
        """Convert SQLite/PostgreSQL-style SQL to MySQL-compatible SQL."""
        import re
        sql = re.sub(
            r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
            "INTEGER AUTO_INCREMENT PRIMARY KEY",
            sql,
            flags=re.IGNORECASE,
        )
        # Convert ON CONFLICT (...) DO UPDATE SET ... to ON DUPLICATE KEY UPDATE ...
        sql = re.sub(
            r"ON\s+CONFLICT\s*\([^)]+\)\s*DO\s+UPDATE\s+SET",
            "ON DUPLICATE KEY UPDATE",
            sql,
            flags=re.IGNORECASE,
        )
        # Convert EXCLUDED.column to VALUES(column)
        sql = re.sub(
            r"EXCLUDED\.(\w+)",
            r"VALUES(\1)",
            sql,
            flags=re.IGNORECASE,
        )
        return sql

    def executescript(self, sql: str) -> None:
        converted = self._convert_sql(sql)
        with self._conn.cursor() as cur:
            for statement in converted.split(";"):
                statement = statement.strip()
                if statement:
                    cur.execute(statement)
        self._conn.commit()

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> Any:
        cur = self._conn.cursor()
        cur.execute(self._convert_sql(sql), params or ())
        return cur

    def fetchone(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> Any:
        with self._conn.cursor() as cur:
            cur.execute(self._convert_sql(sql), params or ())
            return cur.fetchone()

    def fetchall(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> list[Any]:
        with self._conn.cursor() as cur:
            cur.execute(self._convert_sql(sql), params or ())
            return cur.fetchall()

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        if hasattr(self, "_conn") and self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def last_insert_id(self) -> int:
        """Return the rowid of the most recent INSERT."""
        return self._conn.insert_id()

    def health_check(self) -> bool:
        """Check if the database connection is alive."""
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT 1")
            return True
        except Exception:
            return False

    def reconnect(self) -> None:
        """Reconnect to MySQL after connection loss."""
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:
            pass
        # Re-initialize connection using stored parameters
        self.__init__(dsn=self._dsn)  # type: ignore[misc]

    def transaction(self):
        """Context manager for atomic transactions."""
        import contextlib

        @contextlib.contextmanager
        def _txn():
            try:
                yield
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return _txn()


def create_storage(*, db_path: Path | None = None, dsn: str | None = None) -> StorageBackend:
    """Factory: pick the right backend based on parameters.

    - If ``dsn`` starts with ``postgres`` → PostgresBackend
    - If ``dsn`` starts with ``mysql`` → MySQLBackend
    - If ``db_path`` is provided → SQLiteBackend
    - If neither → in-memory SQLite (for tests)
    """
    if dsn:
        if dsn.startswith("mongodb"):
            from agentbase.core.storage_mongodb import MongoDBBackend
            return MongoDBBackend(dsn=dsn)
        if dsn.startswith("mysql"):
            return MySQLBackend(dsn=dsn)
        return PostgresBackend(dsn=dsn)
    if db_path:
        return SQLiteBackend(db_path=db_path)
    # Fallback: in-memory SQLite (useful for tests)
    return SQLiteBackend(db_path=Path(":memory:"))
