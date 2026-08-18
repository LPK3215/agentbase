"""Tests for redact_output middleware — covers PII masking, passthrough, edge cases."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentbase.core.redaction import RedactionManager
from agentbase.extensions.middleware.redact_output import (
    _redact_message_content,
    build_redact_output,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeAIMessage:
    """Minimal stand-in for LangChain AIMessage — mutable .content attribute."""

    def __init__(self, content: Any) -> None:
        self.content = content


class _FrozenAIMessage:
    """AIMessage whose .content raises on set (simulates immutable type)."""

    def __init__(self, content: Any) -> None:
        self._content = content

    @property
    def content(self) -> Any:
        return self._content

    @content.setter
    def content(self, value: Any) -> None:
        raise AttributeError("content is read-only")


class _MockAppConfig:
    """Minimal AppConfig with a redaction section."""

    def __init__(self, *, enabled: bool = False, provider: str = "regex"):
        from agentbase.config.schema import RedactionConfig

        self.redaction = RedactionConfig(enabled=enabled, provider=provider)


class _MockAgentConfig:
    """Minimal AgentConfig with metadata dict."""

    def __init__(self, metadata: dict[str, Any] | None = None):
        self.metadata = metadata or {}


# ---------------------------------------------------------------------------
# _redact_message_content unit tests
# ---------------------------------------------------------------------------


class TestRedactMessageContent:
    def test_redact_string_content(self):
        """String content is redacted."""
        manager = RedactionManager(provider="regex", enabled=True)
        result = _redact_message_content(
            "Contact alice@example.com for details", manager
        )
        assert "alice@example.com" not in result
        assert "***@***.***" in result

    def test_redact_list_content_text_parts(self):
        """List content with text parts is redacted."""
        manager = RedactionManager(provider="regex", enabled=True)
        content = [
            {"type": "text", "text": "Email: bob@test.com"},
            {"type": "tool_use", "text": "not redacted"},
        ]
        result = _redact_message_content(content, manager)
        assert isinstance(result, list)
        assert "bob@test.com" not in result[0]["text"]
        assert "***@***.***" in result[0]["text"]
        # Non-text part unchanged
        assert result[1]["text"] == "not redacted"

    def test_redact_non_text_list_item_passthrough(self):
        """Non-text list items (dicts without type=text) are passed through."""
        manager = RedactionManager(provider="regex", enabled=True)
        content = [{"type": "image_url", "image_url": "http://example.com/img.png"}]
        result = _redact_message_content(content, manager)
        assert result == content

    def test_redact_non_string_non_list_content(self):
        """Non-string, non-list content is returned unchanged."""
        manager = RedactionManager(provider="regex", enabled=True)
        assert _redact_message_content(42, manager) == 42
        assert _redact_message_content(None, manager) is None
        assert _redact_message_content(True, manager) is True

    def test_redact_empty_string(self):
        """Empty string returns empty string."""
        manager = RedactionManager(provider="regex", enabled=True)
        assert _redact_message_content("", manager) == ""

    def test_redact_clean_text_unchanged(self):
        """Text without PII is returned unchanged."""
        manager = RedactionManager(provider="regex", enabled=True)
        text = "The weather is nice today."
        assert _redact_message_content(text, manager) == text

    def test_redact_multiple_pii_types(self):
        """Multiple PII types in one string are all redacted."""
        manager = RedactionManager(provider="regex", enabled=True)
        text = "Email: alice@example.com, Phone: 13800138000, Key: sk-" + "a" * 30
        result = _redact_message_content(text, manager)
        assert "alice@example.com" not in result
        assert "13800138000" not in result
        assert "sk-" + "a" * 30 not in result


# ---------------------------------------------------------------------------
# build_redact_output — normal path
# ---------------------------------------------------------------------------


class TestBuildRedactOutputNormal:
    def test_middleware_redacts_response_content(self):
        """Middleware should redact PII from model response content."""
        app_config = _MockAppConfig(enabled=True, provider="regex")

        with patch("langchain.agents.middleware.wrap_model_call") as mock_wrap:
            # Make wrap_model_call pass-through: it wraps the function
            # but we want the inner function to actually execute.
            mock_wrap.side_effect = lambda fn: fn

            builder = build_redact_output(context={"app_config": app_config})
            assert not isinstance(builder, list), "wrap_model_call should be available"

            # Create a mock request and handler that returns an AIMessage
            mock_request = MagicMock()
            sensitive_response = _FakeAIMessage(
                content="My API key is sk-" + "a" * 30 + " and email is test@example.com"
            )

            def handler(req):
                return sensitive_response

            result = builder(mock_request, handler)
            assert "sk-" + "a" * 30 not in result.content
            assert "test@example.com" not in result.content
            assert "***REDACTED_KEY***" in result.content
            assert "***@***.***" in result.content

    def test_middleware_preserves_clean_text(self):
        """Middleware should not alter clean (PII-free) text."""
        app_config = _MockAppConfig(enabled=True, provider="regex")

        with patch("langchain.agents.middleware.wrap_model_call") as mock_wrap:
            mock_wrap.side_effect = lambda fn: fn

            builder = build_redact_output(context={"app_config": app_config})
            assert not isinstance(builder, list)

            mock_request = MagicMock()
            clean_response = _FakeAIMessage(content="The sky is blue.")

            def handler(req):
                return clean_response

            result = builder(mock_request, handler)
            assert result.content == "The sky is blue."

    def test_middleware_redacts_list_content(self):
        """Middleware should redact list-type content (multi-part responses)."""
        app_config = _MockAppConfig(enabled=True, provider="regex")

        with patch("langchain.agents.middleware.wrap_model_call") as mock_wrap:
            mock_wrap.side_effect = lambda fn: fn

            builder = build_redact_output(context={"app_config": app_config})
            assert not isinstance(builder, list)

            mock_request = MagicMock()
            response = _FakeAIMessage(
                content=[
                    {"type": "text", "text": "Call me at 13800138000"},
                    {"type": "text", "text": "No PII here"},
                ]
            )

            def handler(req):
                return response

            result = builder(mock_request, handler)
            assert "13800138000" not in result.content[0]["text"]
            assert "***PHONE***" in result.content[0]["text"]
            assert result.content[1]["text"] == "No PII here"

    def test_middleware_attaches_redaction_manager(self):
        """The middleware function should expose the redaction_manager."""
        app_config = _MockAppConfig(enabled=True, provider="regex")

        with patch("langchain.agents.middleware.wrap_model_call") as mock_wrap:
            mock_wrap.side_effect = lambda fn: fn

            builder = build_redact_output(context={"app_config": app_config})
            assert not isinstance(builder, list)

            assert hasattr(builder, "redaction_manager")
            assert isinstance(builder.redaction_manager, RedactionManager)
            assert builder.redaction_manager.enabled is True

    def test_per_agent_override_enables_redaction(self):
        """Per-agent metadata.redact_output.enabled=true overrides global config."""
        app_config = _MockAppConfig(enabled=False)  # Global disabled
        agent_config = _MockAgentConfig(
            metadata={"redact_output": {"enabled": True, "provider": "regex"}}
        )

        with patch("langchain.agents.middleware.wrap_model_call") as mock_wrap:
            mock_wrap.side_effect = lambda fn: fn

            builder = build_redact_output(
                context={"app_config": app_config, "agent_config": agent_config}
            )
            assert not isinstance(builder, list)

            assert builder.redaction_manager.enabled is True


# ---------------------------------------------------------------------------
# build_redact_output — boundary path
# ---------------------------------------------------------------------------


class TestBuildRedactOutputBoundary:
    def test_disabled_passthrough(self):
        """When redaction is disabled, response is returned unchanged."""
        app_config = _MockAppConfig(enabled=False)

        with patch("langchain.agents.middleware.wrap_model_call") as mock_wrap:
            mock_wrap.side_effect = lambda fn: fn

            builder = build_redact_output(context={"app_config": app_config})
            assert not isinstance(builder, list)

            mock_request = MagicMock()
            original_content = "My key is sk-" + "a" * 30

            def handler(req):
                return _FakeAIMessage(content=original_content)

            result = builder(mock_request, handler)
            # Content unchanged because redaction is disabled
            assert result.content == original_content

    def test_no_app_config_uses_defaults(self):
        """With no app_config, defaults to disabled (passthrough)."""
        with patch("langchain.agents.middleware.wrap_model_call") as mock_wrap:
            mock_wrap.side_effect = lambda fn: fn

            builder = build_redact_output(context={})
            assert not isinstance(builder, list)

            assert builder.redaction_manager.enabled is False

    def test_none_context(self):
        """With None context, should not crash."""
        with patch("langchain.agents.middleware.wrap_model_call") as mock_wrap:
            mock_wrap.side_effect = lambda fn: fn

            builder = build_redact_output(context=None)
            assert not isinstance(builder, list)

            assert builder.redaction_manager.enabled is False

    def test_empty_response_content(self):
        """Empty string content is handled correctly."""
        app_config = _MockAppConfig(enabled=True)

        with patch("langchain.agents.middleware.wrap_model_call") as mock_wrap:
            mock_wrap.side_effect = lambda fn: fn

            builder = build_redact_output(context={"app_config": app_config})
            assert not isinstance(builder, list)

            mock_request = MagicMock()

            def handler(req):
                return _FakeAIMessage(content="")

            result = builder(mock_request, handler)
            assert result.content == ""

    def test_none_response_content(self):
        """None content on response is handled gracefully."""
        app_config = _MockAppConfig(enabled=True)

        with patch("langchain.agents.middleware.wrap_model_call") as mock_wrap:
            mock_wrap.side_effect = lambda fn: fn

            builder = build_redact_output(context={"app_config": app_config})
            assert not isinstance(builder, list)

            mock_request = MagicMock()

            class _NoContent:
                pass

            def handler(req):
                return _NoContent()

            # Should not crash even though .content doesn't exist
            result = builder(mock_request, handler)
            assert result is not None

    def test_immutable_response_content(self):
        """If response.content is read-only, middleware logs but doesn't crash."""
        app_config = _MockAppConfig(enabled=True)

        with patch("langchain.agents.middleware.wrap_model_call") as mock_wrap:
            mock_wrap.side_effect = lambda fn: fn

            builder = build_redact_output(context={"app_config": app_config})
            assert not isinstance(builder, list)

            mock_request = MagicMock()
            frozen = _FrozenAIMessage(content="sk-" + "a" * 30)

            def handler(req):
                return frozen

            # Should not crash even though .content is immutable
            result = builder(mock_request, handler)
            # Content is unchanged (could not mutate)
            assert "sk-" + "a" * 30 in result.content


