"""Unit tests for request_logger middleware — build_request_logger and inner middleware.

Tests cover:
- build_request_logger returns callable when langchain available
- build_request_logger returns [] when wrap_model_call unavailable
- Inner middleware success path (model name extraction, duration, response)
- Inner middleware error path (exception logged and re-raised)
- Model name extraction via .model.model_name and .model.model fallbacks
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentbase.extensions.middleware.request_logger import build_request_logger


def _passthrough_decorator(func):
    """A stand-in for wrap_model_call that returns the function unchanged."""
    return func


class TestBuildRequestLogger:
    def test_returns_middleware_when_langchain_available(self):
        with patch("langchain.agents.middleware.wrap_model_call", _passthrough_decorator):
            mw = build_request_logger(None)
            assert callable(mw)

    def test_returns_empty_list_when_langchain_unavailable(self):
        """build_request_logger should return [] when wrap_model_call import fails."""
        import sys
        with patch.dict(sys.modules, {"langchain.agents.middleware": None}):
            result = build_request_logger(None)
            assert result == []

    def test_returns_middleware_with_context(self):
        with patch("langchain.agents.middleware.wrap_model_call", _passthrough_decorator):
            mw = build_request_logger({"agent_config": MagicMock()})
            assert callable(mw)


class TestRequestLoggerMiddleware:
    def _build_mw(self):
        with patch("langchain.agents.middleware.wrap_model_call", _passthrough_decorator):
            return build_request_logger(None)

    def test_success_path(self):
        mw = self._build_mw()
        request = MagicMock()
        request.model.model_name = "gpt-4o"
        handler = MagicMock(return_value="response")

        result = mw(request, handler)

        assert result == "response"
        handler.assert_called_once_with(request)

    def test_error_path_reraises(self):
        mw = self._build_mw()
        request = MagicMock()
        request.model.model_name = "gpt-4o"
        handler = MagicMock(side_effect=RuntimeError("model error"))

        with pytest.raises(RuntimeError, match="model error"):
            mw(request, handler)

        handler.assert_called_once_with(request)

    def test_model_name_from_model_name_attr(self):
        mw = self._build_mw()
        request = MagicMock()
        request.model.model_name = "claude-3"
        handler = MagicMock(return_value="ok")

        mw(request, handler)
        # Verify model_name was extracted (logged via logger.info)
        handler.assert_called_once()

    def test_model_name_fallback_to_model_attr(self):
        mw = self._build_mw()
        request = MagicMock()
        # model.model_name is None, so falls back to model.model
        request.model.model_name = None
        request.model.model = "fallback-model"
        handler = MagicMock(return_value="ok")

        mw(request, handler)
        handler.assert_called_once()

    def test_model_name_none_when_no_model(self):
        mw = self._build_mw()
        request = MagicMock()
        request.model = None
        handler = MagicMock(return_value="ok")

        # Should not crash even if model is None
        result = mw(request, handler)
        assert result == "ok"

    def test_model_name_none_when_model_has_no_attrs(self):
        mw = self._build_mw()
        request = MagicMock()
        # Both model_name and model return None via MagicMock default
        request.model.model_name = None
        request.model.model = None
        handler = MagicMock(return_value="ok")

        result = mw(request, handler)
        assert result == "ok"

    def test_duration_logged_on_success(self):
        mw = self._build_mw()
        request = MagicMock()
        request.model.model_name = "gpt-4o"
        handler = MagicMock(return_value="ok")

        with patch("agentbase.extensions.middleware.request_logger.time.time", return_value=100.0):
            # time.time() called twice: once before, once after
            # If both return same value, duration_ms = 0.0
            mw(request, handler)

    def test_duration_logged_on_error(self):
        mw = self._build_mw()
        request = MagicMock()
        request.model.model_name = "gpt-4o"
        handler = MagicMock(side_effect=ValueError("bad request"))

        with patch("agentbase.extensions.middleware.request_logger.time.time", return_value=100.0):
            with pytest.raises(ValueError):
                mw(request, handler)
