from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agentbase.config.schema import CheckpointerConfig
from agentbase.registry.checkpointers import checkpointer_registry, register_checkpointer
from agentbase.runtime.errors import FactoryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


@register_checkpointer("memory")
def build_memory_checkpointer(spec: CheckpointerConfig, *, root_dir: Path) -> Any:
    try:
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
    except Exception:
        try:
            from langgraph.checkpoint.memory import InMemorySaver

            return InMemorySaver()
        except Exception as exc:  # noqa: BLE001
            raise FactoryError(f"Memory checkpointer unavailable: {exc}") from exc


@register_checkpointer("sqlite")
def build_sqlite_checkpointer(spec: CheckpointerConfig, *, root_dir: Path) -> Any:
    dsn = spec.dsn or "sqlite:///./data/checkpoints.db"
    parsed = urlparse(dsn)
    if parsed.scheme in {"sqlite", "file"}:
        raw_path = parsed.path
        if dsn.startswith("sqlite:///"):
            raw_path = dsn.removeprefix("sqlite:///")
        db_path = Path(raw_path)
    else:
        db_path = Path(dsn)

    if not db_path.is_absolute():
        db_path = root_dir / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except Exception as exc:  # noqa: BLE001
        raise FactoryError(
            "Sqlite checkpointer unavailable. Install langgraph-checkpoint-sqlite."
        ) from exc

    try:
        import sqlite3

        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        saver = SqliteSaver(conn)
        if hasattr(saver, "setup"):
            saver.setup()
        return saver
    except Exception as exc:  # noqa: BLE001
        raise FactoryError(f"Failed to build sqlite checkpointer: {exc}") from exc


@register_checkpointer("postgres")
def build_postgres_checkpointer(spec: CheckpointerConfig, *, root_dir: Path) -> Any:
    dsn = spec.dsn
    if not dsn:
        raise FactoryError("Postgres checkpointer requires checkpointer.dsn")

    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except Exception as exc:  # noqa: BLE001
        raise FactoryError(
            "Postgres checkpointer unavailable. Install optional postgres deps "
            "or start docker compose postgres."
        ) from exc

    try:
        if hasattr(PostgresSaver, "from_conn_string"):
            cm = PostgresSaver.from_conn_string(dsn)
            saver = cm.__enter__()
            if hasattr(saver, "setup"):
                saver.setup()
            saver._agentbase_cm = cm  # type: ignore[attr-defined]
            return saver

        saver = PostgresSaver(dsn)
        if hasattr(saver, "setup"):
            saver.setup()
        return saver
    except Exception as exc:  # noqa: BLE001
        raise FactoryError(f"Failed to build postgres checkpointer: {exc}") from exc


@register_checkpointer("mysql")
def build_mysql_checkpointer(spec: CheckpointerConfig, *, root_dir: Path) -> Any:
    dsn = spec.dsn
    if not dsn:
        raise FactoryError("MySQL checkpointer requires checkpointer.dsn")

    try:
        from agentbase.runtime.checkpoint_mysql import MySQLSaver
    except Exception as exc:  # noqa: BLE001
        raise FactoryError(
            "MySQL checkpointer unavailable. Install pymysql: pip install pymysql"
        ) from exc

    try:
        import re
        import pymysql

        match = re.match(r"mysql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", dsn)
        if not match:
            raise FactoryError(f"Invalid MySQL DSN: {dsn}")
        user, password, host, port, database = match.groups()
        conn = pymysql.connect(
            host=host,
            port=int(port),
            user=user,
            password=password,
            database=database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
        saver = MySQLSaver(conn)
        if hasattr(saver, "setup"):
            saver.setup()
        return saver
    except FactoryError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise FactoryError(f"Failed to build mysql checkpointer: {exc}") from exc


def build_checkpointer(spec: CheckpointerConfig, *, root_dir: Path) -> Any:
    builder = checkpointer_registry.get(spec.type)
    logger.info("Building checkpointer: %s", spec.type)
    return builder(spec, root_dir=root_dir)