# ---------------------------------------------------------------------------
# build_redact_output — error / fallback path
# ---------------------------------------------------------------------------


class TestBuildRedactOutputError:
    def test_wrap_model_call_unavailable_returns_empty_list(self):
        """When wrap_model_call is not available, returns empty list."""
        import sys

        # Temporarily remove the langchain.agents.middleware module to force
        # the import inside build_redact_output to fail.
        original = sys.modules.get("langchain.agents.middleware")
        sys.modules["langchain.agents.middleware"] = None  # type: ignore[assignment]
        try:
            builder = build_redact_output(
                context={"app_config": _MockAppConfig(enabled=True)}
            )
        finally:
            if original is not None:
                sys.modules["langchain.agents.middleware"] = original
            else:
                sys.modules.pop("langchain.agents.middleware", None)
        assert builder == []

    def test_handler_exception_propagates(self):
        """If handler raises, the exception should propagate (not swallowed)."""
        app_config = _MockAppConfig(enabled=True)

        with patch("langchain.agents.middleware.wrap_model_call") as mock_wrap:
            mock_wrap.side_effect = lambda fn: fn

            builder = build_redact_output(context={"app_config": app_config})
            assert not isinstance(builder, list)

            mock_request = MagicMock()

            def handler(req):
                raise RuntimeError("model API down")

            with pytest.raises(RuntimeError, match="model API down"):
                builder(mock_request, handler)


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestRedactOutputRegistry:
    def test_registered_in_middleware_registry(self, bootstrapped):
        """Middleware is registered after bootstrap."""
        from agentbase.registry.middleware import middleware_registry

        assert middleware_registry.has("redact_output")

    def test_build_via_factory(self, bootstrapped):
        """Middleware can be built through the factory."""
        from agentbase.factories.middleware_factory import build_middleware

        app_config = _MockAppConfig(enabled=False)
        items = build_middleware(["redact_output"], context={"app_config": app_config})
        # May return empty list if wrap_model_call unavailable, or one item
        assert isinstance(items, list)

    def test_meta_default_disabled(self, bootstrapped):
        """Meta has default_enabled=False."""
        from agentbase.registry.middleware import middleware_registry

        meta = middleware_registry.get_meta("redact_output")
        assert meta is not None
        assert meta.default_enabled is False
        assert meta.kind == "middleware"
        assert "security" in meta.tags

    def test_meta_requires_app_config(self, bootstrapped):
        """Meta declares app_config as required context."""
        from agentbase.registry.middleware import middleware_registry

        meta = middleware_registry.get_meta("redact_output")
        assert meta is not None
        assert "app_config" in meta.requires_context


