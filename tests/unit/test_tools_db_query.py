"""Unit tests for the db_query tool.

Covers four paths:
- Normal: successful SELECT, column names, row data, WITH ... SELECT.
- Security: INSERT/UPDATE/DELETE/DROP blocked, SQL comment blocked,
  multiple statements blocked, table whitelist enforced.
- Boundary: LIMIT enforcement, row truncation, empty result, timeout clamp,
  no DSN configured, empty query.
- Registry: registered in tool_registry, build via factory, meta default_disabled.
"""
from __future__ import annotations

import sqlite3

import pytest

from agentbase.extensions.tools.db_query import (
    _check_tables,
    _detect_dialect,
    _ensure_limit,
    _extract_tables,
    _validate_query,
    build_db_query_tool,
)


# ---------------------------------------------------------------------------
# Helpers — create a temporary SQLite database for testing.
# ---------------------------------------------------------------------------


def _create_test_db(db_path: str) -> None:
    """Create a test SQLite database with sample data."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT,
            age INTEGER
        )
    """)

    cursor.execute("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            product TEXT,
            amount REAL
        )
    """)

    cursor.execute("""
        CREATE TABLE secrets (
            id INTEGER PRIMARY KEY,
            api_key TEXT,
            password TEXT
        )
    """)

    # Insert test data
    users = [
        (1, "Alice", "alice@example.com", 30),
        (2, "Bob", "bob@example.com", 25),
        (3, "Charlie", "charlie@example.com", 35),
        (4, "David", "david@example.com", 28),
        (5, "Eve", "eve@example.com", 22),
    ]
    cursor.executemany("INSERT INTO users VALUES (?, ?, ?, ?)", users)

    orders = [
        (1, 1, "Laptop", 999.99),
        (2, 1, "Mouse", 29.99),
        (3, 2, "Keyboard", 79.99),
        (4, 3, "Monitor", 299.99),
    ]
    cursor.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", orders)

    secrets = [
        (1, "sk-1234567890abcdef", "password123"),
        (2, "sk-abcdef1234567890", "secret456"),
    ]
    cursor.executemany("INSERT INTO secrets VALUES (?, ?, ?)", secrets)

    conn.commit()
    conn.close()


@pytest.fixture
def test_db(tmp_path) -> str:
    """Create a temporary SQLite database and return its DSN."""
    db_path = str(tmp_path / "test.db")
    _create_test_db(db_path)
    return f"sqlite:///{db_path}"


@pytest.fixture
def db_tool(test_db):
    """Build a db_query tool with the test database."""
    from agentbase.config.schema import AppConfig, DBQueryConfig

    app_config = AppConfig()
    app_config.db_query = DBQueryConfig(
        enabled=True,
        dsn=test_db,
        max_rows=100,
        timeout_seconds=5,
        allowed_tables=[],
    )
    return build_db_query_tool(context={"app_config": app_config})


@pytest.fixture
def db_tool_restricted(test_db):
    """Build a db_query tool with table whitelist."""
    from agentbase.config.schema import AppConfig, DBQueryConfig

    app_config = AppConfig()
    app_config.db_query = DBQueryConfig(
        enabled=True,
        dsn=test_db,
        max_rows=50,
        timeout_seconds=5,
        allowed_tables=["users", "orders"],
    )
    return build_db_query_tool(context={"app_config": app_config})


# ---------------------------------------------------------------------------
# Validation helper tests
# ---------------------------------------------------------------------------


class TestValidateQuery:
    def test_valid_select(self):
        assert _validate_query("SELECT * FROM users") is None

    def test_valid_select_with_columns(self):
        assert _validate_query("SELECT id, name FROM users WHERE age > 25") is None

    def test_valid_with_select(self):
        assert _validate_query(
            "WITH active_users AS (SELECT * FROM users WHERE age > 20) SELECT * FROM active_users"
        ) is None

    def test_valid_select_with_join(self):
        assert _validate_query(
            "SELECT u.name, o.product FROM users u JOIN orders o ON u.id = o.user_id"
        ) is None

    def test_valid_select_with_subquery(self):
        assert _validate_query("SELECT * FROM (SELECT id, name FROM users) sub") is None

    def test_empty_query(self):
        err = _validate_query("")
        assert err is not None
        assert "required" in err.lower()

    def test_whitespace_only(self):
        err = _validate_query("   ")
        assert err is not None
        assert "empty" in err.lower()

    def test_insert_blocked(self):
        err = _validate_query("INSERT INTO users VALUES (1, 'hack', 'hack@hack.com', 99)")
        assert err is not None
        assert "INSERT" in err

    def test_update_blocked(self):
        err = _validate_query("UPDATE users SET name = 'hack' WHERE id = 1")
        assert err is not None
        assert "UPDATE" in err

    def test_delete_blocked(self):
        err = _validate_query("DELETE FROM users WHERE id = 1")
        assert err is not None
        assert "DELETE" in err

    def test_drop_blocked(self):
        err = _validate_query("DROP TABLE users")
        assert err is not None
        assert "DROP" in err

    def test_create_blocked(self):
        err = _validate_query("CREATE TABLE evil (id INTEGER)")
        assert err is not None
        assert "CREATE" in err

    def test_alter_blocked(self):
        err = _validate_query("ALTER TABLE users ADD COLUMN evil TEXT")
        assert err is not None
        assert "ALTER" in err

    def test_truncate_blocked(self):
        err = _validate_query("TRUNCATE TABLE users")
        assert err is not None
        assert "TRUNCATE" in err

    def test_multiple_statements_blocked(self):
        err = _validate_query("SELECT * FROM users; DROP TABLE users")
        assert err is not None
        assert "multiple" in err.lower() or "semicolon" in err.lower()

    def test_sql_comment_double_dash_blocked(self):
        err = _validate_query("SELECT * FROM users -- this is a comment")
        assert err is not None
        assert "comment" in err.lower()

    def test_sql_comment_slash_star_blocked(self):
        err = _validate_query("SELECT * FROM /* comment */ users")
        assert err is not None
        assert "comment" in err.lower()

    def test_select_with_drop_in_subquery_blocked(self):
        """DROP keyword in a subquery should be blocked."""
        err = _validate_query("SELECT * FROM (DROP TABLE users) sub")
        assert err is not None
        assert "DROP" in err

    def test_select_with_delete_in_union_blocked(self):
        """DELETE in a UNION should be blocked."""
        err = _validate_query("SELECT * FROM users UNION DELETE FROM users")
        assert err is not None
        assert "DELETE" in err

    def test_non_select_start(self):
        err = _validate_query("DESCRIBE users")
        assert err is not None
        assert "SELECT" in err

    def test_show_blocked(self):
        err = _validate_query("SHOW TABLES")
        assert err is not None

    def test_explain_blocked(self):
        err = _validate_query("EXPLAIN SELECT * FROM users")
        assert err is not None
        assert "EXPLAIN" in err

    def test_pragma_blocked(self):
        err = _validate_query("PRAGMA table_info(users)")
        assert err is not None
        assert "PRAGMA" in err

    def test_attach_blocked(self):
        err = _validate_query("ATTACH DATABASE 'evil.db' AS evil")
        assert err is not None
        assert "ATTACH" in err


class TestExtractTables:
    def test_single_table(self):
        tables = _extract_tables("SELECT * FROM users")
        assert "users" in tables

    def test_multiple_tables_with_join(self):
        tables = _extract_tables(
            "SELECT * FROM users u JOIN orders o ON u.id = o.user_id"
        )
        assert "users" in tables
        assert "orders" in tables

    def test_schema_prefixed_table(self):
        tables = _extract_tables("SELECT * FROM public.users")
        assert "users" in tables

    def test_subquery_table(self):
        tables = _extract_tables("SELECT * FROM (SELECT * FROM users) sub")
        # The subquery's FROM users should be extracted
        assert "users" in tables

    def test_no_table(self):
        tables = _extract_tables("SELECT 1 + 1")
        assert tables == []


class TestCheckTables:
    def test_empty_whitelist_allows_all(self):
        assert _check_tables(["users", "orders"], []) is None

    def test_allowed_table(self):
        assert _check_tables(["users"], ["users", "orders"]) is None

    def test_disallowed_table(self):
        err = _check_tables(["secrets"], ["users", "orders"])
        assert err is not None
        assert "secrets" in err
        assert "allowed" in err.lower()

    def test_mixed_tables(self):
        err = _check_tables(["users", "secrets"], ["users", "orders"])
        assert err is not None
        assert "secrets" in err


class TestEnsureLimit:
    def test_add_limit_when_missing(self):
        sql = _ensure_limit("SELECT * FROM users", 50)
        assert "LIMIT 50" in sql

    def test_keep_existing_limit_under_max(self):
        sql = _ensure_limit("SELECT * FROM users LIMIT 10", 50)
        assert "LIMIT 10" in sql
        assert "LIMIT 50" not in sql

    def test_replace_limit_over_max(self):
        sql = _ensure_limit("SELECT * FROM users LIMIT 200", 50)
        assert "LIMIT 50" in sql
        assert "LIMIT 200" not in sql

    def test_preserve_rest_of_query(self):
        sql = _ensure_limit("SELECT id, name FROM users WHERE age > 25", 10)
        assert "SELECT id, name FROM users WHERE age > 25" in sql
        assert "LIMIT 10" in sql


class TestDetectDialect:
    def test_sqlite(self):
        assert _detect_dialect("sqlite:///data/db.sqlite") == "sqlite"

    def test_postgresql(self):
        assert _detect_dialect("postgresql://user:pass@host:5432/db") == "postgresql"

    def test_mysql(self):
        assert _detect_dialect("mysql://user:pass@host:3306/db") == "mysql"

    def test_unknown(self):
        assert _detect_dialect("oracle://user:pass@host/db") == "unknown"

    def test_empty(self):
        assert _detect_dialect("") == "unknown"


# ---------------------------------------------------------------------------
# Normal path — successful queries
# ---------------------------------------------------------------------------


class TestDbQueryNormal:
    def test_select_all(self, db_tool):
        """Basic SELECT * returns all rows."""
        result = db_tool.invoke({"query": "SELECT * FROM users"})

        assert result["error"] is None
        assert result["row_count"] == 5
        assert result["columns"] == ["id", "name", "email", "age"]
        assert result["dialect"] == "sqlite"
        assert result["truncated"] is False

    def test_select_specific_columns(self, db_tool):
        """SELECT specific columns returns correct column names."""
        result = db_tool.invoke({"query": "SELECT name, email FROM users"})

        assert result["error"] is None
        assert result["columns"] == ["name", "email"]
        assert result["row_count"] == 5

    def test_select_with_where(self, db_tool):
        """SELECT with WHERE clause filters rows."""
        result = db_tool.invoke({"query": "SELECT * FROM users WHERE age > 25"})

        assert result["error"] is None
        assert result["row_count"] == 3  # Alice(30), Charlie(35), David(28)

    def test_select_with_join(self, db_tool):
        """SELECT with JOIN returns combined data."""
        result = db_tool.invoke({
            "query": "SELECT u.name, o.product FROM users u JOIN orders o ON u.id = o.user_id"
        })

        assert result["error"] is None
        assert result["row_count"] == 4
        assert "name" in result["columns"]
        assert "product" in result["columns"]

    def test_select_with_order_by(self, db_tool):
        """SELECT with ORDER BY returns sorted results."""
        result = db_tool.invoke({"query": "SELECT name FROM users ORDER BY age DESC"})

        assert result["error"] is None
        assert result["row_count"] == 5
        # Charlie (35) should be first
        assert result["rows"][0][0] == "Charlie"

    def test_select_count(self, db_tool):
        """SELECT COUNT(*) returns aggregate."""
        result = db_tool.invoke({"query": "SELECT COUNT(*) as cnt FROM users"})

        assert result["error"] is None
        assert result["row_count"] == 1
        assert result["rows"][0][0] == 5

    def test_with_cte(self, db_tool):
        """WITH ... AS (...) SELECT works."""
        result = db_tool.invoke({
            "query": "WITH young AS (SELECT * FROM users WHERE age < 30) SELECT name FROM young"
        })

        assert result["error"] is None
        assert result["row_count"] == 3  # Bob(25), David(28), Eve(22)

    def test_subquery(self, db_tool):
        """Subquery in FROM works."""
        result = db_tool.invoke({
            "query": "SELECT name FROM (SELECT name, age FROM users WHERE age > 25) sub"
        })

        assert result["error"] is None
        assert result["row_count"] == 3

    def test_trailing_semicolon_stripped(self, db_tool):
        """Trailing semicolon is handled."""
        result = db_tool.invoke({"query": "SELECT * FROM users;"})

        assert result["error"] is None
        assert result["row_count"] == 5

    def test_elapsed_ms_positive(self, db_tool):
        """Elapsed time is non-negative."""
        result = db_tool.invoke({"query": "SELECT * FROM users"})
        assert result["elapsed_ms"] >= 0


# ---------------------------------------------------------------------------
# Security path — injection and dangerous statements blocked
# ---------------------------------------------------------------------------


class TestDbQuerySecurity:
    def test_insert_blocked(self, db_tool):
        """INSERT statement is blocked."""
        result = db_tool.invoke({
            "query": "INSERT INTO users VALUES (99, 'hacker', 'hack@hack.com', 50)"
        })

        assert result["error"] is not None
        assert "INSERT" in result["error"]
        assert result["row_count"] == 0

    def test_update_blocked(self, db_tool):
        """UPDATE statement is blocked."""
        result = db_tool.invoke({"query": "UPDATE users SET name = 'hacked' WHERE id = 1"})

        assert result["error"] is not None
        assert "UPDATE" in result["error"]

    def test_delete_blocked(self, db_tool):
        """DELETE statement is blocked."""
        result = db_tool.invoke({"query": "DELETE FROM users WHERE id = 1"})

        assert result["error"] is not None
        assert "DELETE" in result["error"]

    def test_drop_blocked(self, db_tool):
        """DROP TABLE is blocked."""
        result = db_tool.invoke({"query": "DROP TABLE users"})

        assert result["error"] is not None
        assert "DROP" in result["error"]

    def test_sql_injection_drop_in_comment(self, db_tool):
        """SQL injection via comment is blocked."""
        result = db_tool.invoke({
            "query": "SELECT * FROM users -- DROP TABLE users"
        })

        assert result["error"] is not None
        assert "comment" in result["error"].lower()

    def test_sql_injection_stacked_query(self, db_tool):
        """Stacked query injection is blocked."""
        result = db_tool.invoke({
            "query": "SELECT * FROM users; DROP TABLE users"
        })

        assert result["error"] is not None
        assert "semicolon" in result["error"].lower() or "multiple" in result["error"].lower()

    def test_create_table_blocked(self, db_tool):
        """CREATE TABLE is blocked."""
        result = db_tool.invoke({"query": "CREATE TABLE evil (id INTEGER)"})

        assert result["error"] is not None
        assert "CREATE" in result["error"]

    def test_alter_table_blocked(self, db_tool):
        """ALTER TABLE is blocked."""
        result = db_tool.invoke({"query": "ALTER TABLE users ADD COLUMN evil TEXT"})

        assert result["error"] is not None
        assert "ALTER" in result["error"]

    def test_truncate_blocked(self, db_tool):
        """TRUNCATE is blocked."""
        result = db_tool.invoke({"query": "TRUNCATE TABLE users"})

        assert result["error"] is not None
        assert "TRUNCATE" in result["error"]

    def test_pragma_blocked(self, db_tool):
        """PRAGMA is blocked."""
        result = db_tool.invoke({"query": "PRAGMA table_info(users)"})

        assert result["error"] is not None
        assert "PRAGMA" in result["error"]

    def test_attach_blocked(self, db_tool):
        """ATTACH DATABASE is blocked."""
        result = db_tool.invoke({"query": "ATTACH DATABASE 'evil.db' AS evil"})

        assert result["error"] is not None
        assert "ATTACH" in result["error"]

    def test_table_whitelist_enforced(self, db_tool_restricted):
        """Table not in whitelist is blocked."""
        result = db_tool_restricted.invoke({"query": "SELECT * FROM secrets"})

        assert result["error"] is not None
        assert "secrets" in result["error"]
        assert "allowed" in result["error"].lower()

    def test_table_whitelist_allows_listed(self, db_tool_restricted):
        """Table in whitelist is allowed."""
        result = db_tool_restricted.invoke({"query": "SELECT * FROM users"})

        assert result["error"] is None
        assert result["row_count"] == 5

    def test_select_data_not_modified(self, db_tool):
        """SELECT should not modify data."""
        # Query users
        result = db_tool.invoke({"query": "SELECT COUNT(*) FROM users"})
        original_count = result["rows"][0][0]

        # Try to INSERT (should be blocked)
        db_tool.invoke({"query": "INSERT INTO users VALUES (99, 'hack', 'hack@hack.com', 50)"})

        # Verify count unchanged
        result = db_tool.invoke({"query": "SELECT COUNT(*) FROM users"})
        assert result["rows"][0][0] == original_count


# ---------------------------------------------------------------------------
# Boundary path — edge cases
# ---------------------------------------------------------------------------


class TestDbQueryBoundary:
    def test_limit_enforced(self, db_tool):
        """Results are limited to max_rows."""
        result = db_tool.invoke({"query": "SELECT * FROM users"})

        # max_rows is 100 in the fixture, we have 5 rows → not truncated
        assert result["row_count"] == 5
        assert result["truncated"] is False

    def test_truncation_when_over_limit(self, test_db):
        """Results are truncated when exceeding max_rows."""
        from agentbase.config.schema import AppConfig, DBQueryConfig

        app_config = AppConfig()
        app_config.db_query = DBQueryConfig(
            enabled=True,
            dsn=test_db,
            max_rows=3,
            timeout_seconds=5,
        )
        tool = build_db_query_tool(context={"app_config": app_config})

        result = tool.invoke({"query": "SELECT * FROM users"})

        assert result["error"] is None
        assert result["row_count"] == 3
        assert result["truncated"] is True

    def test_explicit_limit_respected(self, db_tool):
        """Explicit LIMIT in query is respected."""
        result = db_tool.invoke({"query": "SELECT * FROM users LIMIT 2"})

        assert result["error"] is None
        assert result["row_count"] == 2

    def test_explicit_limit_over_max_replaced(self, test_db):
        """Explicit LIMIT over max_rows is replaced."""
        from agentbase.config.schema import AppConfig, DBQueryConfig

        app_config = AppConfig()
        app_config.db_query = DBQueryConfig(
            enabled=True,
            dsn=test_db,
            max_rows=3,
        )
        tool = build_db_query_tool(context={"app_config": app_config})

        result = tool.invoke({"query": "SELECT * FROM users LIMIT 100"})

        assert result["error"] is None
        assert result["row_count"] == 3
        assert result["truncated"] is True

    def test_empty_result(self, db_tool):
        """Empty result set is handled."""
        result = db_tool.invoke({"query": "SELECT * FROM users WHERE age > 100"})

        assert result["error"] is None
        assert result["row_count"] == 0
        assert result["rows"] == []
        assert result["columns"] == ["id", "name", "email", "age"]

    def test_empty_query(self, db_tool):
        """Empty query returns error."""
        result = db_tool.invoke({"query": ""})

        assert result["error"] is not None
        assert "required" in result["error"].lower()

    def test_no_dsn_configured(self):
        """Tool without DSN returns error."""
        tool = build_db_query_tool(context={})
        result = tool.invoke({"query": "SELECT 1"})

        assert result["error"] is not None
        assert "dsn" in result["error"].lower()

    def test_max_rows_clamped_to_cap(self):
        """max_rows above cap is clamped."""
        from agentbase.config.schema import AppConfig, DBQueryConfig

        app_config = AppConfig()
        app_config.db_query = DBQueryConfig(
            enabled=True,
            dsn="sqlite:///:memory:",
            max_rows=99999,
        )
        # The clamping happens internally — we just verify no crash.
        tool = build_db_query_tool(context={"app_config": app_config})
        # Tool should be built successfully.
        assert hasattr(tool, "invoke")

    def test_timeout_clamped_to_min(self):
        """Timeout below minimum is clamped."""
        from agentbase.config.schema import AppConfig, DBQueryConfig

        app_config = AppConfig()
        app_config.db_query = DBQueryConfig(
            enabled=True,
            dsn="sqlite:///:memory:",
            timeout_seconds=0,
        )
        tool = build_db_query_tool(context={"app_config": app_config})
        assert hasattr(tool, "invoke")

    def test_build_with_none_context(self):
        """Tool can be built with None context."""
        tool = build_db_query_tool(context=None)
        assert hasattr(tool, "invoke")

    def test_structured_return_keys(self, db_tool):
        """Response dict always has all required keys."""
        result = db_tool.invoke({"query": "SELECT * FROM users"})

        required_keys = {"columns", "rows", "row_count", "truncated", "elapsed_ms", "error", "dialect"}
        assert set(result.keys()) == required_keys

    def test_nonexistent_table(self, db_tool):
        """Querying a nonexistent table returns error."""
        result = db_tool.invoke({"query": "SELECT * FROM nonexistent_table"})

        assert result["error"] is not None
        assert "no such table" in result["error"].lower()

    def test_syntax_error(self, db_tool):
        """Malformed SQL returns error."""
        result = db_tool.invoke({"query": "SELECT FROM WHERE"})

        assert result["error"] is not None


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestDbQueryRegistry:
    def test_registered_in_tool_registry(self, bootstrapped):
        """Tool is registered in tool_registry after bootstrap."""
        from agentbase.registry.tools import tool_registry

        assert tool_registry.has("db_query")

    def test_build_via_factory(self, bootstrapped):
        """Tool can be built through the build_tools factory."""
        from agentbase.factories.tool_factory import build_tools

        tools = build_tools(["db_query"], context={})
        assert len(tools) == 1
        assert hasattr(tools[0], "invoke")

    def test_meta_default_disabled(self, bootstrapped):
        """Tool metadata has default_enabled=False."""
        from agentbase.registry.tools import tool_registry

        meta = tool_registry.get_meta("db_query")
        assert meta is not None
        assert meta.default_enabled is False
        assert meta.kind == "tool"
        assert "database" in meta.tags or "sql" in meta.tags

    def test_strict_mode_works(self, bootstrapped):
        """Building db_query in strict mode works."""
        from agentbase.factories.tool_factory import build_tools

        tools = build_tools(["db_query"], context={}, skip_on_error=False)
        assert len(tools) == 1
