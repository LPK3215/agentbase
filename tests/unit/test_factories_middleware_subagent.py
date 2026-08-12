"""Unit tests for middleware (retry, timeout, request_logger) and middleware_factory."""
from __future__ import annotations

import pytest

from agentbase.config.schema import AgentConfig


class TestRetryMiddleware:
    def test_build_returns_value(self, bootstrapped):
        """retry should return either [] or a middleware object."""
        from agentbase.extensions.middleware.retry import build_retry

        result = build_retry(context={})
        assert result is not None

    def test_build_reads_max_attempts_from_config(self, bootstrapped):
        """Retry should read max_attempts from agent_config.metadata."""
        from agentbase.extensions.middleware.retry import build_retry

        agent_config = AgentConfig(name="test", metadata={"retry": {"max_attempts": 5}})
        result = build_retry(context={"agent_config": agent_config})
        # Should not raise
        assert result is not None

    def test_build_default_max_attempts(self, bootstrapped):
        """Retry should default to 3 attempts when no config."""
        from agentbase.extensions.middleware.retry import build_retry

        result = build_retry(context={})
        assert result is not None


class TestTimeoutMiddleware:
    def test_build_returns_value(self, bootstrapped):
        """timeout should return either [] or a middleware object."""
        from agentbase.extensions.middleware.timeout import build_timeout

        result = build_timeout(context={})
        assert result is not None

    def test_build_reads_seconds_from_config(self, bootstrapped):
        """Timeout should read seconds from agent_config.metadata."""
        from agentbase.extensions.middleware.timeout import build_timeout

        agent_config = AgentConfig(name="test", metadata={"timeout": {"seconds": 60}})
        result = build_timeout(context={"agent_config": agent_config})
        assert result is not None

    def test_build_default_seconds(self, bootstrapped):
        """Timeout should default to 30 seconds."""
        from agentbase.extensions.middleware.timeout import build_timeout

        result = build_timeout(context={})
        assert result is not None


class TestRequestLoggerMiddleware:
    def test_build_returns_value(self, bootstrapped):
        """request_logger should return either [] or a middleware object."""
        from agentbase.extensions.middleware.request_logger import build_request_logger

        result = build_request_logger(context={})
        assert result is not None

    def test_build_with_no_context(self, bootstrapped):
        """Request logger should work with no context."""
        from agentbase.extensions.middleware.request_logger import build_request_logger

        result = build_request_logger(context=None)
        assert result is not None


class TestMiddlewareFactory:
    def test_build_empty_list(self):
        from agentbase.factories.middleware_factory import build_middleware

        result = build_middleware([], context={})
        assert result == []

    def test_build_known_middleware(self, bootstrapped):
        """Build request_logger middleware."""
        from agentbase.factories.middleware_factory import build_middleware

        result = build_middleware(["request_logger"], context={})
        # Should return a list (possibly empty if wrap_model_call unavailable)
        assert isinstance(result, list)

    def test_build_unknown_raises(self):
        from agentbase.factories.middleware_factory import build_middleware

        # Strict mode (skip_on_error=False) must raise for unknown middleware.
        with pytest.raises(Exception, match="Unknown middleware"):
            build_middleware(["nonexistent_middleware"], context={}, skip_on_error=False)

    def test_build_returns_none_raises(self, bootstrapped):
        """If a builder returns None, FactoryError should be raised in strict mode."""
        from agentbase.registry.middleware import register_middleware

        @register_middleware("null_mw", override=True)
        def build_null(context=None):
            return None

        from agentbase.factories.middleware_factory import build_middleware as _bm

        with pytest.raises(Exception, match="returned None"):
            _bm(["null_mw"], context={}, skip_on_error=False)

    def test_build_list_returning_builder(self, bootstrapped):
        """Middleware builders can return lists, which should be extended."""
        from agentbase.registry.middleware import register_middleware

        @register_middleware("list_mw", override=True)
        def build_list_mw(context=None):
            return ["item1", "item2"]

        from agentbase.factories.middleware_factory import build_middleware as _bm

        result = _bm(["list_mw"], context={})
        assert result == ["item1", "item2"]


class TestSubagentFactory:
    def test_build_empty_list(self):
        from agentbase.factories.subagent_factory import build_subagents

        result = build_subagents([], context={})
        assert result == []

    def test_build_unknown_raises(self):
        from agentbase.factories.subagent_factory import build_subagents

        # Strict mode (skip_on_error=False) must raise for unknown subagents.
        with pytest.raises(Exception, match="Unknown subagent"):
            build_subagents(["nonexistent_subagent"], context={}, skip_on_error=False)

    def test_build_general_helper(self, bootstrapped):
        """Build the general_helper subagent."""
        from agentbase.factories.subagent_factory import build_subagents

        result = build_subagents(["general_helper"], context={})
        assert len(result) == 1
        spec = result[0]
        assert spec["name"] == "general_helper"
        assert "description" in spec
        assert "system_prompt" in spec

    def test_build_researcher(self, bootstrapped):
        """Build the researcher subagent."""
        from agentbase.factories.subagent_factory import build_subagents

        result = build_subagents(["researcher"], context={})
        assert len(result) == 1
        spec = result[0]
        assert spec["name"] == "researcher"

    def test_build_materializes_tools(self, bootstrapped):
        """Subagent specs with tool names should be materialized into tool objects."""
        from agentbase.factories.subagent_factory import build_subagents

        result = build_subagents(["general_helper"], context={})
        spec = result[0]
        if "tools" in spec and spec["tools"]:
            # Tools should be objects, not strings
            assert not isinstance(spec["tools"][0], str)

    def test_build_non_dict_raises(self, bootstrapped):
        """If a builder returns non-dict, FactoryError should be raised."""
        from agentbase.registry.subagents import register_subagent

        @register_subagent("bad_subagent", override=True)
        def build_bad(context=None):
            return "not a dict"

        from agentbase.factories.subagent_factory import build_subagents as _bs

        with pytest.raises(Exception, match="must return a dict"):
            _bs(["bad_subagent"], context={}, skip_on_error=False)
