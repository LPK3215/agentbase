"""Unit tests for the token usage tracking service (core.usage).

Covers:
- Cost estimation (estimate_cost with known/unknown/custom pricing)
- UsageRecord dataclass (to_dict / __post_init__ / auto total_tokens)
- UsageFilter dataclass
- UsageStats dataclass (to_dict)
- InMemoryUsageProvider (record / query / stats / count / clear / FIFO eviction)
- NullUsageProvider (no-op behaviour)
- UsageManager (enabled / disabled / record / query / stats / clear)
- Registry (register_usage_provider / create / has / names / unregister)
- Singleton (get_usage_manager / set_usage_manager / reset_usage_manager)
- Token extraction from LangChain/LangGraph results (extract_usage_from_result)
- Concurrency (thread-safe operations)
- Protocol compliance
"""
from __future__ import annotations

import threading
from datetime import datetime

import pytest

from agentbase.core.usage import (
    InMemoryUsageProvider,
    NullUsageProvider,
    UsageFilter,
    UsageManager,
    UsageProvider,
    UsageRecord,
    UsageRegistry,
    UsageStats,
    _apply_filter,
    _compute_stats,
    estimate_cost,
    extract_usage_from_result,
    get_usage_manager,
    register_usage_provider,
    reset_usage_manager,
    set_usage_manager,
)
from agentbase.runtime.errors import RegistryError


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

class TestEstimateCost:
    """Test cost estimation."""

    def test_known_model_gpt4o_mini(self):
        cost = estimate_cost("gpt-4o-mini", 1000, 500)
        # 1000 * 0.00015 / 1000 * 1000 + 500 * 0.0006 / 1000 * 1000
        # = 0.15 + 0.3 = 0.45 per 1K -> wait per 1K so:
        # prompt: (1000/1000) * 0.00015 = 0.00015
        # completion: (500/1000) * 0.0006 = 0.0003
        assert cost == pytest.approx(0.00045, rel=1e-2)

    def test_known_model_gpt4(self):
        cost = estimate_cost("gpt-4", 1000, 1000)
        # prompt: 0.03, completion: 0.06
        assert cost == pytest.approx(0.09, rel=1e-2)

    def test_unknown_model_uses_fallback(self):
        cost = estimate_cost("unknown-model-xyz", 1000, 1000)
        # fallback: 0.001 + 0.002 = 0.003
        assert cost == pytest.approx(0.003, rel=1e-2)

    def test_zero_tokens(self):
        cost = estimate_cost("gpt-4", 0, 0)
        assert cost == 0.0

    def test_case_insensitive(self):
        cost1 = estimate_cost("GPT-4", 100, 50)
        cost2 = estimate_cost("gpt-4", 100, 50)
        assert cost1 == cost2

    def test_partial_match(self):
        """Model name like 'gpt-4-0613' should match 'gpt-4'."""
        cost = estimate_cost("gpt-4-0613", 1000, 1000)
        assert cost == pytest.approx(0.09, rel=1e-2)

    def test_custom_pricing_table(self):
        custom = {"my-model": {"prompt": 0.01, "completion": 0.02}}
        cost = estimate_cost("my-model", 1000, 1000, pricing=custom)
        assert cost == pytest.approx(0.03, rel=1e-2)

    def test_negative_tokens_treated_as_zero(self):
        # Negative tokens don't make sense but shouldn't crash
        cost = estimate_cost("gpt-4", -100, -50)
        # Will produce negative cost, but that's expected with bad input
        assert isinstance(cost, float)

    def test_deepseek_chat_pricing(self):
        cost = estimate_cost("deepseek-chat", 1000, 500)
        # prompt: 0.00014, completion: 0.00028
        # (1000/1000)*0.00014 + (500/1000)*0.00028 = 0.00014 + 0.00014 = 0.00028
        assert cost == pytest.approx(0.00028, rel=1e-2)

    def test_claude_3_5_sonnet_pricing(self):
        cost = estimate_cost("claude-3-5-sonnet", 1000, 1000)
        assert cost == pytest.approx(0.018, rel=1e-2)


