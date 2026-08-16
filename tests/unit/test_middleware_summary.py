"""Unit tests for summary middleware — _estimate_tokens, _extract_messages,
_messages_to_text, _l1_summarize, _l2_compact, build_summary, and inner middleware.

Tests cover:
- Token estimation with CJK and non-CJK text
- Message extraction from dict and object requests
- Message-to-text conversion with dict and object messages
- L1 summarization with model_fn and fallback
- L2 compaction with model_fn and fallback
- build_summary configuration and wrap_model_call fallback
- Inner middleware: below threshold, keep_recent edge, compaction, L2 trigger,
  dict request path, object request path
"""
from __future__ import annotations

import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentbase.extensions.middleware.summary import (
    DEFAULT_SUMMARY_PROMPT,
    _estimate_tokens,
    _extract_messages,
    _l1_summarize,
    _l2_compact,
    _messages_to_text,
    build_summary,
)


def _passthrough_decorator(func):
    """A stand-in for wrap_model_call that returns the function unchanged."""
    return func


# ---------------------------------------------------------------------------
# _estimate_tokens
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    def test_empty_string(self):
        assert _estimate_tokens("") == 0

    def test_whitespace_only(self):
        assert _estimate_tokens("   \n\t  ") == 0

    def test_pure_cjk(self):
        # 4 CJK chars → 4 * 1.5 = 6
        assert _estimate_tokens("你好世界") == 6

    def test_pure_ascii(self):
        # 8 chars, 0 CJK → 8 / 4 = 2
        assert _estimate_tokens("abcdefgh") == 2

    def test_mixed(self):
        # 2 CJK + 4 ascii = 2*1.5 + 4/4 = 3+1 = 4
        assert _estimate_tokens("你好abcd") == 4

    def test_minimum_one_for_short_text(self):
        # 1 char → 0 CJK + 1/4 = 0 → max(0, 1) = 1
        assert _estimate_tokens("a") == 1


# ---------------------------------------------------------------------------
# _extract_messages
# ---------------------------------------------------------------------------


class TestExtractMessages:
    def test_dict_request_with_messages(self):
        request = {"messages": [{"role": "user", "content": "hi"}]}
        result = _extract_messages(request)
        assert len(result) == 1

    def test_dict_request_without_messages(self):
        request = {"model": "gpt-4o"}
        assert _extract_messages(request) == []

    def test_dict_request_messages_not_list(self):
        request = {"messages": "not a list"}
        assert _extract_messages(request) == []

    def test_object_request_with_messages(self):
        request = MagicMock()
        request.messages = [{"role": "user", "content": "hi"}]
        result = _extract_messages(request)
        assert len(result) == 1

    def test_object_request_without_messages(self):
        request = MagicMock()
        request.messages = None
        assert _extract_messages(request) == []

    def test_non_dict_non_object_request(self):
        assert _extract_messages(42) == []
        assert _extract_messages("string") == []


# ---------------------------------------------------------------------------
# _messages_to_text
# ---------------------------------------------------------------------------


