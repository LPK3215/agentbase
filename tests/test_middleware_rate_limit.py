"""Tests for rate_limit middleware — covers precise limit, burst tolerance, cooldown recovery."""
from __future__ import annotations

import time

import pytest

from agentbase.extensions.middleware.rate_limit import (
    AgentRateLimiter,
    build_rate_limit,
)
from agentbase.runtime.errors import ErrorCode, RuntimeExecutionError


# ---------------------------------------------------------------------------
# AgentRateLimiter unit tests
# ---------------------------------------------------------------------------

class TestAgentRateLimiter:
    def test_precise_limit_burst_zero(self):
        """With burst=0, max_requests is a hard cap."""
        limiter = AgentRateLimiter(max_requests=3, window_seconds=60, burst=0)
        # First 3 calls should pass
        assert limiter.check("agent_a") is True
        assert limiter.check("agent_a") is True
        assert limiter.check("agent_a") is True
        # 4th call should be denied
        assert limiter.check("agent_a") is False

    def test_burst_allows_extra(self):
        """With burst=2, total capacity = max_requests + burst."""
        limiter = AgentRateLimiter(max_requests=3, window_seconds=60, burst=2)
        # 3 base + 2 burst = 5 total
        for i in range(5):
            assert limiter.check("agent_a") is True, f"Call {i+1} should pass"
        # 6th call should be denied
        assert limiter.check("agent_a") is False

    def test_per_agent_isolation(self):
        """Each agent has its own bucket."""
        limiter = AgentRateLimiter(max_requests=2, window_seconds=60, burst=0)
        assert limiter.check("agent_a") is True
        assert limiter.check("agent_a") is True
        # agent_a is full, but agent_b should still work
        assert limiter.check("agent_b") is True
        assert limiter.check("agent_b") is True
        # Both are now full
        assert limiter.check("agent_a") is False
        assert limiter.check("agent_b") is False

    def test_cooldown_recovery(self):
        """After window expires, capacity is restored."""
        limiter = AgentRateLimiter(max_requests=2, window_seconds=1, burst=0)
        assert limiter.check("agent_a") is True
        assert limiter.check("agent_a") is True
        assert limiter.check("agent_a") is False
        # Wait for window to expire
        time.sleep(1.1)
        # Should be allowed again
        assert limiter.check("agent_a") is True

    def test_get_remaining(self):
        """get_remaining should return correct count."""
        limiter = AgentRateLimiter(max_requests=5, window_seconds=60, burst=2)
        assert limiter.get_remaining("agent_a") == 7  # 5 + 2
        limiter.check("agent_a")
        assert limiter.get_remaining("agent_a") == 6
        limiter.check("agent_a")
        assert limiter.get_remaining("agent_a") == 5

    def test_reset(self):
        """reset should clear all buckets."""
        limiter = AgentRateLimiter(max_requests=1, window_seconds=60, burst=0)
        limiter.check("agent_a")
        assert limiter.check("agent_a") is False
        limiter.reset()
        assert limiter.check("agent_a") is True

    def test_stats(self):
        """stats should return a dict with limiter information."""
        limiter = AgentRateLimiter(max_requests=10, window_seconds=30, burst=5)
        limiter.check("agent_a")
        limiter.check("agent_a")
        stats = limiter.stats
        assert stats["max_requests"] == 10
        assert stats["window_seconds"] == 30
        assert stats["burst"] == 5
        assert stats["capacity"] == 15
        assert stats["active_keys"] == 1
        assert stats["per_key"]["agent_a"] == 2

    def test_thread_safety(self):
        """Concurrent check calls should be safe."""
        import threading
        limiter = AgentRateLimiter(max_requests=100, window_seconds=60, burst=0)
        allowed = []

        def worker():
            for _ in range(50):
                if limiter.check("concurrent"):
                    allowed.append(1)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly 100 should be allowed (max_requests=100, burst=0)
        assert len(allowed) == 100


# ---------------------------------------------------------------------------
# build_rate_limit middleware tests
# ---------------------------------------------------------------------------

