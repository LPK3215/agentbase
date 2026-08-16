"""Unit tests for cache middleware — CacheMiddleware + build_cache factory.

Covers: key generation, get/set, TTL expiry, LRU eviction, stats,
clear, reset_stats, wrap_invoke (hit/miss/no_cache), build_cache factory.
"""
from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentbase.extensions.middleware.cache import CacheMiddleware, build_cache


# ---------------------------------------------------------------------------
# _make_key
# ---------------------------------------------------------------------------


class TestMakeKey:
    def test_key_is_deterministic(self):
        cm = CacheMiddleware()
        k1 = cm._make_key("agent1", "hello", "t1")
        k2 = cm._make_key("agent1", "hello", "t1")
        assert k1 == k2

    def test_key_differs_by_agent_name(self):
        cm = CacheMiddleware()
        k1 = cm._make_key("agent1", "hello", "t1")
        k2 = cm._make_key("agent2", "hello", "t1")
        assert k1 != k2

    def test_key_differs_by_message(self):
        cm = CacheMiddleware()
        k1 = cm._make_key("agent1", "hello", "t1")
        k2 = cm._make_key("agent1", "world", "t1")
        assert k1 != k2

    def test_key_differs_by_thread_id(self):
        cm = CacheMiddleware()
        k1 = cm._make_key("agent1", "hello", "t1")
        k2 = cm._make_key("agent1", "hello", "t2")
        assert k1 != k2

    def test_key_with_none_thread_id(self):
        cm = CacheMiddleware()
        k = cm._make_key("agent1", "hello", None)
        assert isinstance(k, str)
        assert len(k) == 64  # sha256 hex digest length


# ---------------------------------------------------------------------------
# get / set
# ---------------------------------------------------------------------------


class TestGetSet:
    def test_set_then_get(self):
        cm = CacheMiddleware()
        cm.set("agent1", "hello", {"response": "world"}, "t1")
        result = cm.get("agent1", "hello", "t1")
        assert result == {"response": "world"}

    def test_get_miss(self):
        cm = CacheMiddleware()
        result = cm.get("agent1", "hello", "t1")
        assert result is None
        assert cm.stats["misses"] == 1

    def test_get_hit_increments_counter(self):
        cm = CacheMiddleware()
        cm.set("agent1", "hello", {"response": "world"}, "t1")
        cm.get("agent1", "hello", "t1")
        assert cm.stats["hits"] == 1
        assert cm.stats["misses"] == 0

    def test_get_with_none_thread_id(self):
        cm = CacheMiddleware()
        cm.set("agent1", "hello", {"response": "world"}, None)
        result = cm.get("agent1", "hello", None)
        assert result == {"response": "world"}


# ---------------------------------------------------------------------------
# TTL expiry
# ---------------------------------------------------------------------------


class TestTTLExpiry:
    def test_expired_entry_returns_none(self):
        cm = CacheMiddleware(ttl_seconds=0)
        cm.set("agent1", "hello", {"response": "world"}, "t1")
        # With ttl=0, entry should be expired immediately
        time.sleep(0.01)
        result = cm.get("agent1", "hello", "t1")
        assert result is None
        assert cm.stats["misses"] == 1

    def test_non_expired_entry_returned(self):
        cm = CacheMiddleware(ttl_seconds=3600)
        cm.set("agent1", "hello", {"response": "world"}, "t1")
        result = cm.get("agent1", "hello", "t1")
        assert result == {"response": "world"}

    def test_expired_entry_removed_from_cache(self):
        cm = CacheMiddleware(ttl_seconds=0)
        cm.set("agent1", "hello", {"response": "world"}, "t1")
        time.sleep(0.01)
        cm.get("agent1", "hello", "t1")
        assert cm.size == 0


# ---------------------------------------------------------------------------
# LRU eviction
# ---------------------------------------------------------------------------


class TestLRUEviction:
    def test_eviction_on_capacity(self):
        cm = CacheMiddleware(max_entries=2)
        cm.set("a1", "m1", "r1", "t1")
        cm.set("a1", "m2", "r2", "t1")
        cm.set("a1", "m3", "r3", "t1")  # should evict m1 (LRU)
        assert cm.size == 2
        assert cm.get("a1", "m1", "t1") is None  # evicted
        assert cm.get("a1", "m3", "t1") == "r3"
        assert cm.stats["evictions"] == 1

    def test_lru_order_updated_on_get(self):
        cm = CacheMiddleware(max_entries=2)
        cm.set("a1", "m1", "r1", "t1")
        cm.set("a1", "m2", "r2", "t1")
        # Access m1 to make it most recently used
        cm.get("a1", "m1", "t1")
        # Add m3 — should evict m2 (now LRU)
        cm.set("a1", "m3", "r3", "t1")
        assert cm.get("a1", "m1", "t1") == "r1"  # still present
        assert cm.get("a1", "m2", "t1") is None  # evicted

    def test_no_eviction_when_under_capacity(self):
        cm = CacheMiddleware(max_entries=5)
        cm.set("a1", "m1", "r1", "t1")
        cm.set("a1", "m2", "r2", "t1")
        assert cm.stats["evictions"] == 0


# ---------------------------------------------------------------------------
# clear + reset_stats
# ---------------------------------------------------------------------------


