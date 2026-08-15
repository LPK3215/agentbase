"""Token usage tracking and cost statistics service.

Records prompt/completion/total token counts and estimated costs for
every model invocation, enabling cost monitoring, per-user/agent/model
aggregation, and usage-based analytics — standard capabilities of any
AI backend platform (Dify, FastGPT, One-API, etc.).

Pluggable storage:
- ``InMemoryUsageProvider`` (default) — zero-config, thread-safe, in-process
- ``NullUsageProvider`` — no-op, zero overhead when disabled
- Register custom providers with ``@register_usage_provider("name")``

Usage::

    from agentbase.core.usage import UsageManager, UsageRecord

    manager = UsageManager(provider="memory", enabled=True)
    manager.record(
        agent="default",
        model="gpt-4o-mini",
        prompt_tokens=150,
        completion_tokens=80,
        total_tokens=230,
        cost_usd=0.000345,
        thread_id="thread-abc",
        request_id="req-123",
    )
    stats = manager.get_stats(agent="default")
    records = manager.query_records(agent="default", limit=50)
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
# Default model pricing table (USD per 1K tokens, 2024-2025 approximate).
# Used for automatic cost estimation when no custom pricing is provided.
# Users can override via config ``usage.pricing`` or provide cost at record time.
# ---------------------------------------------------------------------------

_DEFAULT_PRICING: dict[str, dict[str, float]] = {
    # OpenAI models (per 1K tokens)
    "gpt-4o": {"prompt": 0.0025, "completion": 0.01},
    "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
    "gpt-4-turbo": {"prompt": 0.01, "completion": 0.03},
    "gpt-4": {"prompt": 0.03, "completion": 0.06},
    "gpt-3.5-turbo": {"prompt": 0.0005, "completion": 0.0015},
    "gpt-4.1": {"prompt": 0.002, "completion": 0.008},
    "gpt-4.1-mini": {"prompt": 0.0004, "completion": 0.0016},
    "gpt-4.1-nano": {"prompt": 0.0001, "completion": 0.0004},
    "o1": {"prompt": 0.015, "completion": 0.06},
    "o1-mini": {"prompt": 0.003, "completion": 0.012},
    "o1-pro": {"prompt": 0.15, "completion": 0.60},
    "o3": {"prompt": 0.002, "completion": 0.008},
    "o3-mini": {"prompt": 0.0011, "completion": 0.0044},
    "o4-mini": {"prompt": 0.0011, "completion": 0.0044},
    # Anthropic models (per 1K tokens)
    "claude-3-opus": {"prompt": 0.015, "completion": 0.075},
    "claude-3-sonnet": {"prompt": 0.003, "completion": 0.015},
    "claude-3-haiku": {"prompt": 0.00025, "completion": 0.00125},
    "claude-3-5-sonnet": {"prompt": 0.003, "completion": 0.015},
    "claude-3-5-haiku": {"prompt": 0.001, "completion": 0.005},
    "claude-sonnet-4": {"prompt": 0.003, "completion": 0.015},
    "claude-opus-4": {"prompt": 0.005, "completion": 0.025},
    # DeepSeek models (per 1K tokens)
    "deepseek-chat": {"prompt": 0.00014, "completion": 0.00028},
    "deepseek-reasoner": {"prompt": 0.00055, "completion": 0.00219},
    "deepseek-coder": {"prompt": 0.00014, "completion": 0.00028},
    # Google models (per 1K tokens)
    "gemini-1.5-pro": {"prompt": 0.00125, "completion": 0.005},
    "gemini-1.5-flash": {"prompt": 0.000075, "completion": 0.0003},
    "gemini-2.0-flash": {"prompt": 0.0001, "completion": 0.0004},
    "gemini-2.5-pro": {"prompt": 0.00125, "completion": 0.01},
    "gemini-2.5-flash": {"prompt": 0.000075, "completion": 0.0003},
}

# Default fallback pricing (per 1K tokens) for unknown models
_DEFAULT_FALLBACK_PRICING = {"prompt": 0.001, "completion": 0.002}


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    pricing: dict[str, dict[str, float]] | None = None,
) -> float:
    """Estimate USD cost for a model call.

    Args:
        model: Model name (case-insensitive, partial match supported).
        prompt_tokens: Number of input/prompt tokens.
        completion_tokens: Number of output/completion tokens.
        pricing: Optional custom pricing table (model -> {prompt, completion} per 1K tokens).

    Returns:
        Estimated cost in USD.
    """
    if prompt_tokens == 0 and completion_tokens == 0:
        return 0.0

    table = pricing or _DEFAULT_PRICING
    model_lower = model.lower().strip()

    # Try exact match first, then partial match
    rates = None
    if model_lower in table:
        rates = table[model_lower]
    else:
        for key, val in table.items():
            if key in model_lower or model_lower in key:
                rates = val
                break

    if rates is None:
        rates = _DEFAULT_FALLBACK_PRICING

    prompt_cost = (prompt_tokens / 1000.0) * rates.get("prompt", 0.001)
    completion_cost = (completion_tokens / 1000.0) * rates.get("completion", 0.002)
    return round(prompt_cost + completion_cost, 6)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class UsageRecord:
    """A single token usage record — one model invocation.

    Attributes:
        agent: Agent name that made the call.
        model: Model name (e.g. ``"gpt-4o-mini"``).
        prompt_tokens: Number of input/prompt tokens.
        completion_tokens: Number of output/completion tokens.
        total_tokens: Total tokens (prompt + completion).
        cost_usd: Estimated cost in USD.
        thread_id: Conversation thread ID.
        request_id: Request correlation ID.
        user: Optional user identifier (for per-user stats).
        duration_ms: Call duration in milliseconds.
        timestamp: ISO 8601 UTC timestamp (auto-set on record).
        id: Auto-assigned record ID.
    """

    agent: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    thread_id: str = ""
    request_id: str = ""
    user: str = ""
    duration_ms: float = 0.0
    timestamp: str = field(default_factory=_now)
    id: int | None = None

    def __post_init__(self) -> None:
        if self.total_tokens == 0 and (self.prompt_tokens or self.completion_tokens):
            self.total_tokens = self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent": self.agent,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "thread_id": self.thread_id,
            "request_id": self.request_id,
            "user": self.user,
            "duration_ms": round(self.duration_ms, 2),
            "timestamp": self.timestamp,
        }


@dataclass
class UsageFilter:
    """Filter criteria for querying usage records.

    All fields are optional — ``None`` means "no filter on this field".
    """

    agent: str | None = None
    model: str | None = None
    user: str | None = None
    thread_id: str | None = None
    since: str | None = None  # ISO timestamp, inclusive
    until: str | None = None  # ISO timestamp, exclusive
    limit: int = 100
    offset: int = 0


@dataclass
class UsageStats:
    """Aggregated usage statistics.

    Attributes:
        total_calls: Total number of model calls.
        total_prompt_tokens: Sum of prompt tokens.
        total_completion_tokens: Sum of completion tokens.
        total_tokens: Sum of all tokens.
        total_cost_usd: Sum of all costs in USD.
        avg_duration_ms: Average call duration in milliseconds.
        by_model: Per-model breakdown {model: {calls, prompt_tokens, ...}}.
        by_agent: Per-agent breakdown {agent: {calls, prompt_tokens, ...}}.
        by_user: Per-user breakdown {user: {calls, prompt_tokens, ...}}.
    """

    total_calls: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    avg_duration_ms: float = 0.0
    by_model: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_agent: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_user: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_calls": self.total_calls,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "avg_duration_ms": round(self.avg_duration_ms, 2),
            "by_model": dict(self.by_model),
            "by_agent": dict(self.by_agent),
            "by_user": dict(self.by_user),
        }


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class UsageProvider(Protocol):
    """Protocol for usage tracking providers.

    Implementations must be thread-safe.
    """

    def record(self, entry: UsageRecord) -> UsageRecord:
        """Persist a usage record. Returns the record with ID assigned."""
        ...

    def query(self, filter: UsageFilter | None = None) -> list[UsageRecord]:
        """Query usage records matching the filter."""
        ...

    def stats(self, filter: UsageFilter | None = None) -> UsageStats:
        """Compute aggregated statistics for records matching the filter."""
        ...

    def count(self, filter: UsageFilter | None = None) -> int:
        """Count records matching the filter."""
        ...

    def clear(self) -> int:
        """Delete all records. Returns count deleted."""
        ...

    def close(self) -> None:
        """Release resources."""
        ...


# ---------------------------------------------------------------------------
# Null provider (no-op, zero overhead)
# ---------------------------------------------------------------------------

class NullUsageProvider:
    """No-op usage provider — discards all records.

    Used when usage tracking is disabled (``usage.enabled=false``).
    """

    def record(self, entry: UsageRecord) -> UsageRecord:
        return entry

    def query(self, filter: UsageFilter | None = None) -> list[UsageRecord]:
        return []

    def stats(self, filter: UsageFilter | None = None) -> UsageStats:
        return UsageStats()

    def count(self, filter: UsageFilter | None = None) -> int:
        return 0

    def clear(self) -> int:
        return 0

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# In-memory provider (default, zero-config)
# ---------------------------------------------------------------------------

class InMemoryUsageProvider:
    """In-memory usage provider — thread-safe, zero-config.

    Stores records in a list with auto-incrementing IDs.
    Suitable for single-process deployments and testing.
    """

    def __init__(self, max_records: int = 100_000) -> None:
        self._records: list[UsageRecord] = []
        self._lock = threading.Lock()
        self._next_id = 1
        self._max_records = max_records

    def record(self, entry: UsageRecord) -> UsageRecord:
        with self._lock:
            entry.id = self._next_id
            self._next_id += 1
            self._records.append(entry)
            # Enforce max records (FIFO eviction)
            if len(self._records) > self._max_records:
                excess = len(self._records) - self._max_records
                del self._records[:excess]
            return entry

    def query(self, filter: UsageFilter | None = None) -> list[UsageRecord]:
        with self._lock:
            records = list(self._records)
        if filter is None:
            return records
        return _apply_filter(records, filter)

    def stats(self, filter: UsageFilter | None = None) -> UsageStats:
        with self._lock:
            records = list(self._records)
        if filter is not None:
            records = _apply_filter(records, filter)
        return _compute_stats(records)

    def count(self, filter: UsageFilter | None = None) -> int:
        with self._lock:
            records = list(self._records)
        if filter is not None:
            records = _apply_filter(records, filter)
        return len(records)

    def clear(self) -> int:
        with self._lock:
            count = len(self._records)
            self._records.clear()
            self._next_id = 1
            return count

    def close(self) -> None:
        with self._lock:
            self._records.clear()


# ---------------------------------------------------------------------------
# Filter / stats helpers
# ---------------------------------------------------------------------------

def _apply_filter(records: list[UsageRecord], flt: UsageFilter) -> list[UsageRecord]:
    """Apply filter criteria to a list of records."""
    result: list[UsageRecord] = []
    for r in records:
        if flt.agent is not None and r.agent != flt.agent:
            continue
        if flt.model is not None and r.model != flt.model:
            continue
        if flt.user is not None and r.user != flt.user:
            continue
        if flt.thread_id is not None and r.thread_id != flt.thread_id:
            continue
        if flt.since is not None and r.timestamp < flt.since:
            continue
        if flt.until is not None and r.timestamp >= flt.until:
            continue
        result.append(r)
    # Apply offset
    if flt.offset > 0:
        result = result[flt.offset:]
    # Apply limit
    if flt.limit > 0:
        result = result[:flt.limit]
    return result


def _compute_stats(records: list[UsageRecord]) -> UsageStats:
    """Compute aggregated statistics from a list of records."""
    if not records:
        return UsageStats()

    total_calls = len(records)
    total_prompt = sum(r.prompt_tokens for r in records)
    total_completion = sum(r.completion_tokens for r in records)
    total_tokens = sum(r.total_tokens for r in records)
    total_cost = sum(r.cost_usd for r in records)
    durations = [r.duration_ms for r in records if r.duration_ms > 0]
    avg_duration = sum(durations) / len(durations) if durations else 0.0

    by_model: dict[str, dict[str, Any]] = {}
    by_agent: dict[str, dict[str, Any]] = {}
    by_user: dict[str, dict[str, Any]] = {}

    for r in records:
        _add_to_breakdown(by_model, r.model, r)
        _add_to_breakdown(by_agent, r.agent, r)
        if r.user:
            _add_to_breakdown(by_user, r.user, r)

    return UsageStats(
        total_calls=total_calls,
        total_prompt_tokens=total_prompt,
        total_completion_tokens=total_completion,
        total_tokens=total_tokens,
        total_cost_usd=total_cost,
        avg_duration_ms=avg_duration,
        by_model=by_model,
        by_agent=by_agent,
        by_user=by_user,
    )


def _add_to_breakdown(
    table: dict[str, dict[str, Any]],
    key: str,
    r: UsageRecord,
) -> None:
    """Add a record to a breakdown table (mutates in place)."""
    if key not in table:
        table[key] = {
            "calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        }
    entry = table[key]
    entry["calls"] += 1
    entry["prompt_tokens"] += r.prompt_tokens
    entry["completion_tokens"] += r.completion_tokens
    entry["total_tokens"] += r.total_tokens
    entry["cost_usd"] = round(entry["cost_usd"] + r.cost_usd, 6)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class UsageRegistry:
    """Thread-safe registry for usage tracking providers."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., UsageProvider]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        factory: Callable[..., UsageProvider],
        *,
        override: bool = False,
    ) -> None:
        key = name.strip().lower()
        with self._lock:
            if not key:
                raise RegistryError("Cannot register empty usage provider name")
            if key in self._factories and not override:
                raise RegistryError(f"Usage provider already registered: {key}")
            self._factories[key] = factory

    def create(self, name: str, **kwargs: Any) -> UsageProvider:
        key = name.strip().lower()
        with self._lock:
            if key not in self._factories:
                available = ", ".join(sorted(self._factories)) or "<empty>"
                raise RegistryError(f"Unknown usage provider: {key}. Available: {available}")
            factory = self._factories[key]
        return factory(**kwargs)

    def has(self, name: str) -> bool:
        with self._lock:
            return name.strip().lower() in self._factories

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._factories.keys())

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._factories)

    def unregister(self, name: str) -> bool:
        key = name.strip().lower()
        with self._lock:
            if key not in self._factories:
                return False
            self._factories.pop(key, None)
            return True


