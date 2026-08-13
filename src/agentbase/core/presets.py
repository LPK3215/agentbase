"""Project initialization presets — named combinations of database/embedding/queue/tracer.

Presets provide a quick way to scaffold a project with a specific
combination of storage, embedding, queue, and tracer providers.

Usage in CLI::

    agentbase init myproject --preset dev
    agentbase init myproject --preset prod
    agentbase init myproject --storage sqlite --embedding hash --queue memory --tracer null

Available presets:
- ``dev`` (default): SQLite + Hash + Memory + Null — zero external deps
- ``prod``: PostgreSQL + OpenAI + Redis + Memory — production-grade
- ``minimal``: SQLite + Hash + Memory + Null — minimal tooling
- ``full``: PostgreSQL + OpenAI + Redis + Memory — all features enabled
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Preset:
    """A named combination of provider selections.

    Attributes:
        name: Preset identifier (e.g. ``"dev"``, ``"prod"``).
        description: Human-readable description.
        storage_type: Storage backend type (``"sqlite"`` or ``"postgresql"``).
        storage_dsn: DSN template for PostgreSQL (empty for SQLite).
        embedding_provider: Embedding provider name.
        queue_type: Queue provider name (``"memory"`` or ``"redis"``).
        queue_dsn: DSN template for Redis (empty for memory).
        tracer_type: Tracer provider name.
        audit_enabled: Whether audit logging is enabled by default.
        redaction_enabled: Whether redaction is enabled by default.
        env_keys: List of env var names that should be in .env.example.
    """

    name: str
    description: str
    storage_type: str = "sqlite"
    storage_dsn: str = ""
    embedding_provider: str = "hash"
    queue_type: str = "memory"
    queue_dsn: str = ""
    tracer_type: str = "null"
    audit_enabled: bool = False
    redaction_enabled: bool = False
    env_keys: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.env_keys is None:
            self.env_keys = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "storage_type": self.storage_type,
            "storage_dsn": self.storage_dsn,
            "embedding_provider": self.embedding_provider,
            "queue_type": self.queue_type,
            "queue_dsn": self.queue_dsn,
            "tracer_type": self.tracer_type,
            "audit_enabled": self.audit_enabled,
            "redaction_enabled": self.redaction_enabled,
            "env_keys": self.env_keys,
        }


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------

PRESETS: dict[str, Preset] = {
    "dev": Preset(
        name="dev",
        description="Development preset — SQLite + Hash + Memory + Null (zero external deps)",
        storage_type="sqlite",
        embedding_provider="hash",
        queue_type="memory",
        tracer_type="null",
        audit_enabled=False,
        redaction_enabled=False,
        env_keys=[
            "DEEPSEEK_API_KEY",
            "OPENAI_API_KEY",
            "AGENTBASE_API_KEY",
            "AGENTBASE_AUTH__SECRET",
        ],
    ),
    "prod": Preset(
        name="prod",
        description="Production preset — PostgreSQL + OpenAI + Redis + Memory",
        storage_type="postgresql",
        storage_dsn="postgresql://user:pass@localhost:5432/agentbase",
        embedding_provider="openai",
        queue_type="redis",
        queue_dsn="redis://localhost:6379/0",
        tracer_type="memory",
        audit_enabled=True,
        redaction_enabled=True,
        env_keys=[
            "OPENAI_API_KEY",
            "POSTGRES_DSN",
            "REDIS_URL",
            "AGENTBASE_API_KEY",
            "AGENTBASE_AUTH__SECRET",
            "AGENTBASE_STORAGE__TYPE=postgresql",
            "AGENTBASE_STORAGE__DSN=postgresql://user:pass@localhost:5432/agentbase",
            "AGENTBASE_EMBEDDING__PROVIDER=openai",
            "AGENTBASE_QUEUE__PROVIDER=redis",
        ],
    ),
    "minimal": Preset(
        name="minimal",
        description="Minimal preset — SQLite + Hash + Memory + Null (fewest tools)",
        storage_type="sqlite",
        embedding_provider="hash",
        queue_type="memory",
        tracer_type="null",
        audit_enabled=False,
        redaction_enabled=False,
        env_keys=[
            "OPENAI_API_KEY",
            "AGENTBASE_API_KEY",
        ],
    ),
    "full": Preset(
        name="full",
        description="Full preset — PostgreSQL + OpenAI + Redis + Memory (all features enabled)",
        storage_type="postgresql",
        storage_dsn="postgresql://user:pass@localhost:5432/agentbase",
        embedding_provider="openai",
        queue_type="redis",
        queue_dsn="redis://localhost:6379/0",
        tracer_type="memory",
        audit_enabled=True,
        redaction_enabled=True,
        env_keys=[
            "OPENAI_API_KEY",
            "POSTGRES_DSN",
            "REDIS_URL",
            "AGENTBASE_API_KEY",
            "AGENTBASE_AUTH__SECRET",
            "AGENTBASE_STORAGE__TYPE=postgresql",
            "AGENTBASE_STORAGE__DSN=postgresql://user:pass@localhost:5432/agentbase",
            "AGENTBASE_EMBEDDING__PROVIDER=openai",
            "AGENTBASE_QUEUE__PROVIDER=redis",
            "AGENTBASE_AUDIT__ENABLED=true",
            "AGENTBASE_REDACTION__ENABLED=true",
        ],
    ),
}


def get_preset(name: str) -> Preset:
    """Get a preset by name. Returns the ``dev`` preset if unknown."""
    return PRESETS.get(name.lower(), PRESETS["dev"])


def list_presets() -> list[Preset]:
    """List all available presets."""
    return list(PRESETS.values())


def resolve_preset(
    *,
    preset_name: str | None = None,
    storage: str | None = None,
    embedding: str | None = None,
    queue: str | None = None,
    tracer: str | None = None,
    audit: bool | None = None,
    redaction: bool | None = None,
) -> Preset:
    """Resolve a preset with optional overrides.

    If ``preset_name`` is provided, start from that preset. Then apply
    any non-None overrides from the individual component arguments.
    """
    base = get_preset(preset_name or "dev")
    return Preset(
        name=base.name,
        description=base.description,
        storage_type=storage or base.storage_type,
        storage_dsn=base.storage_dsn if storage is None else "",
        embedding_provider=embedding or base.embedding_provider,
        queue_type=queue or base.queue_type,
        queue_dsn=base.queue_dsn if queue is None else "",
        tracer_type=tracer or base.tracer_type,
        audit_enabled=audit if audit is not None else base.audit_enabled,
        redaction_enabled=redaction if redaction is not None else base.redaction_enabled,
        env_keys=base.env_keys,
    )
