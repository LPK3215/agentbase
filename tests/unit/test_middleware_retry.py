"""Unit tests for retry middleware — _is_retryable, _compute_delay, build_retry.

Tests cover:
- Non-retryable error detection (auth, validation, 4xx)
- Retryable error detection (timeout, connection, 429, rate limit)
- Exponential backoff computation with and without jitter
- build_retry configuration from agent_config
- build_retry wrap_model_call fallback when langchain unavailable
- retry_middleware success / retry / exhaustion / non-retryable paths
"""
from __future__ import annotations

import sys
import time
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentbase.extensions.middleware.retry import (
    _compute_delay,
    _is_retryable,
    build_retry,
)


def _passthrough_decorator(func):
    """A stand-in for wrap_model_call that returns the function unchanged."""
    return func


# ---------------------------------------------------------------------------
# _is_retryable
# ---------------------------------------------------------------------------


class TestIsRetryable:
    @pytest.mark.parametrize(
        "msg",
        [
            "Authentication failed",
            "auth required",
            "invalid_api_key",
            "invalid api key",
            "api_key_invalid",
            "unauthorized access",
            "forbidden resource",
            "400 bad request",
            "validation error",
            "invalid_request format",
        ],
    )
    def test_non_retryable_hints(self, msg):
        assert _is_retryable(Exception(msg)) is False

    @pytest.mark.parametrize(
        "msg",
        [
            "429 too many requests",
            "rate limit exceeded",
            "Rate Limit",
        ],
    )
    def test_rate_limit_retryable(self, msg):
        assert _is_retryable(Exception(msg)) is True

    def test_timeout_retryable(self):
        assert _is_retryable(TimeoutError("timeout")) is True

    def test_connection_error_retryable(self):
        assert _is_retryable(ConnectionError("connection refused")) is True

    def test_oserror_retryable(self):
        assert _is_retryable(OSError("network unreachable")) is True

    def test_unknown_error_retryable(self):
        assert _is_retryable(RuntimeError("something went wrong")) is True


# ---------------------------------------------------------------------------
# _compute_delay
# ---------------------------------------------------------------------------


class TestComputeDelay:
    def test_no_jitter(self):
        delay = _compute_delay(1, base_delay=0.5, max_delay=30.0, jitter=False)
        assert delay == 0.5

    def test_no_jitter_exponential(self):
        delay = _compute_delay(3, base_delay=0.5, max_delay=30.0, jitter=False)
        assert delay == 2.0  # 0.5 * 2^2 = 2.0

    def test_capped_at_max_delay(self):
        delay = _compute_delay(10, base_delay=0.5, max_delay=5.0, jitter=False)
        assert delay == 5.0

    def test_with_jitter_within_range(self):
        for _ in range(50):
            delay = _compute_delay(1, base_delay=0.5, max_delay=30.0, jitter=True)
            assert 0.0 <= delay <= 1.0  # base + [0, base]


# ---------------------------------------------------------------------------
# build_retry — configuration and middleware behavior
# ---------------------------------------------------------------------------


class TestBuildRetry:
    def test_returns_middleware_when_langchain_available(self):
        """build_retry should return a non-empty result when langchain is available."""
        with patch("langchain.agents.middleware.wrap_model_call", _passthrough_decorator):
            mw = build_retry(None)
            assert callable(mw)

    def test_returns_empty_list_when_langchain_unavailable(self):
        """build_retry should return [] when wrap_model_call import fails."""
        import sys
        # Remove the module from sys.modules so the import re-occurs
        # and mock it to raise ImportError
        with patch.dict(sys.modules, {"langchain.agents.middleware": None}):
            result = build_retry(None)
            assert result == []

    def test_config_from_agent_config(self):
        """build_retry should read config from agent_config.metadata['retry']."""
        agent_config = MagicMock()
        agent_config.metadata.get.return_value = {
            "max_attempts": 5,
            "base_delay": 0.1,
            "max_delay": 10.0,
            "jitter": False,
        }
        context = {"agent_config": agent_config}
        with patch("langchain.agents.middleware.wrap_model_call", _passthrough_decorator):
            mw = build_retry(context)
            assert callable(mw)

    def test_config_defaults_when_agent_config_none(self):
        """build_retry should use defaults when agent_config is None."""
        with patch("langchain.agents.middleware.wrap_model_call", _passthrough_decorator):
            mw = build_retry(None)
            assert callable(mw)


class TestRetryMiddleware:
    """Test the inner retry_middleware function's behavior."""

    def _build_mw(self, max_attempts=3, base_delay=0.001, max_delay=1.0, jitter=False):
        """Build retry middleware with fast delays for testing."""
        agent_config = MagicMock()
        agent_config.metadata.get.return_value = {
            "max_attempts": max_attempts,
            "base_delay": base_delay,
            "max_delay": max_delay,
            "jitter": jitter,
        }
        with patch("langchain.agents.middleware.wrap_model_call", _passthrough_decorator):
            return build_retry({"agent_config": agent_config})

    def test_success_on_first_try(self):
        mw = self._build_mw()
        handler = MagicMock(return_value="ok")
        # The middleware is a decorated function — call it directly
        result = mw("request", handler)
        assert result == "ok"
        handler.assert_called_once()

    def test_success_on_retry(self):
        mw = self._build_mw(max_attempts=3)
        handler = MagicMock(side_effect=[
            RuntimeError("transient error"),
            "ok",
        ])
        result = mw("request", handler)
        assert result == "ok"
        assert handler.call_count == 2

    def test_exhausted_raises_last_error(self):
        mw = self._build_mw(max_attempts=2)
        handler = MagicMock(side_effect=RuntimeError("persistent error"))
        with pytest.raises(RuntimeError, match="persistent error"):
            mw("request", handler)
        assert handler.call_count == 2

    def test_non_retryable_raises_immediately(self):
        mw = self._build_mw(max_attempts=3)
        handler = MagicMock(side_effect=Exception("authentication failed"))
        with pytest.raises(Exception, match="authentication"):
            mw("request", handler)
        # Should not retry — only called once
        assert handler.call_count == 1

    def test_429_is_retryable(self):
        mw = self._build_mw(max_attempts=3)
        handler = MagicMock(side_effect=[
            Exception("429 too many requests"),
            "ok",
        ])
        result = mw("request", handler)
        assert result == "ok"
        assert handler.call_count == 2

    def test_rate_limit_is_retryable(self):
        mw = self._build_mw(max_attempts=3)
        handler = MagicMock(side_effect=[
            Exception("rate limit exceeded"),
            "ok",
        ])
        result = mw("request", handler)
        assert result == "ok"
        assert handler.call_count == 2

    def test_timeout_is_retryable(self):
        mw = self._build_mw(max_attempts=3)
        handler = MagicMock(side_effect=[
            TimeoutError("request timed out"),
            "ok",
        ])
        result = mw("request", handler)
        assert result == "ok"

    def test_connection_error_is_retryable(self):
        mw = self._build_mw(max_attempts=3)
        handler = MagicMock(side_effect=[
            ConnectionError("connection refused"),
            "ok",
        ])
        result = mw("request", handler)
        assert result == "ok"
