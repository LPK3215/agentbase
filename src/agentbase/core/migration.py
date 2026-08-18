"""Database migration service — Alembic-based schema versioning.

Provides programmatic access to Alembic migration operations without
requiring the ``alembic`` CLI. Supports SQLite and PostgreSQL backends.

Usage::

    from agentbase.core.migration import MigrationManager

    mgr = MigrationManager(
        scripts_dir=Path("migrations"),
        db_url="sqlite:///data/agentbase.db",
    )
    mgr.upgrade()      # upgrade to latest
    mgr.current()      # current revision
    mgr.downgrade()    # downgrade one step
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agentbase.runtime.errors import AgentbaseError, ErrorCode
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


def _ensure_alembic():
    """Import alembic lazily; raise helpful error if not installed."""
    try:
        import alembic  # noqa: F401
    except ImportError as exc:
        raise AgentbaseError(
            "Alembic is not installed. Install with: pip install alembic",
            code=ErrorCode.FACTORY_DEPENDENCY_MISSING,
        ) from exc


def _storage_url_to_sqlalchemy(
    *,
    storage_type: str,
    db_dir: str | None,
    dsn: str | None,
) -> str:
    """Convert AgentBase storage config to a SQLAlchemy URL.

    - ``sqlite`` → ``sqlite:///path/to/db.sqlite``
    - ``postgres`` → uses DSN directly (converts ``postgresql://`` → ``postgresql+psycopg://``)
    - ``mysql`` → uses DSN directly
    - ``mongodb`` → raises (not supported)
    """
    if storage_type == "sqlite":
        db_path = Path(db_dir or "data") / "agentbase.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Convert to forward slashes for SQLAlchemy URL compatibility
        return f"sqlite:///{db_path.as_posix()}"
    elif storage_type == "postgres":
        if not dsn:
            raise AgentbaseError(
                "PostgreSQL storage requires 'storage.dsn' to be set",
                code=ErrorCode.CONFIG_INVALID,
            )
        # Convert postgresql:// to postgresql+psycopg:// for SQLAlchemy
        if dsn.startswith("postgresql://"):
            return dsn.replace("postgresql://", "postgresql+psycopg://", 1)
        return dsn
    elif storage_type == "mysql":
        if not dsn:
            raise AgentbaseError(
                "MySQL storage requires 'storage.dsn' to be set",
                code=ErrorCode.CONFIG_INVALID,
            )
        return dsn
    else:
        raise AgentbaseError(
            f"Migration not supported for storage type: {storage_type}",
            code=ErrorCode.CONFIG_INVALID,
        )


class MigrationManager:
    """Manages database schema migrations via Alembic.

    This class wraps Alembic's Python API to provide a simple,
    programmatic interface for running migrations. It generates
    the Alembic configuration dynamically (no external alembic.ini
    file needed).
    """

    def __init__(
        self,
        *,
        scripts_dir: Path,
        db_url: str,
        enabled: bool = True,
        skip_dir_check: bool = False,
    ) -> None:
        self._scripts_dir = Path(scripts_dir)
        self._db_url = db_url
        self._enabled = enabled

        if not self._enabled:
            logger.info("Database migration is disabled (migration.enabled=false)")
            return

        _ensure_alembic()
        if not skip_dir_check:
            self._ensure_scripts_dir()

    def _ensure_scripts_dir(self) -> None:
        """Ensure the migrations directory exists with proper structure."""
        if not self._scripts_dir.exists():
            raise AgentbaseError(
                f"Migration scripts directory does not exist: {self._scripts_dir}. "
                "Run 'agentbase db init' to create it.",
                code=ErrorCode.CONFIG_INVALID,
            )
        versions_dir = self._scripts_dir / "versions"
        if not versions_dir.exists():
            raise AgentbaseError(
                f"Migration versions directory does not exist: {versions_dir}. "
                "Run 'agentbase db init' to create it.",
                code=ErrorCode.CONFIG_INVALID,
            )

    def _make_config(self) -> Any:
        """Create an Alembic Config object dynamically."""
        from alembic.config import Config

        cfg = Config()
        cfg.set_main_option("script_location", str(self._scripts_dir))
        cfg.set_main_option("sqlalchemy.url", self._db_url)
        # Don't prompt for anything
        cfg.set_main_option("timezone", "UTC")
        return cfg

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def scripts_dir(self) -> Path:
        return self._scripts_dir

    @property
    def db_url(self) -> str:
        return self._db_url

    def upgrade(self, revision: str = "head") -> Any:
        """Upgrade to the specified revision (default: latest).

        Returns the Alembic command result.
        """
        if not self._enabled:
            logger.info("Migration disabled, skipping upgrade")
            return None
        from alembic import command

        cfg = self._make_config()
        logger.info("Upgrading database to revision: %s", revision)
        command.upgrade(cfg, revision)
        logger.info("Database upgrade complete")
        return None

    def downgrade(self, revision: str = "-1") -> Any:
        """Downgrade by the specified revision (default: one step back).

        Returns the Alembic command result.
        """
        if not self._enabled:
            logger.info("Migration disabled, skipping downgrade")
            return None
        from alembic import command

        cfg = self._make_config()
        logger.info("Downgrading database by: %s", revision)
        command.downgrade(cfg, revision)
        logger.info("Database downgrade complete")
        return None

    def current(self) -> str | None:
        """Get the current migration revision."""
        if not self._enabled:
            return None
        from alembic.runtime.migration import MigrationContext
        from sqlalchemy import create_engine

        engine = create_engine(self._db_url)
        with engine.connect() as conn:
            migration_ctx = MigrationContext.configure(conn)
            return migration_ctx.get_current_revision()

    def heads(self) -> list[str]:
        """Get the head revisions."""
        if not self._enabled:
            return []
        from alembic.script import ScriptDirectory

        cfg = self._make_config()
        script_dir = ScriptDirectory.from_config(cfg)
        return list(script_dir.get_heads())

    def history(self) -> list[str]:
        """Get migration history."""
        if not self._enabled:
            return []
        from alembic.script import ScriptDirectory

        cfg = self._make_config()
        script_dir = ScriptDirectory.from_config(cfg)
        revisions = list(script_dir.walk_revisions())
        return [f"{r.revision}: {r.doc}" for r in revisions]

    def stamp(self, revision: str = "head") -> Any:
        """Stamp the database with the given revision without running migrations."""
        if not self._enabled:
            return None
        from alembic import command

        cfg = self._make_config()
        command.stamp(cfg, revision)
        return None

    def init_scripts(self) -> None:
        """Initialize the migration scripts directory structure.

        Creates:
        - ``scripts_dir/env.py`` (custom env that reads DB URL from agentbase config)
        - ``scripts_dir/script.py.mako`` (revision template)
        - ``scripts_dir/versions/`` (directory for migration scripts)
        """
        if not self._enabled:
            return
        _ensure_alembic()

        versions_dir = self._scripts_dir / "versions"
        versions_dir.mkdir(parents=True, exist_ok=True)

        # Write env.py
        env_path = self._scripts_dir / "env.py"
        if not env_path.exists():
            env_path.write_text(_ENV_PY_TEMPLATE, encoding="utf-8")

        # Write script.py.mako
        mako_path = self._scripts_dir / "script.py.mako"
        if not mako_path.exists():
            mako_path.write_text(_SCRIPT_TEMPLATE_MAKO, encoding="utf-8")

        logger.info("Initialized migration scripts in: %s", self._scripts_dir)


def create_migration_manager(
    *,
    storage_type: str,
    db_dir: str | None = None,
    dsn: str | None = None,
    scripts_dir: str = "migrations",
    enabled: bool = True,
) -> MigrationManager:
    """Create a MigrationManager from storage configuration.

    Args:
        storage_type: ``"sqlite"``, ``"postgres"``, or ``"mysql"``
        db_dir: Directory for SQLite database files
        dsn: Connection string for PostgreSQL/MySQL
        scripts_dir: Path to Alembic migrations directory
        enabled: Whether migration is enabled

    Returns:
        A configured MigrationManager instance.
    """
    db_url = _storage_url_to_sqlalchemy(
        storage_type=storage_type,
        db_dir=db_dir,
        dsn=dsn,
    )
    return MigrationManager(
        scripts_dir=Path(scripts_dir),
        db_url=db_url,
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_ENV_PY_TEMPLATE = '''"""Alembic environment for AgentBase migrations.

Reads the database URL from the ``sqlalchemy.url`` config option,
which is set dynamically by ``MigrationManager``.
"""
from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config


def run_migrations_offline() -> None:
    """Run migrations in offline mode (generate SQL scripts)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in online mode (connect to database)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
'''

_SCRIPT_TEMPLATE_MAKO = '''"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
'''
