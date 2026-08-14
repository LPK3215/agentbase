"""Model management service — multi-model registration, CRUD, and testing.

Provides a pluggable model management system that allows users to:
- Register multiple model configurations (provider, name, base_url, etc.)
- Query, update, and delete model configurations at runtime
- Test model connectivity (ping a model with a simple prompt)
- List all registered models for use in agent configuration or UI

Pluggable storage:
- ``InMemoryModelProvider`` (default) — zero-config, in-process
- ``NullModelProvider`` — no-op, zero overhead when disabled
- Register custom providers with ``@register_model_provider("name")``

Usage::

    from agentbase.core.model_manager import ModelManager, ModelEntry

    manager = ModelManager(provider="memory", enabled=True)

    entry = manager.register(ModelEntry(
        name="gpt-4",
        provider="openai",
        model_name="gpt-4.1",
        temperature=0.7,
        api_key_env="OPENAI_API_KEY",
    ))

    # Retrieve
    retrieved = manager.get("gpt-4")

    # Test connectivity
    result = manager.test("gpt-4")
    # → {"success": True, "response": "Hello!", "duration_ms": 234.5}

    # List all
    all_models = manager.list()
"""
from __future__ import annotations

import threading
import time
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
class ModelEntry:
    """A single registered model configuration.

    Attributes:
        name: Unique model name (identifier for API and agent config reference).
        provider: LLM provider (openai, anthropic, google, deepseek, etc.).
        model_name: The model name used by the provider (e.g. ``gpt-4.1``, ``claude-3-5-sonnet``).
        temperature: Sampling temperature (0.0–2.0).
        max_tokens: Maximum output tokens (None = provider default).
        timeout_seconds: Request timeout.
        base_url: Custom API base URL (for OpenAI-compatible gateways).
        api_key_env: Environment variable name containing the API key.
        extra: Additional provider-specific kwargs.
        description: Human-readable description.
        enabled: Whether this model is available for use.
        tags: Optional tags for grouping/filtering (e.g. ["chat", "fast"]).
        created_at: ISO 8601 UTC timestamp (auto-set on register).
        updated_at: ISO 8601 UTC timestamp (auto-set on update).
    """

    name: str
    provider: str = "openai"
    model_name: str = ""
    temperature: float = 0.0
    max_tokens: int | None = None
    timeout_seconds: int = 120
    base_url: str | None = None
    api_key_env: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    enabled: bool = True
    tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "model_name": self.model_name,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "extra": self.extra,
            "description": self.description,
            "enabled": self.enabled,
            "tags": self.tags,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelEntry:
        """Create a ModelEntry from a dict, ignoring unknown keys."""
        known_fields = {
            "name", "provider", "model_name", "temperature", "max_tokens",
            "timeout_seconds", "base_url", "api_key_env", "extra",
            "description", "enabled", "tags", "created_at", "updated_at",
        }
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


@dataclass
class ModelTestResult:
    """Result of testing a model configuration.

    Attributes:
        name: The model name that was tested.
        success: Whether the test succeeded.
        response: The model's response text (may be truncated).
        duration_ms: Time taken in milliseconds.
        error: Error message if the test failed.
        timestamp: ISO 8601 UTC timestamp.
    """

    name: str
    success: bool
    response: str = ""
    duration_ms: float = 0.0
    error: str = ""
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "success": self.success,
            "response": self.response[:500] if self.response else "",
            "duration_ms": round(self.duration_ms, 2),
            "error": self.error,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class ModelProvider(Protocol):
    """Protocol for model management providers.

    Implementations must be thread-safe.
    """

    def register(self, entry: ModelEntry) -> ModelEntry:
        """Register or replace a model configuration. Returns the stored entry."""
        ...

    def get(self, name: str) -> ModelEntry | None:
        """Get a model configuration by name. Returns None if not found."""
        ...

    def list(self) -> list[ModelEntry]:
        """List all registered model configurations."""
        ...

    def update(self, name: str, changes: dict[str, Any]) -> ModelEntry | None:
        """Update fields on an existing model. Returns the updated entry or None."""
        ...

    def delete(self, name: str) -> bool:
        """Delete a model configuration. Returns True if deleted."""
        ...

    def close(self) -> None:
        """Release resources."""
        ...


# ---------------------------------------------------------------------------
# Null provider (no-op, zero overhead)
# ---------------------------------------------------------------------------