class TestMessagesToText:
    def test_dict_messages(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        result = _messages_to_text(messages)
        assert "[user]: Hello" in result
        assert "[assistant]: Hi there" in result

    def test_object_messages(self):
        msg1 = MagicMock()
        msg1.role = "system"
        msg1.content = "System prompt"
        msg2 = MagicMock()
        msg2.role = "user"
        msg2.content = "User query"
        result = _messages_to_text([msg1, msg2])
        assert "[system]: System prompt" in result
        assert "[user]: User query" in result

    def test_missing_role_defaults_to_unknown(self):
        messages = [{"content": "No role"}]
        result = _messages_to_text(messages)
        assert "[unknown]" in result

    def test_missing_content_defaults_to_empty(self):
        messages = [{"role": "user"}]
        result = _messages_to_text(messages)
        assert "[user]:" in result

    def test_empty_list(self):
        assert _messages_to_text([]) == ""


# ---------------------------------------------------------------------------
# _l1_summarize
# ---------------------------------------------------------------------------


class TestL1Summarize:
    def test_with_model_fn(self):
        model_fn = MagicMock(return_value="Summary text")
        result = _l1_summarize("conversation history", model_fn, DEFAULT_SUMMARY_PROMPT)
        assert result == "Summary text"
        model_fn.assert_called_once()

    def test_model_fn_fails_falls_back(self):
        model_fn = MagicMock(side_effect=RuntimeError("model error"))
        text = "[user]: Hello\n[assistant]: Hi"
        result = _l1_summarize(text, model_fn, DEFAULT_SUMMARY_PROMPT)
        # Should fall back to truncation
        assert "Hello" in result or "Hi" in result

    def test_no_model_fn_falls_back(self):
        text = "[user]: Hello\n[assistant]: Hi there"
        result = _l1_summarize(text, None, DEFAULT_SUMMARY_PROMPT)
        assert "Hello" in result

    def test_truncation_long_lines(self):
        # lines[0] is not truncated, but lines[1:] with [role]: prefix are
        text = "[user]: short\n[assistant]: " + "x" * 300
        result = _l1_summarize(text, None, DEFAULT_SUMMARY_PROMPT)
        lines = result.split("\n")
        assert len(lines) == 2
        # Second line should be truncated to 200 + "..." = 203
        assert len(lines[1]) <= 203
        assert "..." in result

    def test_truncation_keeps_at_most_20_lines(self):
        lines = [f"[user]: msg {i}" for i in range(25)]
        text = "\n".join(lines)
        result = _l1_summarize(text, None, DEFAULT_SUMMARY_PROMPT)
        assert result.count("\n") <= 19


# ---------------------------------------------------------------------------
# _l2_compact
# ---------------------------------------------------------------------------


class TestL2Compact:
    def test_with_model_fn(self):
        model_fn = MagicMock(return_value="Compressed")
        result = _l2_compact("long summary", model_fn, 1000)
        assert result == "Compressed"

    def test_model_fn_fails_falls_back(self):
        model_fn = MagicMock(side_effect=RuntimeError("error"))
        result = _l2_compact("short text", model_fn, 1000)
        assert result == "short text"

    def test_no_model_fn_short_text(self):
        result = _l2_compact("short", None, 1000)
        assert result == "short"

    def test_no_model_fn_truncates_long_text(self):
        text = "x" * 5000
        result = _l2_compact(text, None, 100)
        # max_chars = 100*4=400, result = 400 + "\n...(truncated)" = ~415
        assert len(result) <= 420
        assert "truncated" in result

    def test_no_model_fn_exact_fit(self):
        text = "x" * 100
        result = _l2_compact(text, None, 25)
        assert result == text  # 25*4=100, fits exactly


# ---------------------------------------------------------------------------
# build_summary
# ---------------------------------------------------------------------------


class TestBuildSummary:
    def test_returns_empty_list_when_langchain_unavailable(self):
        import sys
        with patch.dict(sys.modules, {"langchain.agents.middleware": None}):
            result = build_summary(None)
            assert result == []

    def test_returns_middleware_with_config(self):
        agent_config = MagicMock()
        agent_config.metadata.get.return_value = {
            "threshold": 5,
            "keep_recent": 2,
            "max_tokens_estimate": 1000,
        }
        with patch("langchain.agents.middleware.wrap_model_call", _passthrough_decorator):
            mw = build_summary({"agent_config": agent_config})
            assert callable(mw)

    def test_returns_middleware_defaults(self):
        with patch("langchain.agents.middleware.wrap_model_call", _passthrough_decorator):
            mw = build_summary(None)
            assert callable(mw)


class TestSummaryMiddleware:
    def _build_mw(self, threshold=3, keep_recent=1, max_tokens=8000, model_fn=None):
        agent_config = MagicMock()
        agent_config.metadata.get.return_value = {
            "threshold": threshold,
            "keep_recent": keep_recent,
            "max_tokens_estimate": max_tokens,
        }
        ctx = {"agent_config": agent_config}
        if model_fn is not None:
            ctx["summary_model"] = model_fn
        with patch("langchain.agents.middleware.wrap_model_call", _passthrough_decorator):
            return build_summary(ctx)

    def test_below_threshold_passes_through(self):
        mw = self._build_mw(threshold=10)
        request = {"messages": [{"role": "user", "content": "hi"}]}
        handler = MagicMock(return_value="ok")
        result = mw(request, handler)
        assert result == "ok"

    def test_keep_recent_ge_len_messages(self):
        mw = self._build_mw(threshold=2, keep_recent=5)
        request = {"messages": [
            {"role": "user", "content": f"msg {i}"} for i in range(3)
        ]}
        handler = MagicMock(return_value="ok")
        result = mw(request, handler)
        assert result == "ok"

    def test_compaction_dict_request(self):
        mw = self._build_mw(threshold=3, keep_recent=1)
        request = {"messages": [
            {"role": "user", "content": f"message {i}"} for i in range(5)
        ]}
        handler = MagicMock(return_value="ok")
        result = mw(request, handler)
        assert result == "ok"
        # Verify handler was called with modified request
        called_req = handler.call_args[0][0]
        assert isinstance(called_req, dict)
        # Should have summary + recent = 2 messages
        assert len(called_req["messages"]) == 2
        assert "[Conversation Summary]" in called_req["messages"][0]["content"]

    def test_compaction_object_request(self):
        mw = self._build_mw(threshold=3, keep_recent=1)
        request = MagicMock()
        request.messages = [
            {"role": "user", "content": f"message {i}"} for i in range(5)
        ]
        handler = MagicMock(return_value="ok")
        result = mw(request, handler)
        assert result == "ok"

    def test_l2_compaction_triggered(self):
        # max_tokens very low to trigger L2
        mw = self._build_mw(threshold=3, keep_recent=1, max_tokens=5)
        request = {"messages": [
            {"role": "user", "content": "x" * 100} for i in range(5)
        ]}
        handler = MagicMock(return_value="ok")
        result = mw(request, handler)
        assert result == "ok"

    def test_compaction_with_model_fn(self):
        model_fn = MagicMock(return_value="Model summary")
        mw = self._build_mw(threshold=3, keep_recent=1, model_fn=model_fn)
        request = {"messages": [
            {"role": "user", "content": f"message {i}"} for i in range(5)
        ]}
        handler = MagicMock(return_value="ok")
        result = mw(request, handler)
        assert result == "ok"
        model_fn.assert_called()

    def test_compaction_object_request_setattr_fails(self):
        mw = self._build_mw(threshold=3, keep_recent=1)
        request = MagicMock()
        request.messages = [
            {"role": "user", "content": f"message {i}"} for i in range(5)
        ]
        # Make setattr fail
        type(request).messages = property(lambda s: request._messages, lambda s, v: (_ for _ in ()).throw(AttributeError("read-only")))
        request._messages = request.messages
        handler = MagicMock(return_value="ok")
        result = mw(request, handler)
        assert result == "ok"
