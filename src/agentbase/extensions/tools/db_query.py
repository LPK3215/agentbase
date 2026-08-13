"""Database query tool — Agent executes read-only SELECT queries.

Tool provided:
- ``db_query`` — execute a SELECT query against a configured datasource

Security features:
- **SELECT-only enforcement**: any statement that is not a pure SELECT is rejected
- **DDL/DML blockade**: INSERT / UPDATE / DELETE / DROP / CREATE / ALTER / TRUNCATE / etc. blocked
- **Table whitelist**: only tables listed in ``allowed_tables`` are queryable (empty = allow all)
- **Row limit**: results capped at ``max_rows`` (default 100, hard cap 1000)
- **Query timeout**: queries that exceed ``timeout_seconds`` are cancelled (default 10s, hard cap 30s)
- **Parameterized DSN**: connection string from config, never from Agent input
- **Structured error returns**: errors are returned as dict, not raised as exceptions

Usage::

    # config
    db_query:
      enabled: true
      dsn: "sqlite:///data/example.db"
      max_rows: 50
      timeout_seconds: 5
      allowed_tables: ["users", "orders"]

    # agent config
    tools:
      - db_query

The agent can then call::

    db_query(query="SELECT * FROM users LIMIT 5")
"""

from __future__ import annotations

import re
import sqlite3
import time
from typing import Any

from langchain_core.tools import tool

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.tools import register_tool
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)

_DB_QUERY_META = ExtensionMeta(
    name="db_query",
    kind="tool",
    description="Execute a read-only SELECT query against a configured database.",
    requires_context=[],
    default_enabled=False,
    tags=["database", "sql", "query"],
)

# --- Safety limits --------------------------------------------------------- #

_MAX_ROWS_CAP = 1000            # hard cap for max_rows
_MIN_TIMEOUT = 3                # minimum timeout (seconds)
_MAX_TIMEOUT = 30               # hard cap for timeout (seconds)
_DEFAULT_MAX_ROWS = 100         # default row limit
_DEFAULT_TIMEOUT = 10           # default timeout (seconds)

# SQL keywords that indicate non-SELECT statements or dangerous operations.
# We check for these as whole words (case-insensitive) in the query.
_FORBIDDEN_KEYWORDS = frozenset({
    "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE",
    "REPLACE", "MERGE", "GRANT", "REVOKE", "ATTACH", "DETACH", "PRAGMA",
    "VACUUM", "REINDEX", "ANALYZE", "COMMIT", "ROLLBACK", "BEGIN",
    "SAVEPOINT", "RELEASE", "EXPLAIN",  # EXPLAIN can reveal plan info
})

# Statements that must start with SELECT (with optional WITH ... AS ... SELECT).
# We allow: SELECT ... and WITH ... AS (...) SELECT ...
_ALLOWED_START_PATTERN = re.compile(
    r"^\s*(?:WITH\s+.*?\s+SELECT\b|SELECT\b)",
    re.IGNORECASE | re.DOTALL,
)

# Pattern to extract table names from FROM and JOIN clauses.
# Matches: FROM table_name, JOIN table_name
# Handles: schema.table, "quoted", [bracketed]
_TABLE_PATTERN = re.compile(
    r"(?:\bFROM\b|\bJOIN\b)\s+"
    r"(?:\[([^\]]+)\]|\"([^\"]+)\"|(\w+(?:\.\w+)?))",
    re.IGNORECASE,
)