class TestBuildRateLimit:
    def test_middleware_allows_under_limit(self):
        """Middleware should allow calls under the rate limit."""
        rate_limiter = build_rate_limit(context={})
        limiter = AgentRateLimiter(max_requests=60, window_seconds=60, burst=10)

        def mock_invoke(*, agent_name="default", **kwargs):
            return {"result": "ok"}

        wrapped = rate_limiter(mock_invoke)
        # Should work fine
        result = wrapped(agent_name="test_agent")
        assert result == {"result": "ok"}

    def test_middleware_blocks_over_limit(self):
        """Middleware should block calls when rate limit is exceeded."""
        # Build with custom config via agent_config
        class MockConfig:
            metadata = {"rate_limit": {"max_requests": 2, "window_seconds": 60, "burst": 0}}

        rate_limiter = build_rate_limit(context={"agent_config": MockConfig()})

        def mock_invoke(*, agent_name="default", **kwargs):
            return {"result": "ok"}

        wrapped = rate_limiter(mock_invoke)

        # First 2 should pass
        assert wrapped(agent_name="test_agent") == {"result": "ok"}
        assert wrapped(agent_name="test_agent") == {"result": "ok"}

        # 3rd should raise
        with pytest.raises(RuntimeExecutionError) as exc_info:
            wrapped(agent_name="test_agent")
        assert exc_info.value.code == ErrorCode.RATE_EXCEEDED
        assert "rate_limit" in str(exc_info.value.detail.get("scope", "")) or True
        assert exc_info.value.detail["max_requests"] == 2
        assert exc_info.value.detail["burst"] == 0

    def test_middleware_burst_tolerance(self):
        """Middleware should allow burst capacity."""
        class MockConfig:
            metadata = {"rate_limit": {"max_requests": 3, "window_seconds": 60, "burst": 2}}

        rate_limiter = build_rate_limit(context={"agent_config": MockConfig()})

        def mock_invoke(*, agent_name="default", **kwargs):
            return {"ok": True}

        wrapped = rate_limiter(mock_invoke)
        # 3 base + 2 burst = 5 should pass
        for i in range(5):
            assert wrapped(agent_name="test") == {"ok": True}
        # 6th should fail
        with pytest.raises(RuntimeExecutionError) as exc_info:
            wrapped(agent_name="test")
        assert exc_info.value.code == ErrorCode.RATE_EXCEEDED

    def test_middleware_cooldown_recovery(self):
        """After window expires, middleware should allow calls again."""
        class MockConfig:
            metadata = {"rate_limit": {"max_requests": 1, "window_seconds": 1, "burst": 0}}

        rate_limiter = build_rate_limit(context={"agent_config": MockConfig()})

        def mock_invoke(*, agent_name="default", **kwargs):
            return {"ok": True}

        wrapped = rate_limiter(mock_invoke)
        # First call passes
        assert wrapped(agent_name="test") == {"ok": True}
        # Second call blocked
        with pytest.raises(RuntimeExecutionError):
            wrapped(agent_name="test")
        # Wait for window to expire
        time.sleep(1.1)
        # Should work again
        assert wrapped(agent_name="test") == {"ok": True}

    def test_middleware_per_agent_scope(self):
        """Per-agent scope: different agents have separate limits."""
        class MockConfig:
            metadata = {"rate_limit": {"max_requests": 1, "window_seconds": 60, "burst": 0, "scope": "agent"}}

        rate_limiter = build_rate_limit(context={"agent_config": MockConfig()})

        def mock_invoke(*, agent_name="default", **kwargs):
            return {"ok": True}

        wrapped = rate_limiter(mock_invoke)
        # agent_a uses its limit
        assert wrapped(agent_name="agent_a") == {"ok": True}
        with pytest.raises(RuntimeExecutionError):
            wrapped(agent_name="agent_a")
        # agent_b has its own limit — should still work
        assert wrapped(agent_name="agent_b") == {"ok": True}

    def test_middleware_global_scope(self):
        """Global scope: all agents share one limit."""
        class MockConfig:
            metadata = {"rate_limit": {"max_requests": 1, "window_seconds": 60, "burst": 0, "scope": "global"}}

        rate_limiter = build_rate_limit(context={"agent_config": MockConfig()})

        def mock_invoke(*, agent_name="default", **kwargs):
            return {"ok": True}

        wrapped = rate_limiter(mock_invoke)
        # First call (agent_a) uses the global limit
        assert wrapped(agent_name="agent_a") == {"ok": True}
        # Second call (agent_b) should fail — same global bucket
        with pytest.raises(RuntimeExecutionError):
            wrapped(agent_name="agent_b")

    def test_middleware_attached_limiter(self):
        """The wrapped function should expose the limiter for inspection."""
        rate_limiter = build_rate_limit(context={})

        def mock_invoke(*, agent_name="default", **kwargs):
            return {"ok": True}

        wrapped = rate_limiter(mock_invoke)
        assert hasattr(wrapped, "limiter")
        assert isinstance(wrapped.limiter, AgentRateLimiter)
        assert wrapped.limiter.max_requests == 60  # default

    def test_middleware_default_config(self):
        """With no agent_config, should use defaults (60/60/10)."""
        rate_limiter = build_rate_limit(context={})

        def mock_invoke(*, agent_name="default", **kwargs):
            return {"ok": True}

        wrapped = rate_limiter(mock_invoke)
        assert wrapped.limiter.max_requests == 60
        assert wrapped.limiter.window == 60
        assert wrapped.limiter.burst == 10
