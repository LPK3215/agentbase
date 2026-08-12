"""MySQL checkpointer for LangGraph — mirrors SqliteSaver with pymysql.

Stores agent session checkpoints in a MySQL/MariaDB database.  Uses the same
two-table schema as SqliteSaver (``checkpoints`` + ``writes``) with MySQL types.

Usage::

    from agentbase.runtime.checkpoint_mysql import MySQLSaver

    saver = MySQLSaver(dsn="mysql://user:pass@host:3306/db")
    saver.setup()
    # pass to graph.compile(checkpointer=saver)

Or via config::

    checkpointer:
      type: mysql
      dsn: mysql://agentbase:agentbase@127.0.0.1:3307/agentbase
"""
from __future__ import annotations

import json
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


class MySQLSaver(BaseCheckpointSaver[str]):
    """A checkpoint saver that stores checkpoints in a MySQL database.

    Synchronous-only (like SqliteSaver).  Uses pymysql with DictCursor.
    """

    def __init__(
        self,
        conn: Any,
        *,
        serde: Any | None = None,
    ) -> None:
        super().__init__(serde=serde)
        self.jsonplus_serde = JsonPlusSerializer()
        self.conn = conn
        self.is_setup = False
        self.lock = threading.Lock()

    @classmethod
    @contextmanager
    def from_dsn(cls, dsn: str) -> Iterator[MySQLSaver]:
        """Create a MySQLSaver from a DSN string."""
        import re

        import pymysql

        match = re.match(r"mysql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", dsn)
        if match:
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
            yield cls(conn)
        else:
            raise ValueError(f"Invalid MySQL DSN: {dsn}")

    def setup(self) -> None:
        """Create checkpoint tables if they don't exist."""
        if self.is_setup:
            return

        with self.conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    thread_id VARCHAR(128) NOT NULL,
                    checkpoint_ns VARCHAR(128) NOT NULL DEFAULT '',
                    checkpoint_id VARCHAR(128) NOT NULL,
                    parent_checkpoint_id VARCHAR(128),
                    type VARCHAR(64),
                    checkpoint LONGBLOB,
                    metadata LONGBLOB,
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS writes (
                    thread_id VARCHAR(128) NOT NULL,
                    checkpoint_ns VARCHAR(128) NOT NULL DEFAULT '',
                    checkpoint_id VARCHAR(128) NOT NULL,
                    task_id VARCHAR(128) NOT NULL,
                    idx INT NOT NULL,
                    channel VARCHAR(128) NOT NULL,
                    type VARCHAR(64),
                    value LONGBLOB,
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
                )
                """
            )
        self.conn.commit()
        self.is_setup = True

    @contextmanager
    def cursor(self, transaction: bool = True) -> Iterator[Any]:
        """Get a cursor for the MySQL database."""
        with self.lock:
            self.setup()
            cur = self.conn.cursor()
            try:
                yield cur
            finally:
                if transaction:
                    self.conn.commit()
                cur.close()

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Get a checkpoint tuple from the database."""
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        thread_id = str(config["configurable"]["thread_id"])

        with self.cursor(transaction=False) as cur:
            if checkpoint_id := get_checkpoint_id(config):
                cur.execute(
                    "SELECT thread_id, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata "
                    "FROM checkpoints WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s",
                    (thread_id, checkpoint_ns, checkpoint_id),
                )
            else:
                cur.execute(
                    "SELECT thread_id, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata "
                    "FROM checkpoints WHERE thread_id = %s AND checkpoint_ns = %s "
                    "ORDER BY checkpoint_id DESC LIMIT 1",
                    (thread_id, checkpoint_ns),
                )

            if value := cur.fetchone():
                row = value
                r_thread_id = row["thread_id"]
                r_checkpoint_id = row["checkpoint_id"]
                r_parent_checkpoint_id = row["parent_checkpoint_id"]
                r_type = row["type"]
                r_checkpoint = row["checkpoint"]
                r_metadata = row["metadata"]

                if not get_checkpoint_id(config):
                    config = {
                        "configurable": {
                            "thread_id": r_thread_id,
                            "checkpoint_ns": checkpoint_ns,
                            "checkpoint_id": r_checkpoint_id,
                        }
                    }

                # find pending writes
                cur.execute(
                    "SELECT task_id, channel, type, value FROM writes "
                    "WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s "
                    "ORDER BY task_id, idx",
                    (thread_id, checkpoint_ns, r_checkpoint_id),
                )
                writes = cur.fetchall()

                return CheckpointTuple(
                    config,
                    self.serde.loads_typed((r_type, r_checkpoint)),
                    cast(
                        CheckpointMetadata,
                        json.loads(r_metadata) if r_metadata is not None else {},
                    ),
                    (
                        {
                            "configurable": {
                                "thread_id": r_thread_id,
                                "checkpoint_ns": checkpoint_ns,
                                "checkpoint_id": r_parent_checkpoint_id,
                            }
                        }
                        if r_parent_checkpoint_id
                        else None
                    ),
                    [
                        (
                            w["task_id"],
                            w["channel"],
                            self.serde.loads_typed((w["type"], w["value"])),
                        )
                        for w in writes
                    ],
                )
        return None

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        """List checkpoints from the database."""
        where_parts: list[str] = []
        param_values: list[Any] = []

        if config:
            where_parts.append("thread_id = %s")
            param_values.append(str(config["configurable"]["thread_id"]))
            checkpoint_ns = config["configurable"].get("checkpoint_ns")
            if checkpoint_ns is not None:
                where_parts.append("checkpoint_ns = %s")
                param_values.append(checkpoint_ns)
            if checkpoint_id := get_checkpoint_id(config):
                where_parts.append("checkpoint_id = %s")
                param_values.append(checkpoint_id)

        if before is not None:
            where_parts.append("checkpoint_id < %s")
            param_values.append(get_checkpoint_id(before))

        where_clause = "WHERE " + " AND ".join(where_parts) if where_parts else ""

        query = (
            f"SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, "
            f"type, checkpoint, metadata FROM checkpoints {where_clause} "
            f"ORDER BY checkpoint_id DESC"
        )
        if limit is not None:
            query += " LIMIT %s"
            param_values.append(int(limit))

        with self.cursor(transaction=False) as cur:
            cur.execute(query, param_values)
            rows = cur.fetchall()

            for row in rows:
                r_thread_id = row["thread_id"]
                r_checkpoint_ns = row["checkpoint_ns"]
                r_checkpoint_id = row["checkpoint_id"]
                r_parent_id = row["parent_checkpoint_id"]
                r_type = row["type"]
                r_checkpoint = row["checkpoint"]
                r_metadata = row["metadata"]

                cur.execute(
                    "SELECT task_id, channel, type, value FROM writes "
                    "WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s "
                    "ORDER BY task_id, idx",
                    (r_thread_id, r_checkpoint_ns, r_checkpoint_id),
                )
                writes = cur.fetchall()

                yield CheckpointTuple(
                    {
                        "configurable": {
                            "thread_id": r_thread_id,
                            "checkpoint_ns": r_checkpoint_ns,
                            "checkpoint_id": r_checkpoint_id,
                        }
                    },
                    self.serde.loads_typed((r_type, r_checkpoint)),
                    cast(
                        CheckpointMetadata,
                        json.loads(r_metadata) if r_metadata is not None else {},
                    ),
                    (
                        {
                            "configurable": {
                                "thread_id": r_thread_id,
                                "checkpoint_ns": r_checkpoint_ns,
                                "checkpoint_id": r_parent_id,
                            }
                        }
                        if r_parent_id
                        else None
                    ),
                    [
                        (
                            w["task_id"],
                            w["channel"],
                            self.serde.loads_typed((w["type"], w["value"])),
                        )
                        for w in writes
                    ],
                )

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Save a checkpoint to the database."""
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"]["checkpoint_ns"]
        type_, serialized_checkpoint = self.serde.dumps_typed(checkpoint)
        serialized_metadata = json.dumps(
            get_checkpoint_metadata(config, metadata), ensure_ascii=False
        ).encode("utf-8")

        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO checkpoints "
                "(thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE "
                "checkpoint = VALUES(checkpoint), metadata = VALUES(metadata)",
                (
                    str(thread_id),
                    checkpoint_ns,
                    checkpoint["id"],
                    config["configurable"].get("checkpoint_id"),
                    type_,
                    serialized_checkpoint,
                    serialized_metadata,
                ),
            )
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Store intermediate writes linked to a checkpoint."""
        query = (
            "INSERT INTO writes "
            "(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE "
            "type = VALUES(type), value = VALUES(value)"
            if all(w[0] in WRITES_IDX_MAP for w in writes)
            else "INSERT IGNORE INTO writes "
            "(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
        )
        with self.cursor() as cur:
            cur.executemany(
                query,
                [
                    (
                        str(config["configurable"]["thread_id"]),
                        str(config["configurable"]["checkpoint_ns"]),
                        str(config["configurable"]["checkpoint_id"]),
                        task_id,
                        WRITES_IDX_MAP.get(channel, idx),
                        channel,
                        *self.serde.dumps_typed(value),
                    )
                    for idx, (channel, value) in enumerate(writes)
                ],
            )

    def delete_thread(self, thread_id: str) -> None:
        """Delete all checkpoints and writes for a thread."""
        with self.cursor() as cur:
            cur.execute(
                "DELETE FROM checkpoints WHERE thread_id = %s",
                (str(thread_id),),
            )
            cur.execute(
                "DELETE FROM writes WHERE thread_id = %s",
                (str(thread_id),),
            )