# Global singleton
usage_registry = UsageRegistry()

# Register defaults
usage_registry.register("null", NullUsageProvider)
usage_registry.register("memory", InMemoryUsageProvider)


def register_usage_provider(name: str, *, override: bool = False):
    """Decorator: register a usage tracking provider class.

    Usage::

        @register_usage_provider("redis")
        class RedisUsageProvider:
            def record(self, entry: UsageRecord) -> UsageRecord: ...
    """
    def decorator(factory: Callable[..., UsageProvider]):
        usage_registry.register(name, factory, override=override)
        return factory
    return decorator


# ---------------------------------------------------------------------------
# Manager — high-level facade
# ---------------------------------------------------------------------------

class UsageManager:
    """High-level usage tracking manager.

    Wraps a ``UsageProvider`` and provides convenience methods.
    When ``enabled=False``, uses ``NullUsageProvider`` (no-op).

    Usage::

        manager = UsageManager(provider="memory", enabled=True)
        manager.record(
            agent="default",
            model="gpt-4o-mini",
            prompt_tokens=150,
            completion_tokens=80,
        )
        stats = manager.get_stats()
    """

    def __init__(
        self,
        *,
        provider: str = "null",
        enabled: bool = False,
        pricing: dict[str, dict[str, float]] | None = None,
        **provider_kwargs: Any,
    ) -> None:
        self._enabled = enabled
        self._pricing = pricing
        if not enabled:
            self._provider: UsageProvider = NullUsageProvider()
        else:
            self._provider = usage_registry.create(provider, **provider_kwargs)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def record(
        self,
        *,
        agent: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int | None = None,
        cost_usd: float | None = None,
        thread_id: str = "",
        request_id: str = "",
        user: str = "",
        duration_ms: float = 0.0,
    ) -> UsageRecord:
        """Record a usage entry. No-op when disabled.

        If ``total_tokens`` is None, it's computed as prompt + completion.
        If ``cost_usd`` is None, it's auto-estimated from the pricing table.
        """
        if total_tokens is None:
            total_tokens = prompt_tokens + completion_tokens
        if cost_usd is None:
            cost_usd = estimate_cost(
                model, prompt_tokens, completion_tokens, self._pricing
            )
        entry = UsageRecord(
            agent=agent,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            thread_id=thread_id,
            request_id=request_id,
            user=user,
            duration_ms=duration_ms,
        )
        return self._provider.record(entry)

    def query_records(self, filter: UsageFilter | None = None) -> list[UsageRecord]:
        """Query usage records. Returns empty list when disabled."""
        return self._provider.query(filter)

    def get_stats(self, filter: UsageFilter | None = None) -> UsageStats:
        """Compute aggregated statistics. Returns empty stats when disabled."""
        return self._provider.stats(filter)

    def count_records(self, filter: UsageFilter | None = None) -> int:
        """Count records matching the filter. Returns 0 when disabled."""
        return self._provider.count(filter)

    def clear_records(self) -> int:
        """Delete all records. Returns 0 when disabled."""
        return self._provider.clear()

    def close(self) -> None:
        self._provider.close()


