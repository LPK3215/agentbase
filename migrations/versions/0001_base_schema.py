"""base schema: memories, kb_documents, kb_chunks, audit_events

Revision ID: 0001_base_schema
Revises:
Create Date: 2025-01-01 00:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_base_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- memories table ---
    op.create_table(
        "memories",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("agent_name", sa.Text, nullable=False, server_default="default"),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("tags", sa.Text, server_default="[]"),
        sa.Column("metadata", sa.Text, server_default="{}"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
        sa.UniqueConstraint("agent_name", "key", name="uq_memories_agent_key"),
    )
    op.create_index("idx_mem_agent", "memories", ["agent_name"])
    op.create_index("idx_mem_tags", "memories", ["tags"])

    # --- kb_documents table ---
    op.create_table(
        "kb_documents",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False, server_default=""),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("chunk_count", sa.Integer, server_default="0"),
        sa.Column("metadata", sa.Text, server_default="{}"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )

    # --- kb_chunks table ---
    # Note: embedding column is TEXT for SQLite and vector(1536) for pgvector.
    # This migration uses TEXT as the portable default. For pgvector,
    # run the pgvector-specific migration after enabling the extension.
    op.create_table(
        "kb_chunks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("embedding", sa.Text),  # JSON-serialized vector (portable)
        sa.ForeignKeyConstraint(
            ["document_id"], ["kb_documents.id"],
            ondelete="CASCADE",
            name="fk_chunks_document",
        ),
    )
    op.create_index("idx_chunks_doc", "kb_chunks", ["document_id"])

    # --- audit_events table ---
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("resource", sa.Text, server_default=""),
        sa.Column("result", sa.Text, server_default="success"),
        sa.Column("detail", sa.Text, server_default="{}"),
        sa.Column("timestamp", sa.Text, nullable=False),
    )
    op.create_index("idx_audit_actor", "audit_events", ["actor"])
    op.create_index("idx_audit_action", "audit_events", ["action"])
    op.create_index("idx_audit_timestamp", "audit_events", ["timestamp"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("kb_chunks")
    op.drop_table("kb_documents")
    op.drop_table("memories")
