"""Unit tests for database migration service (core.migration).

Covers:
- MigrationConfig (schema.py) defaults and configuration
- _storage_url_to_sqlalchemy — URL conversion for SQLite/PostgreSQL/MySQL/MongoDB
- MigrationManager — init, upgrade, downgrade, current, heads, history, stamp
- init_scripts — directory structure creation
- Disabled manager — all operations are no-ops
- Real SQLite migration — upgrade creates tables, downgrade drops them
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentbase.config.schema import AppConfig, MigrationConfig


# ---------------------------------------------------------------------------
# MigrationConfig (schema)
# ---------------------------------------------------------------------------

class TestMigrationConfig:
    def test_defaults(self):
        cfg = MigrationConfig()
        assert cfg.enabled is True
        assert cfg.scripts_dir == "migrations"

    def test_custom(self):
        cfg = MigrationConfig(enabled=False, scripts_dir="custom_migrations")
        assert cfg.enabled is False
        assert cfg.scripts_dir == "custom_migrations"

    def test_in_app_config(self):
        cfg = AppConfig()
        assert hasattr(cfg, "migration")
        assert cfg.migration.enabled is True
        assert cfg.migration.scripts_dir == "migrations"

    def test_app_config_custom_migration(self):
        cfg = AppConfig()
        cfg.migration.enabled = False
        cfg.migration.scripts_dir = "my_migrations"
        assert cfg.migration.enabled is False
        assert cfg.migration.scripts_dir == "my_migrations"


# ---------------------------------------------------------------------------
# _storage_url_to_sqlalchemy
# ---------------------------------------------------------------------------

class TestStorageUrlConversion:
    def test_sqlite_url(self, tmp_path):
        from agentbase.core.migration import _storage_url_to_sqlalchemy

        url = _storage_url_to_sqlalchemy(
            storage_type="sqlite",
            db_dir=str(tmp_path / "data"),
            dsn=None,
        )
        assert url.startswith("sqlite:///")
        assert "agentbase.db" in url

    def test_sqlite_creates_db_dir(self, tmp_path):
        from agentbase.core.migration import _storage_url_to_sqlalchemy

        db_dir = tmp_path / "nested" / "db"
        _storage_url_to_sqlalchemy(
            storage_type="sqlite",
            db_dir=str(db_dir),
            dsn=None,
        )
        assert db_dir.exists()

    def test_postgres_url_with_standard_dsn(self):
        from agentbase.core.migration import _storage_url_to_sqlalchemy

        url = _storage_url_to_sqlalchemy(
            storage_type="postgres",
            db_dir=None,
            dsn="postgresql://user:pass@host:5432/db",
        )
        assert "postgresql+psycopg://" in url

    def test_postgres_url_with_already_qualified_dsn(self):
        from agentbase.core.migration import _storage_url_to_sqlalchemy

        url = _storage_url_to_sqlalchemy(
            storage_type="postgres",
            db_dir=None,
            dsn="postgresql+psycopg://user:pass@host:5432/db",
        )
        assert url == "postgresql+psycopg://user:pass@host:5432/db"

    def test_postgres_missing_dsn_raises(self):
        from agentbase.core.migration import _storage_url_to_sqlalchemy
        from agentbase.runtime.errors import AgentbaseError

        with pytest.raises(AgentbaseError):
            _storage_url_to_sqlalchemy(
                storage_type="postgres",
                db_dir=None,
                dsn=None,
            )

    def test_mysql_url(self):
        from agentbase.core.migration import _storage_url_to_sqlalchemy

        url = _storage_url_to_sqlalchemy(
            storage_type="mysql",
            db_dir=None,
            dsn="mysql://user:pass@host:3306/db",
        )
        assert url == "mysql://user:pass@host:3306/db"

    def test_mysql_missing_dsn_raises(self):
        from agentbase.core.migration import _storage_url_to_sqlalchemy
        from agentbase.runtime.errors import AgentbaseError

        with pytest.raises(AgentbaseError):
            _storage_url_to_sqlalchemy(
                storage_type="mysql",
                db_dir=None,
                dsn=None,
            )

    def test_mongodb_not_supported(self):
        from agentbase.core.migration import _storage_url_to_sqlalchemy
        from agentbase.runtime.errors import AgentbaseError

        with pytest.raises(AgentbaseError):
            _storage_url_to_sqlalchemy(
                storage_type="mongodb",
                db_dir=None,
                dsn=None,
            )


# ---------------------------------------------------------------------------
# MigrationManager — disabled
# ---------------------------------------------------------------------------

class TestMigrationManagerDisabled:
    def test_disabled_manager_properties(self, tmp_path):
        from agentbase.core.migration import MigrationManager

        mgr = MigrationManager(
            scripts_dir=tmp_path / "migrations",
            db_url="sqlite:///test.db",
            enabled=False,
        )
        assert mgr.enabled is False
        assert mgr.scripts_dir == tmp_path / "migrations"
        assert mgr.db_url == "sqlite:///test.db"

    def test_disabled_upgrade_is_noop(self, tmp_path):
        from agentbase.core.migration import MigrationManager

        mgr = MigrationManager(
            scripts_dir=tmp_path / "migrations",
            db_url="sqlite:///test.db",
            enabled=False,
        )
        result = mgr.upgrade()
        assert result is None

    def test_disabled_downgrade_is_noop(self, tmp_path):
        from agentbase.core.migration import MigrationManager

        mgr = MigrationManager(
            scripts_dir=tmp_path / "migrations",
            db_url="sqlite:///test.db",
            enabled=False,
        )
        result = mgr.downgrade()
        assert result is None

    def test_disabled_current_returns_none(self, tmp_path):
        from agentbase.core.migration import MigrationManager

        mgr = MigrationManager(
            scripts_dir=tmp_path / "migrations",
            db_url="sqlite:///test.db",
            enabled=False,
        )
        assert mgr.current() is None

    def test_disabled_heads_returns_empty(self, tmp_path):
        from agentbase.core.migration import MigrationManager

        mgr = MigrationManager(
            scripts_dir=tmp_path / "migrations",
            db_url="sqlite:///test.db",
            enabled=False,
        )
        assert mgr.heads() == []

    def test_disabled_history_returns_empty(self, tmp_path):
        from agentbase.core.migration import MigrationManager

        mgr = MigrationManager(
            scripts_dir=tmp_path / "migrations",
            db_url="sqlite:///test.db",
            enabled=False,
        )
        assert mgr.history() == []

    def test_disabled_stamp_is_noop(self, tmp_path):
        from agentbase.core.migration import MigrationManager

        mgr = MigrationManager(
            scripts_dir=tmp_path / "migrations",
            db_url="sqlite:///test.db",
            enabled=False,
        )
        assert mgr.stamp() is None

    def test_disabled_init_scripts_is_noop(self, tmp_path):
        from agentbase.core.migration import MigrationManager

        mgr = MigrationManager(
            scripts_dir=tmp_path / "migrations",
            db_url="sqlite:///test.db",
            enabled=False,
        )
        mgr.init_scripts()
        # Should not have created anything
        assert not (tmp_path / "migrations" / "env.py").exists()


# ---------------------------------------------------------------------------
# MigrationManager — init_scripts
# ---------------------------------------------------------------------------

class TestInitScripts:
    def test_init_scripts_creates_dirs(self, tmp_path):
        from agentbase.core.migration import MigrationManager

        scripts_dir = tmp_path / "migrations"
        mgr = MigrationManager(
            scripts_dir=scripts_dir,
            db_url="sqlite:///test.db",
            enabled=True,
            skip_dir_check=True,
        )
        mgr.init_scripts()

        assert scripts_dir.exists()
        assert (scripts_dir / "versions").exists()
        assert (scripts_dir / "env.py").exists()
        assert (scripts_dir / "script.py.mako").exists()

    def test_init_scripts_env_py_content(self, tmp_path):
        from agentbase.core.migration import MigrationManager

        scripts_dir = tmp_path / "migrations"
        mgr = MigrationManager(
            scripts_dir=scripts_dir,
            db_url="sqlite:///test.db",
            enabled=True,
            skip_dir_check=True,
        )
        mgr.init_scripts()

        env_content = (scripts_dir / "env.py").read_text(encoding="utf-8")
        assert "alembic" in env_content
        assert "run_migrations_online" in env_content

    def test_init_scripts_mako_content(self, tmp_path):
        from agentbase.core.migration import MigrationManager

        scripts_dir = tmp_path / "migrations"
        mgr = MigrationManager(
            scripts_dir=scripts_dir,
            db_url="sqlite:///test.db",
            enabled=True,
            skip_dir_check=True,
        )
        mgr.init_scripts()

        mako_content = (scripts_dir / "script.py.mako").read_text(encoding="utf-8")
        assert "revision" in mako_content
        assert "upgrade" in mako_content
        assert "downgrade" in mako_content

    def test_init_scripts_idempotent(self, tmp_path):
        from agentbase.core.migration import MigrationManager

        scripts_dir = tmp_path / "migrations"
        mgr = MigrationManager(
            scripts_dir=scripts_dir,
            db_url="sqlite:///test.db",
            enabled=True,
            skip_dir_check=True,
        )
        mgr.init_scripts()
        env1 = (scripts_dir / "env.py").read_text(encoding="utf-8")
        mgr.init_scripts()
        env2 = (scripts_dir / "env.py").read_text(encoding="utf-8")
        assert env1 == env2


# ---------------------------------------------------------------------------
# MigrationManager — ensure_scripts_dir
# ---------------------------------------------------------------------------

class TestEnsureScriptsDir:
    def test_missing_scripts_dir_raises(self, tmp_path):
        from agentbase.core.migration import MigrationManager
        from agentbase.runtime.errors import AgentbaseError

        with pytest.raises(AgentbaseError):
            MigrationManager(
                scripts_dir=tmp_path / "nonexistent",
                db_url="sqlite:///test.db",
                enabled=True,
            )

    def test_missing_versions_dir_raises(self, tmp_path):
        from agentbase.core.migration import MigrationManager
        from agentbase.runtime.errors import AgentbaseError

        scripts_dir = tmp_path / "migrations"
        scripts_dir.mkdir()
        # versions/ not created
        with pytest.raises(AgentbaseError):
            MigrationManager(
                scripts_dir=scripts_dir,
                db_url="sqlite:///test.db",
                enabled=True,
            )

    def test_existing_scripts_dir_ok(self, tmp_path):
        from agentbase.core.migration import MigrationManager

        scripts_dir = tmp_path / "migrations"
        (scripts_dir / "versions").mkdir(parents=True)
        mgr = MigrationManager(
            scripts_dir=scripts_dir,
            db_url="sqlite:///test.db",
            enabled=True,
        )
        assert mgr.enabled is True


# ---------------------------------------------------------------------------
# create_migration_manager
# ---------------------------------------------------------------------------

class TestCreateMigrationManager:
    def test_create_from_sqlite_config(self, tmp_path):
        from agentbase.core.migration import create_migration_manager

        # Create the migrations directory structure required by the manager
        scripts_dir = tmp_path / "migrations"
        (scripts_dir / "versions").mkdir(parents=True, exist_ok=True)

        mgr = create_migration_manager(
            storage_type="sqlite",
            db_dir=str(tmp_path / "data"),
            dsn=None,
            scripts_dir=str(scripts_dir),
            enabled=True,
        )
        assert mgr.enabled is True
        assert "sqlite:///" in mgr.db_url

    def test_create_disabled(self, tmp_path):
        from agentbase.core.migration import create_migration_manager

        mgr = create_migration_manager(
            storage_type="sqlite",
            db_dir=str(tmp_path / "data"),
            dsn=None,
            scripts_dir=str(tmp_path / "migrations"),
            enabled=False,
        )
        assert mgr.enabled is False

    def test_create_mongodb_raises(self, tmp_path):
        from agentbase.core.migration import create_migration_manager
        from agentbase.runtime.errors import AgentbaseError

        with pytest.raises(AgentbaseError):
            create_migration_manager(
                storage_type="mongodb",
                db_dir=None,
                dsn=None,
                scripts_dir=str(tmp_path / "migrations"),
            )


# ---------------------------------------------------------------------------
# Real SQLite migration — full upgrade/downgrade cycle
# ---------------------------------------------------------------------------

class TestRealMigration:
    """Run actual Alembic migrations against a temp SQLite database."""

    @pytest.fixture
    def migration_setup(self, tmp_path):
        """Set up a migrations directory with the base schema migration."""
        # Copy the project's migrations directory to tmp
        project_root = Path(__file__).resolve().parents[2]
        src_migrations = project_root / "migrations"

        dst_migrations = tmp_path / "migrations"
        dst_migrations.mkdir(parents=True, exist_ok=True)

        # Copy env.py
        env_src = src_migrations / "env.py"
        if env_src.exists():
            (dst_migrations / "env.py").write_text(
                env_src.read_text(encoding="utf-8"), encoding="utf-8"
            )

        # Copy script.py.mako
        mako_src = src_migrations / "script.py.mako"
        if mako_src.exists():
            (dst_migrations / "script.py.mako").write_text(
                mako_src.read_text(encoding="utf-8"), encoding="utf-8"
            )

        # Copy versions
        src_versions = src_migrations / "versions"
        dst_versions = dst_migrations / "versions"
        dst_versions.mkdir(exist_ok=True)
        for py_file in src_versions.glob("*.py"):
            (dst_versions / py_file.name).write_text(
                py_file.read_text(encoding="utf-8"), encoding="utf-8"
            )

        db_path = tmp_path / "data" / "test.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite:///{db_path.as_posix()}"

        from agentbase.core.migration import MigrationManager

        mgr = MigrationManager(
            scripts_dir=dst_migrations,
            db_url=db_url,
            enabled=True,
        )
        return mgr, db_path

    def test_upgrade_creates_tables(self, migration_setup):
        mgr, db_path = migration_setup
        mgr.upgrade()
        assert db_path.exists()

        # Verify tables exist by querying
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "memories" in tables
        assert "kb_documents" in tables
        assert "kb_chunks" in tables
        assert "audit_events" in tables
        # Alembic's version table
        assert "alembic_version" in tables

    def test_current_after_upgrade(self, migration_setup):
        mgr, _ = migration_setup
        mgr.upgrade()
        current = mgr.current()
        assert current is not None

    def test_heads(self, migration_setup):
        mgr, _ = migration_setup
        heads = mgr.heads()
        assert len(heads) >= 1

    def test_history(self, migration_setup):
        mgr, _ = migration_setup
        history = mgr.history()
        assert len(history) >= 1

    def test_downgrade_drops_tables(self, migration_setup):
        mgr, db_path = migration_setup
        mgr.upgrade()

        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables_before = {row[0] for row in cursor.fetchall()}
        assert "memories" in tables_before

        mgr.downgrade()

        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables_after = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "memories" not in tables_after
        assert "kb_documents" not in tables_after
        assert "kb_chunks" not in tables_after
        assert "audit_events" not in tables_after

    def test_upgrade_creates_indexes(self, migration_setup):
        mgr, db_path = migration_setup
        mgr.upgrade()

        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
        )
        indexes = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "idx_mem_agent" in indexes
        assert "idx_mem_tags" in indexes
        assert "idx_chunks_doc" in indexes
        assert "idx_audit_actor" in indexes
        assert "idx_audit_action" in indexes
        assert "idx_audit_timestamp" in indexes

    def test_stamp(self, migration_setup):
        mgr, db_path = migration_setup
        mgr.stamp("0001_base_schema")

        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT version_num FROM alembic_version")
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "0001_base_schema"

    def test_upgrade_then_upgrade_again_is_idempotent(self, migration_setup):
        mgr, _ = migration_setup
        mgr.upgrade()
        # Second upgrade should be a no-op (already at head)
        mgr.upgrade()


# ---------------------------------------------------------------------------
# ErrorCode for migrations
# ---------------------------------------------------------------------------

class TestMigrationErrorCodes:
    def test_error_codes_exist(self):
        from agentbase.runtime.errors import ErrorCode

        assert hasattr(ErrorCode, "MIGRATION_FAILED")
        assert hasattr(ErrorCode, "MIGRATION_SCRIPTS_MISSING")
        assert ErrorCode.MIGRATION_FAILED == "AGENTBASE_MIGRATION_001"
        assert ErrorCode.MIGRATION_SCRIPTS_MISSING == "AGENTBASE_MIGRATION_002"

    def test_http_status_mapping(self):
        from agentbase.runtime.errors import ErrorCode, http_status_for_code

        assert http_status_for_code(ErrorCode.MIGRATION_FAILED) == 500
        assert http_status_for_code(ErrorCode.MIGRATION_SCRIPTS_MISSING) == 500