class TestClearAndReset:
    def test_clear_empties_cache(self):
        cm = CacheMiddleware()
        cm.set("a1", "m1", "r1", "t1")
        cm.set("a1", "m2", "r2", "t1")
        cm.clear()
        assert cm.size == 0

    def test_reset_stats_only_resets_counters(self):
        cm = CacheMiddleware()
        cm.set("a1", "m1", "r1", "t1")
        cm.get("a1", "m1", "t1")  # hit
        cm.get("a1", "m2", "t1")  # miss
        cm.reset_stats()
        assert cm.stats["hits"] == 0
        assert cm.stats["misses"] == 0
        assert cm.size == 1  # cache not cleared

    def test_reset_stats_resets_evictions(self):
        cm = CacheMiddleware(max_entries=1)
        cm.set("a1", "m1", "r1", "t1")
        cm.set("a1", "m2", "r2", "t1")  # evicts m1
        cm.reset_stats()
        assert cm.stats["evictions"] == 0


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


class TestStats:
    def test_initial_stats(self):
        cm = CacheMiddleware()
        stats = cm.stats
        assert stats["size"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["evictions"] == 0
        assert stats["hit_rate"] == 0.0

    def test_hit_rate_calculation(self):
        cm = CacheMiddleware()
        cm.set("a1", "m1", "r1", "t1")
        cm.get("a1", "m1", "t1")  # hit
        cm.get("a1", "m2", "t1")  # miss
        stats = cm.stats
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5

    def test_hit_rate_zero_division(self):
        cm = CacheMiddleware()
        stats = cm.stats
        assert stats["hit_rate"] == 0.0

    def test_size_property(self):
        cm = CacheMiddleware()
        cm.set("a1", "m1", "r1", "t1")
        cm.set("a1", "m2", "r2", "t1")
        assert cm.size == 2


# ---------------------------------------------------------------------------
# wrap_invoke
# ---------------------------------------------------------------------------


class TestWrapInvoke:
    def test_cache_hit_returns_cached_result(self):
        cm = CacheMiddleware()
        call_count = [0]

        def invoke_fn(*, agent_name, message, thread_id=None, **kwargs):
            call_count[0] += 1
            return {"response": "fresh"}

        cached_fn = cm.wrap_invoke(invoke_fn)

        # First call — miss, invokes function
        result1 = cached_fn(agent_name="a1", message="hello", thread_id="t1")
        assert result1 == {"response": "fresh"}
        assert call_count[0] == 1

        # Second call — hit, returns cached
        result2 = cached_fn(agent_name="a1", message="hello", thread_id="t1")
        assert result2["response"] == "fresh"
        assert result2["_cached"] is True
        assert call_count[0] == 1  # function not called again

    def test_cache_miss_invokes_function(self):
        cm = CacheMiddleware()
        call_count = [0]

        def invoke_fn(*, agent_name, message, thread_id=None, **kwargs):
            call_count[0] += 1
            return {"response": "fresh"}

        cached_fn = cm.wrap_invoke(invoke_fn)
        result = cached_fn(agent_name="a1", message="hello", thread_id="t1")
        assert result == {"response": "fresh"}
        assert call_count[0] == 1

    def test_no_cache_metadata_skips_cache(self):
        cm = CacheMiddleware()
        call_count = [0]

        def invoke_fn(*, agent_name, message, thread_id=None, **kwargs):
            call_count[0] += 1
            return {"response": "fresh"}

        cached_fn = cm.wrap_invoke(invoke_fn)

        # Call with no_cache=True should bypass cache
        result1 = cached_fn(agent_name="a1", message="hello", thread_id="t1",
                            metadata={"no_cache": True})
        result2 = cached_fn(agent_name="a1", message="hello", thread_id="t1",
                            metadata={"no_cache": True})
        assert call_count[0] == 2  # both calls invoked the function
        assert "_cached" not in result1
        assert "_cached" not in result2

    def test_different_message_not_cached(self):
        cm = CacheMiddleware()
        call_count = [0]

        def invoke_fn(*, agent_name, message, thread_id=None, **kwargs):
            call_count[0] += 1
            return {"response": message}

        cached_fn = cm.wrap_invoke(invoke_fn)
        r1 = cached_fn(agent_name="a1", message="hello", thread_id="t1")
        r2 = cached_fn(agent_name="a1", message="world", thread_id="t1")
        assert call_count[0] == 2
        assert r1 == {"response": "hello"}
        assert r2 == {"response": "world"}


# ---------------------------------------------------------------------------
# build_cache factory
# ---------------------------------------------------------------------------


class TestBuildCache:
    def test_default_config(self):
        cm = build_cache(context={})
        assert isinstance(cm, CacheMiddleware)
        assert cm._ttl == 3600
        assert cm._max_entries == 1000

    def test_none_context(self):
        cm = build_cache(context=None)
        assert isinstance(cm, CacheMiddleware)

    def test_with_agent_config(self):
        agent_config = MagicMock()
        agent_config.middleware_config = {
            "cache": {"ttl_seconds": 120, "max_entries": 50}
        }
        cm = build_cache(context={"agent_config": agent_config})
        assert cm._ttl == 120
        assert cm._max_entries == 50

    def test_agent_config_without_middleware_config(self):
        agent_config = MagicMock(spec=[])  # no attributes
        cm = build_cache(context={"agent_config": agent_config})
        assert isinstance(cm, CacheMiddleware)
        assert cm._ttl == 3600
        assert cm._max_entries == 1000

    def test_partial_config_uses_defaults(self):
        agent_config = MagicMock()
        agent_config.middleware_config = {"cache": {"ttl_seconds": 60}}
        cm = build_cache(context={"agent_config": agent_config})
        assert cm._ttl == 60
        assert cm._max_entries == 1000  # default