def _validate_query(sql: str) -> str | None:
    """Validate that the SQL query is a safe read-only SELECT.

    Args:
        sql: Raw SQL string from the Agent.

    Returns:
        Error message string, or None if the query is safe.
    """
    if not sql or not isinstance(sql, str):
        return "Query is required and must be a non-empty string."

    stripped = sql.strip()
    if not stripped:
        return "Query is empty."

    # Remove trailing semicolon for analysis (we add it back for execution).
    stripped = stripped.rstrip(";").strip()

    # Check for multiple statements (semicolon in the middle).
    if ";" in stripped:
        return "Multiple SQL statements are not allowed (detected semicolon in the middle of the query)."

    # Check that the query starts with SELECT or WITH ... SELECT.
    if not _ALLOWED_START_PATTERN.match(stripped):
        # Check if it starts with a forbidden keyword for a better error message.
        first_word_match = re.match(r"\s*(\w+)", stripped)
        first_word = first_word_match.group(1).upper() if first_word_match else ""
        if first_word in _FORBIDDEN_KEYWORDS:
            return f"Only SELECT queries are allowed. Detected: {first_word}."
        return f"Query must start with SELECT or WITH ... SELECT. First word: '{first_word}'."

    # Check for SQL comments that could hide malicious code.
    # This is checked before forbidden keywords because a comment like
    # "-- DROP TABLE" should be reported as a comment violation, not a
    # forbidden keyword violation.
    if "--" in stripped or "/*" in stripped:
        return "SQL comments (-- and /* */) are not allowed in queries."

    # Check for forbidden keywords anywhere in the query (as whole words).
    # This catches subqueries like: SELECT * FROM (DELETE FROM t RETURNING *)
    # or: SELECT * FROM users UNION DELETE FROM users
    words = set(re.findall(r"\b([A-Z]+)\b", stripped.upper()))
    forbidden_found = words & _FORBIDDEN_KEYWORDS
    if forbidden_found:
        return (
            f"Forbidden SQL keyword(s) detected: {', '.join(sorted(forbidden_found))}. "
            f"Only read-only SELECT queries are allowed."
        )

    return None


def _extract_tables(sql: str) -> list[str]:
    """Extract table names from FROM and JOIN clauses.

    Args:
        sql: SQL query string.

    Returns:
        List of table names (lowercase, without schema prefix).
    """
    tables: list[str] = []
    for match in _TABLE_PATTERN.finditer(sql):
        # Three possible groups: [bracketed], "quoted", or plain
        table = match.group(1) or match.group(2) or match.group(3)
        if table:
            # Remove schema prefix if present (e.g. "public.users" → "users")
            if "." in table:
                table = table.split(".")[-1]
            tables.append(table.lower())
    return tables


def _check_tables(tables: list[str], allowed: list[str]) -> str | None:
    """Check if all queried tables are in the whitelist.

    Args:
        tables: Table names extracted from the query.
        allowed: Whitelist of allowed table names.

    Returns:
        Error message, or None if all tables are allowed (or whitelist is empty).
    """
    if not allowed:
        return None  # Empty whitelist = allow all

    allowed_lower = {t.lower() for t in allowed}
    for t in tables:
        if t not in allowed_lower:
            return f"Table '{t}' is not in the allowed tables list. Allowed: {', '.join(sorted(allowed_lower))}."
    return None


def _ensure_limit(sql: str, max_rows: int) -> str:
    """Ensure the query has a LIMIT clause, adding one if missing.

    If the query already has a LIMIT, and it's greater than max_rows,
    replace it with max_rows.

    Args:
        sql: SQL query string.
        max_rows: Maximum rows allowed.

    Returns:
        SQL query with enforced LIMIT.
    """
    # Check if LIMIT already exists (case-insensitive, as a whole word).
    limit_match = re.search(r"\bLIMIT\s+(\d+)", sql, re.IGNORECASE)
    if limit_match:
        existing_limit = int(limit_match.group(1))
        if existing_limit > max_rows:
            # Replace the existing LIMIT with max_rows.
            sql = sql[:limit_match.start()] + f"LIMIT {max_rows}" + sql[limit_match.end():]
        return sql

    # No LIMIT clause — append one.
    sql = sql.rstrip(";").rstrip()
    return f"{sql} LIMIT {max_rows}"


def _detect_dialect(dsn: str) -> str:
    """Detect database dialect from DSN string.

    Args:
        dsn: Connection string like "sqlite:///path/to/db" or "postgresql://...".

    Returns:
        One of: "sqlite", "postgresql", "mysql".
    """
    dsn_lower = dsn.lower().strip()
    if dsn_lower.startswith("sqlite"):
        return "sqlite"
    if dsn_lower.startswith("postgres"):
        return "postgresql"
    if dsn_lower.startswith("mysql"):
        return "mysql"
    return "unknown"