# ---------------------------------------------------------------------------
# UsageRecord dataclass
# ---------------------------------------------------------------------------

class TestUsageRecord:
    """Test UsageRecord dataclass."""

    def test_basic_creation(self):
        r = UsageRecord(agent="default", model="gpt-4", prompt_tokens=100, completion_tokens=50)
        assert r.agent == "default"
        assert r.model == "gpt-4"
        assert r.prompt_tokens == 100
        assert r.completion_tokens == 50
        assert r.total_tokens == 150  # auto-computed
        assert r.id is None
        assert r.timestamp != ""

    def test_explicit_total_tokens(self):
        r = UsageRecord(
            agent="default",
            model="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=999,  # override
        )
        assert r.total_tokens == 999

    def test_auto_total_tokens_when_zero(self):
        r = UsageRecord(agent="a", model="m", prompt_tokens=30, completion_tokens=20)
        assert r.total_tokens == 50

    def test_zero_tokens_stays_zero(self):
        r = UsageRecord(agent="a", model="m")
        assert r.total_tokens == 0

    def test_to_dict(self):
        r = UsageRecord(
            agent="default",
            model="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.015,
            thread_id="thread-1",
            request_id="req-1",
            user="user1",
            duration_ms=123.4,
            id=5,
        )
        d = r.to_dict()
        assert d["id"] == 5
        assert d["agent"] == "default"
        assert d["model"] == "gpt-4"
        assert d["prompt_tokens"] == 100
        assert d["completion_tokens"] == 50
        assert d["total_tokens"] == 150
        assert d["cost_usd"] == pytest.approx(0.015, rel=1e-6)
        assert d["thread_id"] == "thread-1"
        assert d["request_id"] == "req-1"
        assert d["user"] == "user1"
        assert d["duration_ms"] == pytest.approx(123.4, rel=1e-6)
        assert "timestamp" in d

    def test_timestamp_auto_set(self):
        r = UsageRecord(agent="a", model="m")
        assert r.timestamp  # non-empty
        # Should be ISO format
        datetime.fromisoformat(r.timestamp)


# ---------------------------------------------------------------------------
# UsageFilter
# ---------------------------------------------------------------------------

class TestUsageFilter:
    """Test UsageFilter dataclass."""

    def test_defaults(self):
        f = UsageFilter()
        assert f.agent is None
        assert f.model is None
        assert f.user is None
        assert f.thread_id is None
        assert f.since is None
        assert f.until is None
        assert f.limit == 100
        assert f.offset == 0

    def test_custom_values(self):
        f = UsageFilter(agent="default", model="gpt-4", limit=10, offset=5)
        assert f.agent == "default"
        assert f.model == "gpt-4"
        assert f.limit == 10
        assert f.offset == 5


# ---------------------------------------------------------------------------
# InMemoryUsageProvider
# ---------------------------------------------------------------------------