class NullModelProvider:
    """No-op model provider — stores nothing.

    Used when model management is disabled (``model_manager.enabled=false``).
    """

    def register(self, entry: ModelEntry) -> ModelEntry:
        return entry

    def get(self, name: str) -> ModelEntry | None:
        return None

    def list(self) -> list[ModelEntry]:
        return []

    def update(self, name: str, changes: dict[str, Any]) -> ModelEntry | None:
        return None

    def delete(self, name: str) -> bool:
        return False

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# In-memory provider (default, zero-config)
# ---------------------------------------------------------------------------

class InMemoryModelProvider:
    """In-memory model provider — zero-config, process-local, thread-safe.

    Models are stored in a dict and lost on process restart.  Suitable for
    development, testing, and single-instance deployments.

    For production multi-instance setups, implement a storage-backed
    provider (PostgreSQL, Redis, etc.) and register it with
    ``@register_model_provider("name")``.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._models: dict[str, ModelEntry] = {}

    def register(self, entry: ModelEntry) -> ModelEntry:
        with self._lock:
            if not entry.name:
                raise ValueError("Model entry name cannot be empty")
            now = _now()
            # Preserve original created_at if updating existing entry
            existing = self._models.get(entry.name)
            if existing is not None:
                entry.created_at = existing.created_at
            entry.updated_at = now
            self._models[entry.name] = entry
            logger.info(
                "Model registered: %s (provider=%s, model_name=%s)",
                entry.name, entry.provider, entry.model_name,
                extra={
                    "event": "model.register",
                    "model": entry.name,
                    "provider": entry.provider,
                },
            )
            return entry

    def get(self, name: str) -> ModelEntry | None:
        with self._lock:
            return self._models.get(name)

    def list(self) -> list[ModelEntry]:
        with self._lock:
            return list(self._models.values())

    def update(self, name: str, changes: dict[str, Any]) -> ModelEntry | None:
        with self._lock:
            existing = self._models.get(name)
            if existing is None:
                return None
            data = existing.to_dict()
            for key, value in changes.items():
                if key in data and key != "name" and key != "created_at":
                    data[key] = value
            data["updated_at"] = _now()
            updated = ModelEntry.from_dict(data)
            self._models[name] = updated
            logger.info(
                "Model updated: %s (fields: %s)",
                name, list(changes.keys()),
                extra={"event": "model.update", "model": name},
            )
            return updated

    def delete(self, name: str) -> bool:
        with self._lock:
            if name in self._models:
                del self._models[name]
                logger.info(
                    "Model deleted: %s", name,
                    extra={"event": "model.delete", "model": name},
                )
                return True
            return False

    def close(self) -> None:
        with self._lock:
            count = len(self._models)
            self._models.clear()
            if count:
                logger.info("Model provider closed: %d entries cleared", count)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_model_registry: dict[str, Callable[..., ModelProvider]] = {}
_model_registry_lock = threading.Lock()


def register_model_provider(name: str) -> Callable[[Callable], Callable]:
    """Decorator to register a model provider implementation.

    Usage::

        @register_model_provider("redis")
        class RedisModelProvider:
            def __init__(self, **kwargs):
                ...
    """

    def decorator(cls: Callable) -> Callable:
        with _model_registry_lock:
            _model_registry[name] = cls
        logger.debug("Model provider registered: %s -> %s", name, cls.__name__)
        return cls

    return decorator


def get_model_provider(name: str, **kwargs: Any) -> ModelProvider:
    """Get a model provider instance by name.

    Raises RegistryError if the provider is not found.
    """
    with _model_registry_lock:
        factory = _model_registry.get(name)
    if factory is None:
        raise RegistryError(
            f"Unknown model provider: '{name}'. "
            f"Available: {', '.join(sorted(_model_registry.keys())) or 'none'}",
            code="AGENTBASE_REG_001",
        )
    return factory(**kwargs)


def list_model_providers() -> list[str]:
    """List all registered model provider names."""
    with _model_registry_lock:
        return sorted(_model_registry.keys())


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class ModelManager:
    """High-level model management service.

    Wraps a ``ModelProvider`` instance and provides:
    - CRUD operations (register / get / list / update / delete)
    - Connectivity testing (``test()`` builds a model and sends a simple prompt)
    - Integration with ``model_factory.build_model()`` for testing

    Configuration::

        model_manager:
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
            self._provider: ModelProvider = NullModelProvider()
            logger.info("Model manager disabled (NullModelProvider)")
        else:
            try:
                self._provider = get_model_provider(provider, **kwargs)
            except RegistryError:
                logger.warning(
                    "Model provider '%s' not found, falling back to NullModelProvider",
                    provider,
                )
                self._provider = NullModelProvider()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def provider(self) -> ModelProvider:
        return self._provider

    def register(self, entry: ModelEntry) -> ModelEntry:
        """Register or replace a model configuration."""
        return self._provider.register(entry)

    def get(self, name: str) -> ModelEntry | None:
        """Get a model configuration by name."""
        return self._provider.get(name)

    def list(self) -> list[ModelEntry]:
        """List all registered model configurations."""
        return self._provider.list()

    def update(self, name: str, changes: dict[str, Any]) -> ModelEntry | None:
        """Update fields on an existing model."""
        return self._provider.update(name, changes)

    def delete(self, name: str) -> bool:
        """Delete a model configuration."""
        return self._provider.delete(name)

    def test(self, name: str, *, prompt: str = "Say hello in one word.") -> ModelTestResult:
        """Test a model's connectivity by sending a simple prompt.

        Builds a LangChain model instance from the registered configuration
        and sends a test message.  Returns timing and response information.

        Args:
            name: The registered model name to test.
            prompt: The test prompt to send (default: simple greeting).

        Returns:
            ModelTestResult with success/failure, response text, and duration.
        """
        entry = self._provider.get(name)
        if entry is None:
            return ModelTestResult(
                name=name,
                success=False,
                error=f"Model '{name}' not found",
            )
        if not entry.enabled:
            return ModelTestResult(
                name=name,
                success=False,
                error=f"Model '{name}' is disabled",
            )

        start = time.time()
        try:
            from agentbase.config.schema import ModelConfig
            from agentbase.factories.model_factory import build_model

            # Build ModelConfig from ModelEntry
            model_cfg = ModelConfig(
                provider=entry.provider,
                name=entry.model_name or entry.name,
                temperature=entry.temperature,
                max_tokens=entry.max_tokens,
                timeout_seconds=entry.timeout_seconds,
                base_url=entry.base_url,
                api_key_env=entry.api_key_env,
                extra=entry.extra,
            )
            model = build_model(model_cfg)
            response = model.invoke(prompt)
            # Extract text from response
            if hasattr(response, "content"):
                text = str(response.content)
            else:
                text = str(response)
            duration_ms = (time.time() - start) * 1000
            return ModelTestResult(
                name=name,
                success=True,
                response=text,
                duration_ms=duration_ms,
            )
        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.time() - start) * 1000
            logger.warning(
                "Model test failed for '%s': %s", name, exc,
                extra={"event": "model.test_failed", "model": name, "error": str(exc)},
            )
            return ModelTestResult(
                name=name,
                success=False,
                error=str(exc),
                duration_ms=duration_ms,
            )

    def close(self) -> None:
        """Release provider resources."""
        self._provider.close()


# ---------------------------------------------------------------------------
# Default singleton (lazy-initialized)
# ---------------------------------------------------------------------------

_default_manager: ModelManager | None = None
_default_manager_lock = threading.Lock()


def get_model_manager() -> ModelManager:
    """Get the default ModelManager singleton.

    The singleton is lazily initialised as disabled (NullModelProvider)
    on first access.  Call ``set_model_manager()`` to configure it.
    """
    global _default_manager
    if _default_manager is None:
        with _default_manager_lock:
            if _default_manager is None:
                _default_manager = ModelManager(enabled=False)
    return _default_manager


def set_model_manager(manager: ModelManager) -> None:
    """Set the global ModelManager singleton."""
    global _default_manager
    with _default_manager_lock:
        _default_manager = manager


# ---------------------------------------------------------------------------
# Register default providers
# ---------------------------------------------------------------------------

@register_model_provider("memory")
def _make_in_memory_provider(**kwargs: Any) -> InMemoryModelProvider:
    """Factory for InMemoryModelProvider."""
    return InMemoryModelProvider()


@register_model_provider("null")
def _make_null_provider(**kwargs: Any) -> NullModelProvider:
    """Factory for NullModelProvider."""
    return NullModelProvider()
