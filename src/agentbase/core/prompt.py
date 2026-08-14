"""Prompt template management — CRUD, versioning, and rendering.

Provides a pluggable prompt management system that allows users to:
- Register reusable prompt templates with variables and metadata
- Query, update, and delete prompt templates at runtime
- Render templates by substituting variables (``{name}`` syntax)
- Tag and categorise templates for discovery
- Track version history (created_at / updated_at)

Pluggable storage:
- ``InMemoryPromptProvider`` (default) — zero-config, in-process
- ``NullPromptProvider`` — no-op, zero overhead when disabled
- Register custom providers with ``@register_prompt_provider("name")``

Usage::

    from agentbase.core.prompt import PromptManager, PromptTemplate

    manager = PromptManager(provider="memory", enabled=True)

    tpl = manager.register(PromptTemplate(
        name="greeting",
        content="Hello {name}! You are a {role}.",
        variables=["name", "role"],
        description="Greeting prompt with name and role.",
        tags=["chat", "greeting"],
    ))

    # Render
    rendered = manager.render("greeting", name="Alice", role="engineer")
    # → "Hello Alice! You are a engineer."

    # List all
    all_prompts = manager.list()
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, runtime_checkable

from agentbase.runtime.errors import RegistryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PromptTemplate:
    """A reusable prompt template with variable substitution.

    Attributes:
        name: Unique template name (identifier for API and agent reference).
        content: The template text using ``{variable}`` placeholders.
        variables: List of expected variable names (for documentation and validation).
        description: Human-readable description of the template's purpose.
        category: Optional grouping (e.g. "system", "greeting", "summary").
        tags: Optional tags for filtering and discovery.
        version: Version string for tracking changes (e.g. "1.0.0").
        enabled: Whether this template is available for use.
        created_at: ISO 8601 UTC timestamp (auto-set on register).
        updated_at: ISO 8601 UTC timestamp (auto-set on update).
    """

    name: str
    content: str = ""
    variables: list[str] = field(default_factory=list)
    description: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)
    version: str = "1.0.0"
    enabled: bool = True
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "content": self.content,
            "variables": self.variables,
            "description": self.description,
            "category": self.category,
            "tags": self.tags,
            "version": self.version,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PromptTemplate:
        """Create a PromptTemplate from a dict, ignoring unknown keys."""
        known_fields = {
            "name", "content", "variables", "description",
            "category", "tags", "version", "enabled",
            "created_at", "updated_at",
        }
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class PromptProvider(Protocol):
    """Protocol for prompt template storage providers.

    Implementations must be thread-safe.
    """

    def register(self, template: PromptTemplate) -> PromptTemplate:
        """Register or replace a prompt template. Returns the stored template."""
        ...

    def get(self, name: str) -> PromptTemplate | None:
        """Get a prompt template by name. Returns None if not found."""
        ...

    def list(self) -> list[PromptTemplate]:
        """List all registered prompt templates."""
        ...

    def update(self, name: str, changes: dict[str, Any]) -> PromptTemplate | None:
        """Update fields on an existing template. Returns the updated template or None."""
        ...

    def delete(self, name: str) -> bool:
        """Delete a prompt template. Returns True if deleted."""
        ...

    def close(self) -> None:
        """Release resources."""
        ...


# ---------------------------------------------------------------------------
# Null provider (no-op, zero overhead)
# ---------------------------------------------------------------------------

class NullPromptProvider:
    """No-op prompt provider — stores nothing.

    Used when prompt management is disabled (``prompt_manager.enabled=false``).
    """

    def register(self, template: PromptTemplate) -> PromptTemplate:
        return template

    def get(self, name: str) -> PromptTemplate | None:
        return None

    def list(self) -> list[PromptTemplate]:
        return []

    def update(self, name: str, changes: dict[str, Any]) -> PromptTemplate | None:
        return None

    def delete(self, name: str) -> bool:
        return False

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# In-memory provider (default, zero-config)
# ---------------------------------------------------------------------------

class InMemoryPromptProvider:
    """In-memory prompt provider — zero-config, process-local, thread-safe.

    Templates are stored in a dict and lost on process restart.  Suitable for
    development, testing, and single-instance deployments.

    For production multi-instance setups, implement a storage-backed
    provider (PostgreSQL, Redis, etc.) and register it with
    ``@register_prompt_provider("name")``.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._templates: dict[str, PromptTemplate] = {}

    def register(self, template: PromptTemplate) -> PromptTemplate:
        with self._lock:
            if not template.name:
                raise ValueError("Prompt template name cannot be empty")
            now = _now()
            # Preserve original created_at if updating existing entry
            existing = self._templates.get(template.name)
            if existing is not None:
                template.created_at = existing.created_at
            template.updated_at = now
            self._templates[template.name] = template
            logger.info(
                "Prompt template registered: %s (category=%s, variables=%d)",
                template.name, template.category, len(template.variables),
                extra={
                    "event": "prompt.register",
                    "prompt": template.name,
                    "category": template.category,
                },
            )
            return template

    def get(self, name: str) -> PromptTemplate | None:
        with self._lock:
            return self._templates.get(name)

    def list(self) -> list[PromptTemplate]:
        with self._lock:
            return list(self._templates.values())

    def update(self, name: str, changes: dict[str, Any]) -> PromptTemplate | None:
        with self._lock:
            existing = self._templates.get(name)
            if existing is None:
                return None
            data = existing.to_dict()
            for key, value in changes.items():
                if key in data and key != "name" and key != "created_at":
                    data[key] = value
            data["updated_at"] = _now()
            updated = PromptTemplate.from_dict(data)
            self._templates[name] = updated
            logger.info(
                "Prompt template updated: %s (fields: %s)",
                name, list(changes.keys()),
                extra={"event": "prompt.update", "prompt": name},
            )
            return updated

    def delete(self, name: str) -> bool:
        with self._lock:
            if name in self._templates:
                del self._templates[name]
                logger.info(
                    "Prompt template deleted: %s", name,
                    extra={"event": "prompt.delete", "prompt": name},
                )
                return True
            return False

    def close(self) -> None:
        with self._lock:
            count = len(self._templates)
            self._templates.clear()
            if count:
                logger.info("Prompt provider closed: %d templates cleared", count)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_prompt_registry: dict[str, Callable[..., PromptProvider]] = {}
