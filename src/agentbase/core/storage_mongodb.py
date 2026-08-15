"""MongoDB storage backend — adapts the SQL-style StorageBackend interface to MongoDB.

This backend allows MemoryManager, KnowledgeBase, and AuditLogService to
use MongoDB as their storage layer by translating the unified SQL-style
interface (``execute`` / ``fetchone`` / ``fetchall`` / ``commit`` /
``close`` / ``last_insert_id``) into MongoDB operations.

Supported SQL translations:
- ``CREATE TABLE IF NOT EXISTS <name> (...)`` → ``create_collection()``
- ``CREATE INDEX IF NOT EXISTS ... ON <table>(<col>)`` → ``create_index()``
- ``INSERT INTO <table> (cols) VALUES (%s, ...)`` → ``insert_one()``
- ``SELECT * FROM <table>`` → ``find()``
- ``SELECT * FROM <table> WHERE <col> = %s`` → ``find({col: val})``
- ``SELECT COUNT(*) AS cnt FROM <table>`` → ``count_documents()``
- ``UPDATE <table> SET ... WHERE ...`` → ``update_many()``
- ``DELETE FROM <table> WHERE ...`` → ``delete_many()``

Requires ``pymongo`` installed. Install with::

    pip install agentbase[mongodb]

Usage via config::

    storage:
      type: mongodb
      dsn: mongodb://localhost:27017/agentbase

Usage programmatically::

    from agentbase.core.storage import create_storage
    storage = create_storage(dsn="mongodb://localhost:27017/agentbase")
"""
from __future__ import annotations

import re
import threading
from typing import Any

from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


class _Row(dict):
    """Dict subclass that also supports attribute access for compatibility.

    MongoDB documents are dicts, but some callers use ``row.column``
    (e.g. ``row.id``). This class bridges the gap.
    """

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")