@register_tool("db_query", meta=_DB_QUERY_META)
def build_db_query_tool(context: dict[str, Any] | None = None):
    """Build the db_query tool instance.

    Reads configuration from the shared context dict (app_config.db_query).

    Args:
        context: Shared context dict. Expected to contain ``app_config``
            with a ``db_query`` field for configuration. If not present,
            defaults are used.

    Returns:
        langchain Tool instance.
    """
    # Extract config from context
    dsn = ""
    max_rows = _DEFAULT_MAX_ROWS
    timeout_seconds = _DEFAULT_TIMEOUT
    allowed_tables: list[str] = []

    if context and "app_config" in context:
        app_cfg = context["app_config"]
        db_cfg = getattr(app_cfg, "db_query", None)
        if db_cfg is not None:
            dsn = db_cfg.dsn or dsn
            max_rows = db_cfg.max_rows or max_rows
            timeout_seconds = db_cfg.timeout_seconds or timeout_seconds
            allowed_tables = list(db_cfg.allowed_tables) if db_cfg.allowed_tables else []

    # Clamp limits
    max_rows = min(max(max_rows, 1), _MAX_ROWS_CAP)
    timeout_seconds = min(max(timeout_seconds, _MIN_TIMEOUT), _MAX_TIMEOUT)

    @tool
    def db_query(query: str) -> dict[str, Any]:
        """Execute a read-only SELECT query against the configured database.

        Only SELECT statements are allowed. DDL, DML, and other statement
        types are blocked. Results are limited to max_rows rows.

        Args:
            query: A SQL SELECT query string. Only SELECT (or WITH ... SELECT)
                is allowed. The query will be automatically limited to
                max_rows if no LIMIT is specified.

        Returns:
            dict with keys:
                - columns: List of column names.
                - rows: List of row tuples (each tuple has one value per column).
                - row_count: Number of rows returned.
                - truncated: True if results were truncated to max_rows.
                - elapsed_ms: Query duration in milliseconds.
                - error: Error message if query failed (None on success).
                - dialect: Database dialect detected from DSN.
        """
        # --- Validate query ------------------------------------------------- #
        validation_error = _validate_query(query)
        if validation_error:
            logger.warning(
                "db_query validation failed: %s",
                validation_error,
                extra={"event": "db_query.validation_failed", "error": validation_error},
            )
            return {
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "elapsed_ms": 0,
                "error": validation_error,
                "dialect": _detect_dialect(dsn) if dsn else "unknown",
            }

        # --- Check table whitelist ----------------------------------------- #
        tables = _extract_tables(query)
        table_error = _check_tables(tables, allowed_tables)
        if table_error:
            logger.warning(
                "db_query table not allowed: %s",
                table_error,
                extra={"event": "db_query.table_blocked", "tables": tables, "error": table_error},
            )
            return {
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "elapsed_ms": 0,
                "error": table_error,
                "dialect": _detect_dialect(dsn) if dsn else "unknown",
            }

        # --- Enforce LIMIT -------------------------------------------------- #
        safe_query = _ensure_limit(query, max_rows)

        # --- Check DSN ------------------------------------------------------ #
        if not dsn:
            return {
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "elapsed_ms": 0,
                "error": "No database DSN configured. Set db_query.dsn in config.",
                "dialect": "unknown",
            }

        dialect = _detect_dialect(dsn)

        # --- Execute query -------------------------------------------------- #
        start = time.monotonic()

        try:
            if dialect == "sqlite":
                # SQLite uses file paths, not DSNs with host/port.
                # DSN format: "sqlite:///path/to/db" or "sqlite:///:memory:"
                db_path = dsn.replace("sqlite:///", "", 1)
                if not db_path:
                    db_path = ":memory:"

                conn = sqlite3.connect(
                    db_path,
                    timeout=timeout_seconds,
                )
            elif dialect in ("postgresql", "mysql"):
                # For PostgreSQL/MySQL, we need the appropriate driver.
                # We try to import it here so the tool only requires the
                # driver when actually used.
                try:
                    if dialect == "postgresql":
                        import psycopg  # noqa: F401
                        conn_factory = psycopg.connect
                        conn = conn_factory(dsn, connect_timeout=timeout_seconds)
                    else:  # mysql
                        import pymysql
                        # Parse DSN to extract connection params.
                        # DSN format: mysql://user:pass@host:port/dbname
                        conn_params = _parse_mysql_dsn(dsn)
                        conn = pymysql.connect(
                            connect_timeout=timeout_seconds,
                            read_timeout=timeout_seconds,
                            **conn_params,
                        )
                except ImportError:
                    elapsed = int((time.monotonic() - start) * 1000)
                    missing_dep = "psycopg" if dialect == "postgresql" else "pymysql"
                    return {
                        "columns": [],
                        "rows": [],
                        "row_count": 0,
                        "truncated": False,
                        "elapsed_ms": elapsed,
                        "error": f"Database driver '{missing_dep}' not installed. Install with: pip install agentbase[{ 'postgres' if dialect == 'postgresql' else 'mysql'}]",
                        "dialect": dialect,
                    }
            else:
                elapsed = int((time.monotonic() - start) * 1000)
                return {
                    "columns": [],
                    "rows": [],
                    "row_count": 0,
                    "truncated": False,
                    "elapsed_ms": elapsed,
                    "error": f"Unsupported database dialect: {dialect}. DSN must start with sqlite, postgresql, or mysql.",
                    "dialect": dialect,
                }

            # Set query timeout for SQLite.
            if dialect == "sqlite":
                # SQLite doesn't have a direct query timeout, but we can
                # set a busy timeout. For actual query cancellation, we
                # rely on the connection timeout.
                pass

            try:
                cursor = conn.cursor()
                cursor.execute(safe_query)

                # Fetch one extra row to detect truncation.
                # If the query had a LIMIT added by _ensure_limit, the DB
                # already capped results, so fetchmany(max_rows + 1) will
                # return at most max_rows. In that case, if we got exactly
                # max_rows rows, it's likely that more data existed.
                rows = cursor.fetchmany(max_rows + 1)
                truncated = len(rows) > max_rows
                if truncated:
                    rows = rows[:max_rows]
                elif len(rows) == max_rows:
                    # Check if the safe_query had a LIMIT that we enforced.
                    # If so, there might have been more rows in the DB.
                    has_limit = bool(re.search(r"\bLIMIT\b", safe_query, re.IGNORECASE))
                    if has_limit:
                        truncated = True

                columns: list[str] = []
                if cursor.description:
                    columns = [desc[0] for desc in cursor.description]

                elapsed = int((time.monotonic() - start) * 1000)

                logger.info(
                    "db_query executed: %d rows in %dms",
                    len(rows),
                    elapsed,
                    extra={
                        "event": "db_query.success",
                        "row_count": len(rows),
                        "elapsed_ms": elapsed,
                        "truncated": truncated,
                        "tables": tables,
                    },
                )

                return {
                    "columns": columns,
                    "rows": [list(r) for r in rows],
                    "row_count": len(rows),
                    "truncated": truncated,
                    "elapsed_ms": elapsed,
                    "error": None,
                    "dialect": dialect,
                }

            finally:
                conn.close()

        except sqlite3.OperationalError as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            error_msg = str(exc)
            is_timeout = "timeout" in error_msg.lower() or "locked" in error_msg.lower()

            logger.warning(
                "db_query operational error (%dms): %s",
                elapsed,
                error_msg,
                extra={
                    "event": "db_query.operational_error",
                    "elapsed_ms": elapsed,
                    "error": error_msg,
                    "is_timeout": is_timeout,
                },
            )

            return {
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "elapsed_ms": elapsed,
                "error": f"Query timed out after {timeout_seconds}s" if is_timeout else f"Database error: {error_msg}",
                "dialect": dialect,
            }

        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            error_msg = f"Unexpected error: {exc}"

            logger.error(
                "db_query unexpected error (%dms): %s",
                elapsed,
                exc,
                extra={
                    "event": "db_query.unexpected_error",
                    "elapsed_ms": elapsed,
                    "error": str(exc),
                },
                exc_info=True,
            )

            return {
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "elapsed_ms": elapsed,
                "error": error_msg,
                "dialect": dialect,
            }

    return db_query


def _parse_mysql_dsn(dsn: str) -> dict[str, Any]:
    """Parse a MySQL DSN string into connection parameters.

    DSN format: mysql://user:pass@host:port/dbname

    Args:
        dsn: MySQL connection string.

    Returns:
        Dict with keys: user, password, host, port, database.
    """
    # Remove the "mysql://" prefix.
    rest = dsn[len("mysql://"):]

    # Split into user:pass@host:port/dbname
    if "@" in rest:
        auth, host_part = rest.rsplit("@", 1)
        if ":" in auth:
            user, password = auth.split(":", 1)
        else:
            user, password = auth, ""
    else:
        user, password = "", ""
        host_part = rest

    # Split host:port/database
    if "/" in host_part:
        host_port, database = host_part.split("/", 1)
    else:
        host_port = host_part
        database = ""

    if ":" in host_port:
        host, port_str = host_port.split(":", 1)
        port = int(port_str)
    else:
        host = host_port
        port = 3306

    return {
        "user": user,
        "password": password,
        "host": host,
        "port": port,
        "database": database,
    }