# ---------------------------------------------------------------------------
# Supplementary coverage — dataclass-like .text items + per-agent options
# ---------------------------------------------------------------------------


class TestRedactMessageContentExtra:
    def test_redact_dataclass_like_text_item(self):
        """List items with a .text attribute (not dict) are handled via copy."""

        class _TextPart:
            def __init__(self, text: str) -> None:
                self.text = text

        manager = RedactionManager(provider="regex", enabled=True)
        content = [_TextPart("Email: alice@example.com")]
        result = _redact_message_content(content, manager)
        assert isinstance(result, list)
        assert "alice@example.com" not in result[0].text
        assert "***@***.***" in result[0].text

    def test_redact_dataclass_like_text_item_copy_failure(self):
        """If copy fails, the original item is passed through unchanged."""

        class _Uncopyable:
            text: str = "Email: alice@example.com"

            def __copy__(self):
                raise TypeError("cannot copy")

        manager = RedactionManager(provider="regex", enabled=True)
        content = [_Uncopyable()]
        result = _redact_message_content(content, manager)
        assert isinstance(result, list)
        # Original item passed through (copy failed)
        assert result[0].text == "Email: alice@example.com"


class TestPerAgentOptionsOverride:
    def test_per_agent_options_override(self):
        """Per-agent metadata.redact_output.options merges into provider_kwargs."""
        app_config = _MockAppConfig(enabled=True, provider="regex")
        agent_config = _MockAgentConfig(
            metadata={
                "redact_output": {
                    "enabled": True,
                    "provider": "regex",
                    "options": {"custom_rules": True},
                }
            }
        )

        with patch("langchain.agents.middleware.wrap_model_call") as mock_wrap:
            mock_wrap.side_effect = lambda fn: fn

            builder = build_redact_output(
                context={"app_config": app_config, "agent_config": agent_config}
            )
            assert not isinstance(builder, list)
            assert builder.redaction_manager.enabled is True