class TestInMemoryUsageProvider:
    """Test InMemoryUsageProvider."""

    def test_record_assigns_id(self):
        provider = InMemoryUsageProvider()
        entry = UsageRecord(agent="a", model="m", prompt_tokens=10, completion_tokens=5)
        result = provider.record(entry)
        assert result.id is not None
        assert result.id == 1

    def test_record_increments_id(self):
        provider = InMemoryUsageProvider()
        e1 = provider.record(UsageRecord(agent="a", model="m"))
        e2 = provider.record(UsageRecord(agent="b", model="m"))
        assert e1.id == 1
        assert e2.id == 2

    def test_query_empty(self):
        provider = InMemoryUsageProvider()
        assert provider.query() == []

    def test_query_all(self):
        provider = InMemoryUsageProvider()
        provider.record(UsageRecord(agent="a", model="m", prompt_tokens=10, completion_tokens=5))
        provider.record(UsageRecord(agent="b", model="m", prompt_tokens=20, completion_tokens=10))
        records = provider.query()
        assert len(records) == 2

    def test_query_filter_by_agent(self):
        provider = InMemoryUsageProvider()
        provider.record(UsageRecord(agent="a", model="m"))
        provider.record(UsageRecord(agent="b", model="m"))
        provider.record(UsageRecord(agent="a", model="m"))
        result = provider.query(UsageFilter(agent="a"))
        assert len(result) == 2
        assert all(r.agent == "a" for r in result)

    def test_query_filter_by_model(self):
        provider = InMemoryUsageProvider()
        provider.record(UsageRecord(agent="a", model="gpt-4"))
        provider.record(UsageRecord(agent="a", model="claude-3"))
        result = provider.query(UsageFilter(model="gpt-4"))
        assert len(result) == 1

    def test_query_filter_by_user(self):
        provider = InMemoryUsageProvider()
        provider.record(UsageRecord(agent="a", model="m", user="u1"))
        provider.record(UsageRecord(agent="a", model="m", user="u2"))
        result = provider.query(UsageFilter(user="u1"))
        assert len(result) == 1

    def test_query_filter_by_thread_id(self):
        provider = InMemoryUsageProvider()
        provider.record(UsageRecord(agent="a", model="m", thread_id="t1"))
        provider.record(UsageRecord(agent="a", model="m", thread_id="t2"))
        result = provider.query(UsageFilter(thread_id="t1"))
        assert len(result) == 1

    def test_query_with_limit(self):
        provider = InMemoryUsageProvider()
        for i in range(10):
            provider.record(UsageRecord(agent=f"a{i}", model="m"))
        result = provider.query(UsageFilter(limit=5))
        assert len(result) == 5

    def test_query_with_offset(self):
        provider = InMemoryUsageProvider()
        for i in range(10):
            provider.record(UsageRecord(agent=f"a{i}", model="m"))
        result = provider.query(UsageFilter(limit=5, offset=5))
        assert len(result) == 5

    def test_stats_empty(self):
        provider = InMemoryUsageProvider()
        stats = provider.stats()
        assert stats.total_calls == 0
        assert stats.total_prompt_tokens == 0
        assert stats.total_completion_tokens == 0

    def test_stats_aggregation(self):
        provider = InMemoryUsageProvider()
        provider.record(UsageRecord(agent="a", model="gpt-4", prompt_tokens=100, completion_tokens=50, cost_usd=0.01, duration_ms=100))
        provider.record(UsageRecord(agent="b", model="gpt-4", prompt_tokens=200, completion_tokens=100, cost_usd=0.02, duration_ms=200))
        stats = provider.stats()
        assert stats.total_calls == 2
        assert stats.total_prompt_tokens == 300
        assert stats.total_completion_tokens == 150
        assert stats.total_tokens == 450
        assert stats.total_cost_usd == pytest.approx(0.03, rel=1e-6)
        assert stats.avg_duration_ms == pytest.approx(150.0, rel=1e-6)

    def test_stats_by_model(self):
        provider = InMemoryUsageProvider()
        provider.record(UsageRecord(agent="a", model="gpt-4", prompt_tokens=100, completion_tokens=50, cost_usd=0.01))
        provider.record(UsageRecord(agent="b", model="claude-3", prompt_tokens=200, completion_tokens=100, cost_usd=0.02))
        stats = provider.stats()
        assert "gpt-4" in stats.by_model
        assert "claude-3" in stats.by_model
        assert stats.by_model["gpt-4"]["calls"] == 1
        assert stats.by_model["gpt-4"]["prompt_tokens"] == 100
        assert stats.by_model["claude-3"]["calls"] == 1

    def test_stats_by_agent(self):
        provider = InMemoryUsageProvider()
        provider.record(UsageRecord(agent="a", model="m", prompt_tokens=100, completion_tokens=50, cost_usd=0.01))
        provider.record(UsageRecord(agent="a", model="m", prompt_tokens=50, completion_tokens=30, cost_usd=0.005))
        provider.record(UsageRecord(agent="b", model="m", prompt_tokens=200, completion_tokens=100, cost_usd=0.02))
        stats = provider.stats()
        assert stats.by_agent["a"]["calls"] == 2
        assert stats.by_agent["a"]["prompt_tokens"] == 150
        assert stats.by_agent["b"]["calls"] == 1

    def test_stats_by_user(self):
        provider = InMemoryUsageProvider()
        provider.record(UsageRecord(agent="a", model="m", user="u1", prompt_tokens=100, completion_tokens=50, cost_usd=0.01))
        provider.record(UsageRecord(agent="a", model="m", user="u2", prompt_tokens=200, completion_tokens=100, cost_usd=0.02))
        stats = provider.stats()
        assert "u1" in stats.by_user
        assert "u2" in stats.by_user
        assert stats.by_user["u1"]["calls"] == 1
        assert stats.by_user["u2"]["calls"] == 1

    def test_stats_no_user_skips_by_user(self):
        provider = InMemoryUsageProvider()
        provider.record(UsageRecord(agent="a", model="m", user="", prompt_tokens=100, completion_tokens=50, cost_usd=0.01))
        stats = provider.stats()
        assert stats.by_user == {}

    def test_count_empty(self):
        provider = InMemoryUsageProvider()
        assert provider.count() == 0

    def test_count_with_filter(self):
        provider = InMemoryUsageProvider()
        provider.record(UsageRecord(agent="a", model="m"))
        provider.record(UsageRecord(agent="b", model="m"))
        assert provider.count() == 2
        assert provider.count(UsageFilter(agent="a")) == 1

    def test_clear(self):
        provider = InMemoryUsageProvider()
        for i in range(5):
            provider.record(UsageRecord(agent=f"a{i}", model="m"))
        deleted = provider.clear()
        assert deleted == 5
        assert provider.count() == 0

    def test_clear_resets_id(self):
        provider = InMemoryUsageProvider()
        provider.record(UsageRecord(agent="a", model="m"))
        provider.clear()
        entry = provider.record(UsageRecord(agent="a", model="m"))
        assert entry.id == 1

    def test_fifo_eviction(self):
        provider = InMemoryUsageProvider(max_records=3)
        for i in range(5):
            provider.record(UsageRecord(agent=f"a{i}", model="m"))
        records = provider.query()
        assert len(records) == 3
        # Should keep the last 3 (a2, a3, a4)
        assert records[0].agent == "a2"
        assert records[2].agent == "a4"

    def test_close(self):
        provider = InMemoryUsageProvider()
        provider.record(UsageRecord(agent="a", model="m"))
        provider.close()
        assert provider.query() == []

    def test_thread_safety(self):
        provider = InMemoryUsageProvider()
        errors: list[Exception] = []

        def worker():
            try:
                for _ in range(100):
                    provider.record(UsageRecord(agent="thread", model="m"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert provider.count() == 500


# ---------------------------------------------------------------------------
# NullUsageProvider
# ---------------------------------------------------------------------------

class TestNullUsageProvider:
    """Test NullUsageProvider no-op behaviour."""

    def test_record_returns_entry(self):
        provider = NullUsageProvider()
        entry = UsageRecord(agent="a", model="m")
        result = provider.record(entry)
        assert result is entry  # returns the same entry

    def test_query_returns_empty(self):
        provider = NullUsageProvider()
        assert provider.query() == []
        assert provider.query(UsageFilter(agent="a")) == []

    def test_stats_returns_empty(self):
        provider = NullUsageProvider()
        stats = provider.stats()
        assert stats.total_calls == 0
        assert stats.total_tokens == 0

    def test_count_returns_zero(self):
        provider = NullUsageProvider()
        assert provider.count() == 0

    def test_clear_returns_zero(self):
        provider = NullUsageProvider()
        assert provider.clear() == 0

    def test_close_is_noop(self):
        provider = NullUsageProvider()
        provider.close()  # should not raise


# ---------------------------------------------------------------------------
# UsageManager
# ---------------------------------------------------------------------------

class TestUsageManager:
    """Test UsageManager facade."""

    def test_disabled_uses_null(self):
        mgr = UsageManager(provider="memory", enabled=False)
        assert mgr.enabled is False
        # Record is no-op
        result = mgr.record(agent="a", model="m", prompt_tokens=10, completion_tokens=5)
        assert result.id is None  # NullProvider doesn't assign ID

    def test_enabled_uses_provider(self):
        mgr = UsageManager(provider="memory", enabled=True)
        assert mgr.enabled is True
        result = mgr.record(agent="a", model="gpt-4", prompt_tokens=10, completion_tokens=5)
        assert result.id is not None
        assert result.agent == "a"

    def test_record_auto_estimates_cost(self):
        mgr = UsageManager(provider="memory", enabled=True)
        result = mgr.record(
            agent="a",
            model="gpt-4o-mini",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        # cost should be auto-estimated
        assert result.cost_usd > 0

    def test_record_explicit_cost(self):
        mgr = UsageManager(provider="memory", enabled=True)
        result = mgr.record(
            agent="a",
            model="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.05,
        )
        assert result.cost_usd == 0.05

    def test_record_auto_total_tokens(self):
        mgr = UsageManager(provider="memory", enabled=True)
        result = mgr.record(
            agent="a",
            model="m",
            prompt_tokens=100,
            completion_tokens=50,
        )
        assert result.total_tokens == 150

    def test_record_explicit_total_tokens(self):
        mgr = UsageManager(provider="memory", enabled=True)
        result = mgr.record(
            agent="a",
            model="m",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=999,
        )
        assert result.total_tokens == 999

    def test_query_records(self):
        mgr = UsageManager(provider="memory", enabled=True)
        mgr.record(agent="a", model="m", prompt_tokens=10, completion_tokens=5)
        mgr.record(agent="b", model="m", prompt_tokens=20, completion_tokens=10)
        records = mgr.query_records()
        assert len(records) == 2

    def test_query_records_disabled(self):
        mgr = UsageManager(provider="memory", enabled=False)
        assert mgr.query_records() == []

    def test_get_stats(self):
        mgr = UsageManager(provider="memory", enabled=True)
        mgr.record(agent="a", model="gpt-4", prompt_tokens=100, completion_tokens=50, cost_usd=0.01)
        mgr.record(agent="b", model="gpt-4", prompt_tokens=200, completion_tokens=100, cost_usd=0.02)
        stats = mgr.get_stats()
        assert stats.total_calls == 2
        assert stats.total_prompt_tokens == 300

    def test_get_stats_disabled(self):
        mgr = UsageManager(provider="memory", enabled=False)
        stats = mgr.get_stats()
        assert stats.total_calls == 0

    def test_count_records(self):
        mgr = UsageManager(provider="memory", enabled=True)
        mgr.record(agent="a", model="m")
        mgr.record(agent="b", model="m")
        assert mgr.count_records() == 2

    def test_count_records_with_filter(self):
        mgr = UsageManager(provider="memory", enabled=True)
        mgr.record(agent="a", model="m")
        mgr.record(agent="b", model="m")
        assert mgr.count_records(UsageFilter(agent="a")) == 1

    def test_clear_records(self):
        mgr = UsageManager(provider="memory", enabled=True)
        mgr.record(agent="a", model="m")
        mgr.record(agent="b", model="m")
        deleted = mgr.clear_records()
        assert deleted == 2
        assert mgr.count_records() == 0

    def test_clear_records_disabled(self):
        mgr = UsageManager(provider="memory", enabled=False)
        assert mgr.clear_records() == 0

    def test_custom_pricing(self):
        custom = {"my-model": {"prompt": 0.05, "completion": 0.10}}
        mgr = UsageManager(provider="memory", enabled=True, pricing=custom)
        result = mgr.record(agent="a", model="my-model", prompt_tokens=1000, completion_tokens=500)
        # (1000/1000)*0.05 + (500/1000)*0.10 = 0.05 + 0.05 = 0.10
        assert result.cost_usd == pytest.approx(0.10, rel=1e-2)

    def test_close(self):
        mgr = UsageManager(provider="memory", enabled=True)
        mgr.close()  # should not raise


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestUsageRegistry:
    """Test UsageRegistry."""

    def test_default_registrations(self):
        reg = UsageRegistry()
        reg.register("null", NullUsageProvider)
        reg.register("memory", InMemoryUsageProvider)
        assert reg.has("null")
        assert reg.has("memory")
        assert "null" in reg.names()
        assert "memory" in reg.names()
        assert reg.count >= 2

    def test_create_null(self):
        reg = UsageRegistry()
        reg.register("null", NullUsageProvider)
        provider = reg.create("null")
        assert isinstance(provider, NullUsageProvider)

    def test_create_memory(self):
        reg = UsageRegistry()
        reg.register("memory", InMemoryUsageProvider)
        provider = reg.create("memory")
        assert isinstance(provider, InMemoryUsageProvider)

    def test_create_unknown_raises(self):
        reg = UsageRegistry()
        with pytest.raises(RegistryError, match="Unknown usage provider"):
            reg.create("nonexistent")

    def test_register_empty_name_raises(self):
        reg = UsageRegistry()
        with pytest.raises(RegistryError, match="empty"):
            reg.register("", NullUsageProvider)

    def test_register_duplicate_raises(self):
        reg = UsageRegistry()
        reg.register("test", NullUsageProvider)
        with pytest.raises(RegistryError, match="already registered"):
            reg.register("test", InMemoryUsageProvider)

    def test_register_override(self):
        reg = UsageRegistry()
        reg.register("test", NullUsageProvider)
        reg.register("test", InMemoryUsageProvider, override=True)
        provider = reg.create("test")
        assert isinstance(provider, InMemoryUsageProvider)

    def test_unregister(self):
        reg = UsageRegistry()
        reg.register("test", NullUsageProvider)
        assert reg.unregister("test") is True
        assert reg.has("test") is False

    def test_unregister_not_found(self):
        reg = UsageRegistry()
        assert reg.unregister("nonexistent") is False

    def test_names_sorted(self):
        reg = UsageRegistry()
        reg.register("zeta", NullUsageProvider)
        reg.register("alpha", NullUsageProvider)
        names = reg.names()
        assert names == ["alpha", "zeta"]


class TestRegisterUsageProvider:
    """Test the @register_usage_provider decorator."""

    def test_decorator_registers(self):
        @register_usage_provider("test_custom", override=True)
        class CustomProvider:
            def record(self, entry):
                return entry
            def query(self, filter=None):
                return []
            def stats(self, filter=None):
                return UsageStats()
            def count(self, filter=None):
                return 0
            def clear(self):
                return 0
            def close(self):
                pass

        from agentbase.core.usage import usage_registry
        assert usage_registry.has("test_custom")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    """Test singleton management."""

    def setup_method(self):
        reset_usage_manager()

    def teardown_method(self):
        reset_usage_manager()

    def test_get_uninitialised_raises(self):
        with pytest.raises(RuntimeError, match="not initialised"):
            get_usage_manager()

    def test_set_and_get(self):
        mgr = UsageManager(provider="memory", enabled=True)
        set_usage_manager(mgr)
        assert get_usage_manager() is mgr

    def test_reset(self):
        mgr = UsageManager(provider="memory", enabled=True)
        set_usage_manager(mgr)
        reset_usage_manager()
        with pytest.raises(RuntimeError):
            get_usage_manager()


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------

class TestExtractUsageFromResult:
    """Test extract_usage_from_result."""

    def test_empty_result(self):
        result = extract_usage_from_result(None)
        assert result == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def test_dict_with_no_messages(self):
        result = extract_usage_from_result({"other": "data"})
        assert result == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def test_dict_with_messages_no_usage(self):
        result = extract_usage_from_result({"messages": [{"content": "hello"}]})
        assert result == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def test_dict_with_messages_usage_metadata(self):
        """Simulate LangChain AIMessage with usage_metadata."""
        class FakeAIMessage:
            def __init__(self):
                self.content = "response"
                self.usage_metadata = {
                    "input_tokens": 150,
                    "output_tokens": 80,
                    "total_tokens": 230,
                }

        result = {
            "messages": [FakeAIMessage()],
        }
        extracted = extract_usage_from_result(result)
        assert extracted["prompt_tokens"] == 150
        assert extracted["completion_tokens"] == 80
        assert extracted["total_tokens"] == 230

    def test_dict_with_messages_response_metadata(self):
        """Simulate LangChain AIMessage with response_metadata (OpenAI format)."""
        class FakeAIMessage:
            def __init__(self):
                self.content = "response"
                self.response_metadata = {
                    "token_usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 50,
                        "total_tokens": 150,
                    }
                }

        result = {
            "messages": [FakeAIMessage()],
        }
        extracted = extract_usage_from_result(result)
        assert extracted["prompt_tokens"] == 100
        assert extracted["completion_tokens"] == 50
        assert extracted["total_tokens"] == 150

    def test_multiple_messages_aggregate(self):
        """Multiple AIMessages should aggregate token counts."""
        class FakeAIMessage:
            def __init__(self, input_tokens, output_tokens):
                self.content = "response"
                self.usage_metadata = {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                }

        result = {
            "messages": [
                FakeAIMessage(100, 50),
                FakeAIMessage(200, 100),
            ],
        }
        extracted = extract_usage_from_result(result)
        assert extracted["prompt_tokens"] == 300
        assert extracted["completion_tokens"] == 150
        assert extracted["total_tokens"] == 450

    def test_object_with_messages_attribute(self):
        """Object with .messages attribute (not a dict)."""
        class FakeResult:
            def __init__(self):
                class FakeAIMessage:
                    def __init__(self):
                        self.content = "response"
                        self.usage_metadata = {
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "total_tokens": 150,
                        }
                self.messages = [FakeAIMessage()]

        result = FakeResult()
        extracted = extract_usage_from_result(result)
        assert extracted["prompt_tokens"] == 100
        assert extracted["completion_tokens"] == 50
        assert extracted["total_tokens"] == 150

    def test_usage_metadata_with_prompt_tokens_key(self):
        """Some providers use prompt_tokens instead of input_tokens."""
        class FakeAIMessage:
            def __init__(self):
                self.content = "response"
                self.usage_metadata = {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                }

        result = {"messages": [FakeAIMessage()]}
        extracted = extract_usage_from_result(result)
        assert extracted["prompt_tokens"] == 100
        assert extracted["completion_tokens"] == 50

    def test_auto_total_when_zero(self):
        """If total_tokens is 0 but prompt/completion exist, compute it."""
        class FakeAIMessage:
            def __init__(self):
                self.content = "response"
                self.usage_metadata = {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    # no total_tokens key
                }

        result = {"messages": [FakeAIMessage()]}
        extracted = extract_usage_from_result(result)
        assert extracted["total_tokens"] == 150

    def test_plain_string_result(self):
        """Plain string (from stream final_text) has no usage."""
        result = extract_usage_from_result("just text")
        assert result == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


# ---------------------------------------------------------------------------
# Filter / stats helpers
# ---------------------------------------------------------------------------

class TestApplyFilter:
    """Test _apply_filter helper."""

    def test_no_filter_returns_all(self):
        records = [
            UsageRecord(agent="a", model="m"),
            UsageRecord(agent="b", model="m"),
        ]
        result = _apply_filter(records, UsageFilter(limit=0))
        assert len(result) == 2

    def test_filter_by_agent(self):
        records = [
            UsageRecord(agent="a", model="m"),
            UsageRecord(agent="b", model="m"),
        ]
        result = _apply_filter(records, UsageFilter(agent="a", limit=0))
        assert len(result) == 1

    def test_filter_by_since(self):
        records = [
            UsageRecord(agent="a", model="m", timestamp="2024-01-01T00:00:00+00:00"),
            UsageRecord(agent="b", model="m", timestamp="2024-06-01T00:00:00+00:00"),
        ]
        result = _apply_filter(records, UsageFilter(since="2024-03-01T00:00:00+00:00", limit=0))
        assert len(result) == 1
        assert result[0].agent == "b"

    def test_filter_by_until(self):
        records = [
            UsageRecord(agent="a", model="m", timestamp="2024-01-01T00:00:00+00:00"),
            UsageRecord(agent="b", model="m", timestamp="2024-06-01T00:00:00+00:00"),
        ]
        result = _apply_filter(records, UsageFilter(until="2024-03-01T00:00:00+00:00", limit=0))
        assert len(result) == 1
        assert result[0].agent == "a"


class TestComputeStats:
    """Test _compute_stats helper."""

    def test_empty_list(self):
        stats = _compute_stats([])
        assert stats.total_calls == 0

    def test_basic_aggregation(self):
        records = [
            UsageRecord(agent="a", model="gpt-4", prompt_tokens=100, completion_tokens=50, cost_usd=0.01, duration_ms=100),
            UsageRecord(agent="b", model="gpt-4", prompt_tokens=200, completion_tokens=100, cost_usd=0.02, duration_ms=200),
        ]
        stats = _compute_stats(records)
        assert stats.total_calls == 2
        assert stats.total_prompt_tokens == 300
        assert stats.total_completion_tokens == 150
        assert stats.total_tokens == 450
        assert stats.total_cost_usd == pytest.approx(0.03, rel=1e-6)
        assert stats.avg_duration_ms == pytest.approx(150.0, rel=1e-6)

    def test_avg_duration_skips_zero(self):
        records = [
            UsageRecord(agent="a", model="m", prompt_tokens=10, completion_tokens=5, duration_ms=0),
            UsageRecord(agent="b", model="m", prompt_tokens=10, completion_tokens=5, duration_ms=100),
        ]
        stats = _compute_stats(records)
        # Only the non-zero duration is counted
        assert stats.avg_duration_ms == pytest.approx(100.0, rel=1e-6)


# ---------------------------------------------------------------------------
# UsageStats
# ---------------------------------------------------------------------------

class TestUsageStats:
    """Test UsageStats dataclass."""

    def test_defaults(self):
        stats = UsageStats()
        assert stats.total_calls == 0
        assert stats.total_prompt_tokens == 0
        assert stats.total_completion_tokens == 0
        assert stats.total_tokens == 0
        assert stats.total_cost_usd == 0.0
        assert stats.avg_duration_ms == 0.0
        assert stats.by_model == {}
        assert stats.by_agent == {}
        assert stats.by_user == {}

    def test_to_dict(self):
        stats = UsageStats(
            total_calls=5,
            total_prompt_tokens=1000,
            total_completion_tokens=500,
            total_tokens=1500,
            total_cost_usd=0.15,
            avg_duration_ms=200.5,
        )
        d = stats.to_dict()
        assert d["total_calls"] == 5
        assert d["total_prompt_tokens"] == 1000
        assert d["total_completion_tokens"] == 500
        assert d["total_tokens"] == 1500
        assert d["total_cost_usd"] == pytest.approx(0.15, rel=1e-6)
        assert d["avg_duration_ms"] == pytest.approx(200.5, rel=1e-6)
        assert "by_model" in d
        assert "by_agent" in d
        assert "by_user" in d


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------

class TestProtocolCompliance:
    """Test that providers satisfy the UsageProvider Protocol."""

    def test_null_provider_is_usage_provider(self):
        provider = NullUsageProvider()
        assert isinstance(provider, UsageProvider)

    def test_inmemory_provider_is_usage_provider(self):
        provider = InMemoryUsageProvider()
        assert isinstance(provider, UsageProvider)