# ---------------------------------------------------------------------------
# Singleton management
# ---------------------------------------------------------------------------

_usage_manager: UsageManager | None = None
_usage_manager_lock = threading.Lock()


def get_usage_manager() -> UsageManager:
    """Get the global UsageManager singleton.

    Raises ``RuntimeError`` if not initialised — call ``set_usage_manager``
    first (typically during application bootstrap).
    """
    if _usage_manager is None:
        with _usage_manager_lock:
            if _usage_manager is None:
                raise RuntimeError(
                    "UsageManager not initialised. Call set_usage_manager() first."
                )
    return _usage_manager  # type: ignore[return-value]


def set_usage_manager(manager: UsageManager) -> None:
    """Set the global UsageManager singleton."""
    global _usage_manager
    with _usage_manager_lock:
        _usage_manager = manager


def reset_usage_manager() -> None:
    """Reset the global UsageManager singleton (for testing)."""
    global _usage_manager
    with _usage_manager_lock:
        _usage_manager = None


# ---------------------------------------------------------------------------
# Token extraction from LangChain / LangGraph results
# ---------------------------------------------------------------------------

def extract_usage_from_result(result: Any) -> dict[str, int]:
    """Extract token usage from a LangChain/LangGraph agent invocation result.

    Looks for ``usage_metadata`` on AIMessage objects in the result messages,
    or for ``usage`` fields in raw dict responses.

    Returns a dict with keys ``prompt_tokens``, ``completion_tokens``,
    ``total_tokens``. Values are 0 if not found.
    """
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0

    messages: list[Any] = []

    # Case 1: result is a dict with "messages" key (LangGraph standard)
    if isinstance(result, dict):
        msgs = result.get("messages")
        if isinstance(msgs, list):
            messages = msgs
    # Case 2: result has .messages attribute (object-style)
    elif hasattr(result, "messages"):
        msgs = getattr(result, "messages")
        if isinstance(msgs, list):
            messages = msgs

    # Scan messages for usage_metadata (AIMessage objects)
    for msg in messages:
        usage = _get_attr(msg, "usage_metadata")
        if usage is None:
            # Try response_metadata (OpenAI format)
            rmeta = _get_attr(msg, "response_metadata")
            if rmeta and isinstance(rmeta, dict):
                token_usage = rmeta.get("token_usage") or rmeta.get("usage")
                if token_usage and isinstance(token_usage, dict):
                    prompt_tokens += int(token_usage.get("prompt_tokens", 0))
                    completion_tokens += int(token_usage.get("completion_tokens", 0))
                    total_tokens += int(token_usage.get("total_tokens", 0))
            continue
        if isinstance(usage, dict):
            prompt_tokens += int(usage.get("input_tokens", usage.get("prompt_tokens", 0)))
            completion_tokens += int(usage.get("output_tokens", usage.get("completion_tokens", 0)))
            total_tokens += int(usage.get("total_tokens", 0))

    # If total_tokens is 0 but we have prompt/completion, compute it
    if total_tokens == 0 and (prompt_tokens or completion_tokens):
        total_tokens = prompt_tokens + completion_tokens

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _get_attr(obj: Any, name: str) -> Any:
    """Safely get an attribute or dict key, returning None if not found."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
