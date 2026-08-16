"""Unit tests for timeout middleware — build_timeout and inner middleware.

Tests cover:
- build_timeout returns callable when langchain available
- build_timeout returns [] when wrap_model_call unavailable
- build_timeout config from agent_config
- Inner middleware: success within timeout, timeout exceeded
"""
from __future__ import annotations

import concurrent.futures
import sys
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentbase.extensions.middleware.timeout import build_timeout


def _passthrough_decorator(func):
    """A stand-in for wrap_model_call that returns the function unchanged."""
    return func


class TestBuildTimeout:
    def test_returns_middleware_when_langchain_available(self):
        with patch("langchain.agents.middleware.wrap_model_call", _passthrough_decorator):
            mw = build_timeout(None)
            assert callable(mw)

    def test_returns_empty_list_when_langchain_unavailable(self):
        with patch.dict(sys.modules, {"langchain.agents.middleware": None}):
            result = build_timeout(None)
            assert result == []

    def test_config_from_agent_config(self):
        agent_config = MagicMock()
        agent_config.metadata.get.return_value = {"seconds": 10}
        with patch("langchain.agents.middleware.wrap_model_call", _passthrough_decorator):
            mw = build_timeout({"agent_config": agent_config})
            assert callable(mw)

    def test_config_defaults(self):
        with patch("langchain.agents.middleware.wrap_model_call", _passthrough_decorator):
            mw = build_timeout(None)
            assert callable(mw)


class TestTimeoutMiddleware:
    def _build_mw(self, seconds=5):
        agent_config = MagicMock()
        agent_config.metadata.get.return_value = {"seconds": seconds}
        with patch("langchain.agents.middleware.wrap_model_call", _passthrough_decorator):
            return build_timeout({"agent_config": agent_config})

    def test_success_within_timeout(self):
        mw = self._build_mw(seconds=5)
        handler = MagicMock(return_value="ok")
        result = mw("request", handler)
        assert result == "ok"

    def test_timeout_exceeded_raises(self):
        mw = self._build_mw(seconds=0)

        def slow_handler(request):
            time.sleep(2)
            return "should not reach"

        with pytest.raises(TimeoutError, match="timed out"):
            mw("request", slow_handler)
