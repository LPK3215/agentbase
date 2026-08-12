"""Cache middleware — caches identical model calls to save API costs.

Caches model responses based on a hash of (agent_name, message, config).
On cache hit, returns the cached response without calling the model.
Useful for development, testing, and reducing API costs for repetitive queries.

Features:
- Thread-safe via ``threading.Lock``
- LRU eviction via ``OrderedDict`` — least recently used entries are
  evicted first when capacity is reached
- TTL expiration — entries older than ``ttl_seconds`` are automatically
  evicted on access
- Cache statistics — hit/miss counts for observability

Usage in config::

    middleware:
      - cache:
          ttl_seconds: 3600
          max_entries: 1000
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from typing import Any

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.middleware import register_middleware

_CACHE_META = ExtensionMeta(
    name="cache",
    kind="middleware",
    description="Cache identical model calls to save API costs. TTL + LRU eviction.",
)


class CacheMiddleware:
    """Caches agent responses to avoid redundant model calls.

    Cache key is based on (agent_name, message, thread_id).
    Entries expire after ``ttl_seconds`` (default 1 hour).

    Uses ``OrderedDict`` for O(1) LRU eviction and ``threading.Lock``
    for thread-safe concurrent access.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int = 3600,
        max_entries: int = 1000,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        # Statistics
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def _make_key(self, agent_name: str, message: str, thread_id: str | None = None) -> str:
        raw = f"{agent_name}:{message}:{thread_id or ''}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, agent_name: str, message: str, thread_id: str | None = None) -> Any | None:
        """Get a cached response. Returns None on miss or expiry."""
        key = self._make_key(agent_name, message, thread_id)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None
            timestamp, result = entry
            if time.time() - timestamp > self._ttl:
                # Expired — remove and count as miss
                del self._cache[key]
                self._misses += 1
                return None
            # Move to end (most recently used)
            self._cache.move_to_end(key)
            self._hits += 1
            return result

    def set(self, agent_name: str, message: str, result: Any, thread_id: str | None = None) -> None:
        """Cache a response."""
        key = self._make_key(agent_name, message, thread_id)
        with self._lock:
            # Evict oldest entries if at capacity
            while len(self._cache) >= self._max_entries:
                # Popitem(last=False) removes the least recently used
                self._cache.popitem(last=False)
                self._evictions += 1
            self._cache[key] = (time.time(), result)
            self._cache.move_to_end(key)

    def clear(self) -> None:
        """Clear all cached entries."""
        with self._lock:
            self._cache.clear()

    def reset_stats(self) -> None:
        """Reset hit/miss/eviction counters. Does not clear the cache."""
        with self._lock:
            self._hits = 0
            self._misses = 0
            self._evictions = 0

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    @property
    def stats(self) -> dict[str, int]:
        """Return cache statistics."""
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._cache),
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "hit_rate": round(self._hits / total, 4) if total > 0 else 0.0,
            }

    def wrap_invoke(self, invoke_fn):
        """Wrap an invoke function with caching."""

        def cached_invoke(*, agent_name: str, message: str, thread_id: str | None = None, **kwargs):
            # Skip cache if metadata contains "no_cache"
            if kwargs.get("metadata", {}).get("no_cache"):
                return invoke_fn(agent_name=agent_name, message=message, thread_id=thread_id, **kwargs)

            cached = self.get(agent_name, message, thread_id)
            if cached is not None:
                cached["_cached"] = True
                return cached

            result = invoke_fn(agent_name=agent_name, message=message, thread_id=thread_id, **kwargs)
            self.set(agent_name, message, result, thread_id)
            return result

        return cached_invoke


@register_middleware("cache", meta=_CACHE_META)
def build_cache(context: dict[str, Any] | None = None):
    """Build cache middleware from agent config context."""
    context = context or {}
    agent_config = context.get("agent_config")
    ttl_seconds = 3600
    max_entries = 1000
    if agent_config and hasattr(agent_config, "middleware_config"):
        cfg = agent_config.middleware_config.get("cache", {})
        ttl_seconds = cfg.get("ttl_seconds", 3600)
        max_entries = cfg.get("max_entries", 1000)
    return CacheMiddleware(ttl_seconds=ttl_seconds, max_entries=max_entries)