class MongoDBBackend:
    """MongoDB storage backend (requires ``pymongo`` installed).

    Implements the same ``StorageBackend`` Protocol as ``SQLiteBackend``
    and ``PostgresBackend``, translating SQL-style calls to MongoDB operations.

    Thread-safe via ``threading.RLock`` for the insert ID tracking.
    """

    def __init__(self, *, dsn: str) -> None:
        try:
            import pymongo
        except ImportError as exc:
            raise ImportError(
                "MongoDB backend requires pymongo. "
                "Install with: pip install agentbase[mongodb]"
            ) from exc

        self._dsn = dsn
        self._lock = threading.RLock()
        self._last_insert_id: int = 0

        # Parse mongodb://user:pass@host:port/database
        # pymongo.MongoClient handles full URI parsing
        self._client = pymongo.MongoClient(dsn)

        # Extract database name from DSN
        # Format: mongodb://host:port/dbname or mongodb://user:pass@host:port/dbname
        db_match = re.search(r"/([^/?]+)(?:\?|$)", dsn)
        if db_match:
            self._db_name = db_match.group(1)
        else:
            self._db_name = "agentbase"

        self._db = self._client[self._db_name]
        logger.info(
            "MongoDB backend connected: db=%s",
            self._db_name,
            extra={
                "event": "storage.mongodb.connected",
                "database": self._db_name,
            },
        )

    @staticmethod
    def _convert_sql(sql: str) -> str:
        """Convert %s placeholders — MongoDB backend handles params separately."""
        return sql

    @staticmethod
    def _parse_create_table(sql: str) -> dict[str, Any] | None:
        """Parse CREATE TABLE IF NOT EXISTS <name> (...).

        Returns dict with 'table' name, or None if not a CREATE TABLE statement.
        """
        # CREATE TABLE IF NOT EXISTS <name> (col defs...)
        match = re.match(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\(",
            sql,
            re.IGNORECASE,
        )
        if match:
            return {"table": match.group(1)}
        return None

    @staticmethod
    def _parse_create_index(sql: str) -> dict[str, Any] | None:
        """Parse CREATE INDEX IF NOT EXISTS <idx> ON <table>(<col>)."""
        match = re.match(
            r"CREATE\s+INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+ON\s+(\w+)\s*\(([^)]+)\)",
            sql,
            re.IGNORECASE,
        )
        if match:
            idx_name, table, cols = match.groups()
            return {
                "index_name": idx_name,
                "table": table,
                "columns": [c.strip() for c in cols.split(",")],
            }
        return None

    @staticmethod
    def _parse_insert(sql: str, params: tuple[Any, ...] | list[Any] | None) -> dict[str, Any] | None:
        """Parse INSERT INTO <table> (cols) VALUES (%s, ...)."""
        match = re.match(
            r"INSERT\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)",
            sql,
            re.IGNORECASE,
        )
        if match:
            table, cols_str, vals_str = match.groups()
            columns = [c.strip() for c in cols_str.split(",")]
            # Count %s placeholders to map params
            param_count = vals_str.count("%s")
            param_values = list(params or ())
            if len(param_values) < param_count:
                # Pad with None if not enough params
                param_values.extend([None] * (param_count - len(param_values)))
            return {
                "table": table,
                "columns": columns,
                "values": param_values[:param_count],
            }
        return None

    @staticmethod
    def _parse_select(sql: str, params: tuple[Any, ...] | list[Any] | None) -> dict[str, Any] | None:
        """Parse SELECT ... FROM <table> [WHERE ...] [ORDER BY ...] [LIMIT N].

        Supports:
        - SELECT * FROM <table>
        - SELECT * FROM <table> WHERE <col> = %s [AND <col2> = %s ...]
        - SELECT <cols> FROM <table>
        - SELECT COUNT(*) AS cnt FROM <table> [WHERE ...]
        """
        sql_stripped = sql.strip()

        # Check for COUNT(*)
        count_match = re.match(
            r"SELECT\s+COUNT\s*\(\s*\*\s*\)\s+AS\s+(\w+)\s+FROM\s+(\w+)",
            sql_stripped,
            re.IGNORECASE,
        )
        if count_match:
            count_alias, table = count_match.groups()
            # Parse WHERE clause
            where = MongoDBBackend._parse_where(
                sql_stripped[count_match.end():], params
            )
            return {
                "type": "count",
                "count_alias": count_alias,
                "table": table,
                "where": where,
            }

        # Regular SELECT
        match = re.match(
            r"SELECT\s+(.+?)\s+FROM\s+(\w+)",
            sql_stripped,
            re.IGNORECASE,
        )
        if match:
            cols_str, table = match.groups()
            if cols_str.strip() == "*":
                columns = None  # All columns
            else:
                columns = [c.strip() for c in cols_str.split(",")]

            # Parse WHERE clause
            rest = sql_stripped[match.end():]
            where = MongoDBBackend._parse_where(rest, params)

            # Parse ORDER BY
            order_by: list[tuple[str, int]] = []
            order_match = re.search(
                r"ORDER\s+BY\s+(\w+)(?:\s+(ASC|DESC))?",
                rest,
                re.IGNORECASE,
            )
            if order_match:
                col, direction = order_match.groups()
                order_by.append((col, 1 if direction.upper() != "DESC" else -1))

            # Parse LIMIT
            limit: int | None = None
            limit_match = re.search(r"LIMIT\s+(\d+)", rest, re.IGNORECASE)
            if limit_match:
                limit = int(limit_match.group(1))

            # Parse OFFSET
            offset: int | None = None
            offset_match = re.search(r"OFFSET\s+(\d+)", rest, re.IGNORECASE)
            if offset_match:
                offset = int(offset_match.group(1))

            return {
                "type": "select",
                "columns": columns,
                "table": table,
                "where": where,
                "order_by": order_by,
                "limit": limit,
                "offset": offset,
            }
        return None

    @staticmethod
    def _parse_where(
        sql_after_table: str,
        params: tuple[Any, ...] | list[Any] | None,
    ) -> dict[str, Any]:
        """Parse WHERE clause into MongoDB query dict.

        Supports: WHERE col = %s [AND col2 = %s ...]
        """
        param_values = list(params or ())
        param_idx = 0

        where_match = re.search(
            r"WHERE\s+(.+?)(?:\s+ORDER\s+BY|\s+LIMIT\s+|$)",
            sql_after_table,
            re.IGNORECASE,
        )
        if not where_match:
            return {}

        where_clause = where_match.group(1).strip()
        # Split on AND
        conditions = re.split(r"\s+AND\s+", where_clause, flags=re.IGNORECASE)
        query: dict[str, Any] = {}

        for cond in conditions:
            cond = cond.strip()
            # Match: col = %s  or  col >= %s  or  col < %s  etc.
            match = re.match(r"(\w+)\s*(=|>=|<=|>|<|!=)\s*%s", cond)
            if match:
                col, op = match.groups()
                val = param_values[param_idx] if param_idx < len(param_values) else None
                param_idx += 1

                if op == "=":
                    query[col] = val
                elif op == "!=":
                    query[col] = {"$ne": val}
                elif op == ">":
                    query[col] = {"$gt": val}
                elif op == ">=":
                    query[col] = {"$gte": val}
                elif op == "<":
                    query[col] = {"$lt": val}
                elif op == "<=":
                    query[col] = {"$lte": val}

        return query

    @staticmethod
    def _parse_update(sql: str, params: tuple[Any, ...] | list[Any] | None) -> dict[str, Any] | None:
        """Parse UPDATE <table> SET col=%s, ... WHERE col=%s."""
        match = re.match(
            r"UPDATE\s+(\w+)\s+SET\s+(.+?)(?:\s+WHERE\s+(.+))?$",
            sql.strip(),
            re.IGNORECASE | re.DOTALL,
        )
        if match:
            table, set_str, where_str = match.groups()
            param_values = list(params or ())
            param_idx = 0

            # Parse SET assignments
            set_doc: dict[str, Any] = {}
            assignments = re.split(r"\s*,\s*", set_str.strip())
            for assign in assignments:
                assign_match = re.match(r"(\w+)\s*=\s*%s", assign.strip())
                if assign_match:
                    col = assign_match.group(1)
                    val = param_values[param_idx] if param_idx < len(param_values) else None
                    param_idx += 1
                    set_doc[col] = val

            # Parse WHERE
            query: dict[str, Any] = {}
            if where_str:
                conditions = re.split(r"\s+AND\s+", where_str.strip(), flags=re.IGNORECASE)
                for cond in conditions:
                    cond_match = re.match(r"(\w+)\s*=\s*%s", cond.strip())
                    if cond_match:
                        col = cond_match.group(1)
                        val = param_values[param_idx] if param_idx < len(param_values) else None
                        param_idx += 1
                        query[col] = val

            return {
                "table": table,
                "set": set_doc,
                "where": query,
            }
        return None

    @staticmethod
    def _parse_delete(sql: str, params: tuple[Any, ...] | list[Any] | None) -> dict[str, Any] | None:
        """Parse DELETE FROM <table> WHERE col=%s [AND ...]."""
        match = re.match(
            r"DELETE\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+))?$",
            sql.strip(),
            re.IGNORECASE | re.DOTALL,
        )
        if match:
            table, where_str = match.groups()
            where = MongoDBBackend._parse_where(
                f"WHERE {where_str}" if where_str else "",
                params,
            )
            return {
                "table": table,
                "where": where,
            }
        return None

    # ------------------------------------------------------------------
    # StorageBackend Protocol implementation
    # ------------------------------------------------------------------

    def executescript(self, sql: str) -> None:
        """Execute a multi-statement SQL script.

        Splits on semicolons and processes each statement.
        For MongoDB, CREATE TABLE / CREATE INDEX are no-ops (collections
        are created automatically on first insert).
        """
        for statement in sql.split(";"):
            statement = statement.strip()
            if not statement:
                continue

            create_table = self._parse_create_table(statement)
            if create_table:
                # MongoDB creates collections automatically — no-op
                logger.debug(
                    "MongoDB: CREATE TABLE → auto-create collection '%s'",
                    create_table["table"],
                )
                continue

            create_index = self._parse_create_index(statement)
            if create_index:
                try:
                    collection = self._db[create_index["table"]]
                    for col in create_index["columns"]:
                        collection.create_index(col, name=create_index["index_name"])
                except Exception as exc:
                    logger.debug("MongoDB: create_index skipped: %s", exc)
                continue

            # Other statements — execute individually
            try:
                self.execute(statement)
            except Exception as exc:
                logger.debug("MongoDB: executescript statement skipped: %s", exc)

    def execute(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] | None = None,
    ) -> Any:
        """Execute a single SQL statement.

        For INSERT/UPDATE/DELETE, performs the MongoDB operation.
        For SELECT, delegates to fetchall and returns the results.
        """
        sql = sql.strip()

        # INSERT
        insert = self._parse_insert(sql, params)
        if insert:
            doc: dict[str, Any] = {}
            for col, val in zip(insert["columns"], insert["values"]):
                doc[col] = val

            collection = self._db[insert["table"]]
            result = collection.insert_one(doc)

            # Track insert ID (MongoDB uses ObjectId, we track a numeric counter)
            with self._lock:
                self._last_insert_id += 1

            logger.debug(
                "MongoDB: INSERT into '%s' → _id=%s",
                insert["table"],
                result.inserted_id,
            )
            return result

        # UPDATE
        update = self._parse_update(sql, params)
        if update:
            collection = self._db[update["table"]]
            result = collection.update_many(
                update["where"],
                {"$set": update["set"]},
            )
            logger.debug(
                "MongoDB: UPDATE '%s' → matched=%d modified=%d",
                update["table"],
                result.matched_count,
                result.modified_count,
            )
            return result

        # DELETE
        delete = self._parse_delete(sql, params)
        if delete:
            collection = self._db[delete["table"]]
            result = collection.delete_many(delete["where"])
            logger.debug(
                "MongoDB: DELETE from '%s' → deleted=%d",
                delete["table"],
                result.deleted_count,
            )
            return result

        # Fallback: try as SELECT
        return self.fetchall(sql, params)

    def fetchone(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] | None = None,
    ) -> Any:
        """Execute a SELECT and return the first row, or None."""
        select = self._parse_select(sql, params)
        if select is None:
            return None

        if select["type"] == "count":
            collection = self._db[select["table"]]
            count = collection.count_documents(select["where"])
            return _Row({select["count_alias"]: count})

        # Regular SELECT
        collection = self._db[select["table"]]

        # Build projection
        projection: dict[str, int] | None = None
        if select["columns"] is not None:
            projection = {col: 1 for col in select["columns"]}
            # Always include _id unless explicitly excluded
            if "_id" not in select["columns"]:
                projection["_id"] = 0

        cursor = collection.find(select["where"], projection=projection)

        # Apply sort
        if select["order_by"]:
            cursor = cursor.sort(select["order_by"])

        # Apply limit 1 for fetchone
        cursor = cursor.limit(1)

        # Apply offset
        if select["offset"]:
            cursor = cursor.skip(select["offset"])

        doc = cursor.fetchone() if hasattr(cursor, "fetchone") else next(cursor, None)
        if doc is None:
            return None

        # Convert _id to id for compatibility
        if "_id" in doc and "id" not in doc:
            doc["id"] = str(doc.pop("_id"))

        return _Row(doc)

    def fetchall(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] | None = None,
    ) -> list[Any]:
        """Execute a SELECT and return all matching rows."""
        select = self._parse_select(sql, params)
        if select is None:
            return []

        if select["type"] == "count":
            collection = self._db[select["table"]]
            count = collection.count_documents(select["where"])
            return [_Row({select["count_alias"]: count})]

        # Regular SELECT
        collection = self._db[select["table"]]

        # Build projection
        projection: dict[str, int] | None = None
        if select["columns"] is not None:
            projection = {col: 1 for col in select["columns"]}
            if "_id" not in select["columns"]:
                projection["_id"] = 0

        cursor = collection.find(select["where"], projection=projection)

        # Apply sort
        if select["order_by"]:
            cursor = cursor.sort(select["order_by"])

        # Apply limit
        if select["limit"] is not None:
            cursor = cursor.limit(select["limit"])

        # Apply offset
        if select["offset"]:
            cursor = cursor.skip(select["offset"])

        results: list[Any] = []
        for doc in cursor:
            if "_id" in doc and "id" not in doc:
                doc["id"] = str(doc.pop("_id"))
            results.append(_Row(doc))

        return results

    def commit(self) -> None:
        """No-op for MongoDB (auto-committed per operation)."""
        pass

    def close(self) -> None:
        """Close the MongoDB client connection."""
        try:
            self._client.close()
        except Exception as exc:
            logger.debug("MongoDB: close error: %s", exc)

    def last_insert_id(self) -> int:
        """Return the last insert ID (numeric counter, not MongoDB ObjectId)."""
        with self._lock:
            return self._last_insert_id

    def health_check(self) -> bool:
        """Check if the MongoDB connection is alive."""
        try:
            self._client.admin.command("ping")
            return True
        except Exception:
            return False

    def reconnect(self) -> None:
        """Reconnect to MongoDB after connection loss."""
        try:
            self._client.close()
        except Exception:
            pass
        import pymongo
        self._client = pymongo.MongoClient(self._dsn)
        self._db = self._client[self._db_name]

    def transaction(self):
        """Context manager for atomic transactions.

        MongoDB doesn't have multi-document transactions without replica sets,
        so this is a best-effort no-op that just yields. Individual operations
        are atomic at the document level.
        """
        import contextlib

        @contextlib.contextmanager
        def _txn():
            yield

        return _txn()