_prompt_registry_lock = threading.Lock()


def register_prompt_provider(name: str) -> Callable[[Callable], Callable]:
    """Decorator to register a prompt provider implementation.

    Usage::

        @register_prompt_provider("redis")
        class RedisPromptProvider:
            def __init__(self, **kwargs):
                ...
    """

    def decorator(cls: Callable) -> Callable:
        with _prompt_registry_lock:
            _prompt_registry[name] = cls
        logger.debug("Prompt provider registered: %s -> %s", name, cls.__name__)
        return cls

    return decorator


def get_prompt_provider(name: str, **kwargs: Any) -> PromptProvider:
    """Get a prompt provider instance by name.

    Raises RegistryError if the provider is not found.
    """
    with _prompt_registry_lock:
        factory = _prompt_registry.get(name)
    if factory is None:
        raise RegistryError(
            f"Unknown prompt provider: '{name}'. "
            f"Available: {', '.join(sorted(_prompt_registry.keys())) or 'none'}",
            code="AGENTBASE_REG_001",
        )
    return factory(**kwargs)


def list_prompt_providers() -> list[str]:
    """List all registered prompt provider names."""
    with _prompt_registry_lock:
        return sorted(_prompt_registry.keys())


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class PromptManager:
    """High-level prompt template management service.

    Wraps a ``PromptProvider`` instance and provides:
    - CRUD operations (register / get / list / update / delete)
    - Template rendering with variable substitution
    - Variable extraction and validation

    Configuration::

        prompt_manager:
          enabled: false  # default off
          provider: memory
    """

    def __init__(
        self,
        *,
        provider: str = "memory",
        enabled: bool = False,
        **kwargs: Any,
    ) -> None:
        self._enabled = enabled
        if not enabled:
            self._provider: PromptProvider = NullPromptProvider()
            logger.info("Prompt manager disabled (NullPromptProvider)")
        else:
            try:
                self._provider = get_prompt_provider(provider, **kwargs)
            except RegistryError:
                logger.warning(
                    "Prompt provider '%s' not found, falling back to NullPromptProvider",
                    provider,
                )
                self._provider = NullPromptProvider()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def provider(self) -> PromptProvider:
        return self._provider

    def register(self, template: PromptTemplate) -> PromptTemplate:
        """Register or replace a prompt template."""
        return self._provider.register(template)

    def get(self, name: str) -> PromptTemplate | None:
        """Get a prompt template by name."""
        return self._provider.get(name)

    def list(self) -> list[PromptTemplate]:
        """List all registered prompt templates."""
        return self._provider.list()

    def update(self, name: str, changes: dict[str, Any]) -> PromptTemplate | None:
        """Update fields on an existing template."""
        return self._provider.update(name, changes)

    def delete(self, name: str) -> bool:
        """Delete a prompt template."""
        return self._provider.delete(name)

    def render(self, template_name: str, **variables: Any) -> str:
        """Render a prompt template by substituting variables.

        Uses ``str.format()`` for ``{variable}`` substitution.
        Missing variables raise ``KeyError``; extra variables are ignored.

        Args:
            template_name: The registered template name to render.
            **variables: Keyword arguments matching the template's variables.

        Returns:
            The rendered prompt string.

        Raises:
            KeyError: If a required variable is missing.
            ValueError: If the template is not found or is disabled.
        """
        template = self._provider.get(template_name)
        if template is None:
            raise ValueError(f"Prompt template '{template_name}' not found")
        if not template.enabled:
            raise ValueError(f"Prompt template '{template_name}' is disabled")
        try:
            return template.content.format(**variables)
        except KeyError as exc:
            missing = exc.args[0] if exc.args else "unknown"
            raise KeyError(
                f"Missing variable '{missing}' for prompt template '{template_name}'"
            ) from exc

    def extract_variables(self, template_name: str) -> list[str]:
        """Extract variable names from a template's content using ``string.Formatter``.

        Returns the list of variable names found in ``{...}`` placeholders.
        This is useful for validating that declared variables match actual usage.

        Args:
            template_name: The registered template name.

        Returns:
            List of variable names found in the template content.
        """
        template = self._provider.get(template_name)
        if template is None:
            raise ValueError(f"Prompt template '{template_name}' not found")
        import string
        formatter = string.Formatter()
        return [
            field_name
            for _, field_name, _, _ in formatter.parse(template.content)
            if field_name
        ]

    def close(self) -> None:
        """Release provider resources."""
        self._provider.close()


# ---------------------------------------------------------------------------
# Default singleton (lazy-initialized)
# ---------------------------------------------------------------------------

_default_manager: PromptManager | None = None
_default_manager_lock = threading.Lock()


def get_prompt_manager() -> PromptManager:
    """Get the default PromptManager singleton.

    The singleton is lazily initialised as disabled (NullPromptProvider)
    on first access.  Call ``set_prompt_manager()`` to configure it.
    """
    global _default_manager
    if _default_manager is None:
        with _default_manager_lock:
            if _default_manager is None:
                _default_manager = PromptManager(enabled=False)
    return _default_manager


def set_prompt_manager(manager: PromptManager) -> None:
    """Set the global PromptManager singleton."""
    global _default_manager
    with _default_manager_lock:
        _default_manager = manager


# ---------------------------------------------------------------------------
# Register default providers
# ---------------------------------------------------------------------------

@register_prompt_provider("memory")
def _make_in_memory_provider(**kwargs: Any) -> InMemoryPromptProvider:
    """Factory for InMemoryPromptProvider."""
    return InMemoryPromptProvider()


@register_prompt_provider("null")
def _make_null_provider(**kwargs: Any) -> NullPromptProvider:
    """Factory for NullPromptProvider."""
    return NullPromptProvider()
