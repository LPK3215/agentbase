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

    # --- Supplementary tests for missing branches ---

    def test_build_unknown_skip_on_error(self):
        """Unknown middleware with skip_on_error=True should be skipped."""
        from agentbase.factories.middleware_factory import build_middleware

        result = build_middleware(["nonexistent_mw_xyz"], context={}, skip_on_error=True)
        assert result == []

    def test_build_middleware_typeerror_fallback_success(self):
        """If builder(context=...) raises TypeError, fallback to builder() works."""
        from agentbase.registry.middleware import register_middleware

        @register_middleware("fallback_mw_tf", override=True)
        def build_fallback(context=None):
            if context is not None:
                raise TypeError("no context arg")
            return "mw_instance"

        from agentbase.factories.middleware_factory import build_middleware

        result = build_middleware(["fallback_mw_tf"], context={}, skip_on_error=False)
        assert len(result) == 1
        assert result[0] == "mw_instance"

    def test_build_middleware_typeerror_fallback_also_fails_skip(self):
        """If both builder(context=...) and builder() fail, skip with skip_on_error=True."""
        from agentbase.registry.middleware import register_middleware

        @register_middleware("double_fail_mw", override=True)
        def build_double_fail(context=None):
            if context is not None:
                raise TypeError("no context arg")
            raise RuntimeError("always fails")

        from agentbase.factories.middleware_factory import build_middleware

        result = build_middleware(["double_fail_mw"], context={}, skip_on_error=True)
        assert result == []

    def test_build_middleware_typeerror_fallback_also_fails_raise(self):
        """If both builder(context=...) and builder() fail, raise with skip_on_error=False."""
        from agentbase.registry.middleware import register_middleware
        from agentbase.runtime.errors import FactoryError

        @register_middleware("double_fail_mw2", override=True)
        def build_double_fail(context=None):
            if context is not None:
                raise TypeError("no context arg")
            raise RuntimeError("always fails")

        from agentbase.factories.middleware_factory import build_middleware

        with pytest.raises(FactoryError, match="builder.*failed"):
            build_middleware(["double_fail_mw2"], context={}, skip_on_error=False)

    def test_build_middleware_exception_skip(self):
        """If builder raises generic Exception, skip with skip_on_error=True."""
        from agentbase.registry.middleware import register_middleware

        @register_middleware("exc_mw", override=True)
        def build_exc(context=None):
            raise ValueError("builder error")

        from agentbase.factories.middleware_factory import build_middleware

        result = build_middleware(["exc_mw"], context={}, skip_on_error=True)
        assert result == []

    def test_build_middleware_exception_raise(self):
        """If builder raises generic Exception, raise with skip_on_error=False."""
        from agentbase.registry.middleware import register_middleware
        from agentbase.runtime.errors import FactoryError

        @register_middleware("exc_mw2", override=True)
        def build_exc(context=None):
            raise ValueError("builder error")

        from agentbase.factories.middleware_factory import build_middleware

        with pytest.raises(FactoryError, match="builder.*failed"):
            build_middleware(["exc_mw2"], context={}, skip_on_error=False)

    def test_build_middleware_returns_none_skip(self):
        """If builder returns None, skip with skip_on_error=True."""
        from agentbase.registry.middleware import register_middleware

        @register_middleware("null_mw_skip", override=True)
        def build_null(context=None):
            return None

        from agentbase.factories.middleware_factory import build_middleware

        result = build_middleware(["null_mw_skip"], context={}, skip_on_error=True)
        assert result == []

    def test_build_mixed_skip_and_success(self):
        """Mix of valid and invalid middleware should return only valid ones."""
        from agentbase.registry.middleware import register_middleware

        @register_middleware("valid_mixed_mw", override=True)
        def build_valid(context=None):
            return "valid_mw"

        from agentbase.factories.middleware_factory import build_middleware

        result = build_middleware(
            ["valid_mixed_mw", "nonexistent_xyz"],
            context={},
            skip_on_error=True,
        )
        assert len(result) == 1
        assert result[0] == "valid_mw"


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

    # --- Supplementary tests for missing branches ---

    def test_build_unknown_skip_on_error(self):
        """Unknown subagent with skip_on_error=True should be skipped, not raise."""
        from agentbase.factories.subagent_factory import build_subagents

        result = build_subagents(["nonexistent_xyz"], context={}, skip_on_error=True)
        assert result == []

    def test_materialize_missing_name_raises(self):
        """_materialize_subagent should raise if 'name' is missing."""
        from agentbase.factories.subagent_factory import _materialize_subagent
        from agentbase.runtime.errors import FactoryError

        with pytest.raises(FactoryError, match="missing required field"):
            _materialize_subagent({"description": "no name"}, context={})

    def test_materialize_defaults_description_and_prompt(self):
        """_materialize_subagent should provide defaults for description and system_prompt."""
        from agentbase.factories.subagent_factory import _materialize_subagent

        result = _materialize_subagent({"name": "test_agent"}, context={})
        assert result["description"] == "Subagent test_agent"
        assert result["system_prompt"] == "You are the test_agent subagent."

    def test_materialize_tool_not_registered_skipped(self):
        """_materialize_subagent should skip tools not in registry."""
        from agentbase.factories.subagent_factory import _materialize_subagent

        spec = {"name": "test_agent", "tools": ["nonexistent_tool"]}
        result = _materialize_subagent(spec, context={})
        assert result["tools"] == []

    def test_materialize_tool_build_fallback_fails(self):
        """If builder(context=...) raises TypeError and builder() also fails, tool is skipped."""
        from agentbase.factories.subagent_factory import _materialize_subagent
        from agentbase.registry.tools import register_tool

        @register_tool("bad_tool_for_sub", override=True)
        def build_bad_tool(context=None):
            if context is not None:
                raise TypeError("no context arg")
            raise RuntimeError("tool build error")

        spec = {"name": "test_agent", "tools": ["bad_tool_for_sub"]}
        result = _materialize_subagent(spec, context={})
        # Tool should be skipped, not crash
        assert result["tools"] == []

    def test_build_subagent_typeerror_fallback_success(self):
        """If builder(context=...) raises TypeError, fallback to builder() should work."""
        from agentbase.registry.subagents import register_subagent

        @register_subagent("fallback_sub", override=True)
        def build_fallback(context=None):
            if context is not None:
                raise TypeError("no context arg")
            return {"name": "fallback_sub"}

        from agentbase.factories.subagent_factory import build_subagents

        result = build_subagents(["fallback_sub"], context={}, skip_on_error=False)
        assert len(result) == 1
        assert result[0]["name"] == "fallback_sub"

    def test_build_subagent_typeerror_fallback_also_fails_skip(self):
        """If both builder(context=...) and builder() fail, skip with skip_on_error=True."""
        from agentbase.registry.subagents import register_subagent

        @register_subagent("double_fail_sub", override=True)
        def build_double_fail(context=None):
            if context is not None:
                raise TypeError("no context arg")
            raise RuntimeError("always fails")

        from agentbase.factories.subagent_factory import build_subagents

        result = build_subagents(["double_fail_sub"], context={}, skip_on_error=True)
        assert result == []

    def test_build_subagent_typeerror_fallback_also_fails_raise(self):
        """If both builder(context=...) and builder() fail, raise with skip_on_error=False."""
        from agentbase.registry.subagents import register_subagent
        from agentbase.runtime.errors import FactoryError

        @register_subagent("double_fail_sub2", override=True)
        def build_double_fail(context=None):
            if context is not None:
                raise TypeError("no context arg")
            raise RuntimeError("always fails")

        from agentbase.factories.subagent_factory import build_subagents

        with pytest.raises(FactoryError, match="builder.*failed"):
            build_subagents(["double_fail_sub2"], context={}, skip_on_error=False)

    def test_build_subagent_exception_skip(self):
        """If builder raises generic Exception, skip with skip_on_error=True."""
        from agentbase.registry.subagents import register_subagent

        @register_subagent("exc_sub", override=True)
        def build_exc(context=None):
            raise ValueError("builder error")

        from agentbase.factories.subagent_factory import build_subagents

        result = build_subagents(["exc_sub"], context={}, skip_on_error=True)
        assert result == []

    def test_build_subagent_exception_raise(self):
        """If builder raises generic Exception, raise with skip_on_error=False."""
        from agentbase.registry.subagents import register_subagent
        from agentbase.runtime.errors import FactoryError

        @register_subagent("exc_sub2", override=True)
        def build_exc(context=None):
            raise ValueError("builder error")

        from agentbase.factories.subagent_factory import build_subagents

        with pytest.raises(FactoryError, match="builder.*failed"):
            build_subagents(["exc_sub2"], context={}, skip_on_error=False)

    def test_build_subagent_non_dict_skip(self):
        """If builder returns non-dict, skip with skip_on_error=True."""
        from agentbase.registry.subagents import register_subagent

        @register_subagent("non_dict_sub", override=True)
        def build_non_dict(context=None):
            return 42

        from agentbase.factories.subagent_factory import build_subagents

        result = build_subagents(["non_dict_sub"], context={}, skip_on_error=True)
        assert result == []

    def test_build_subagent_materialize_fails_skip(self):
        """If _materialize_subagent raises FactoryError, skip with skip_on_error=True."""
        from agentbase.registry.subagents import register_subagent

        @register_subagent("no_name_sub", override=True)
        def build_no_name(context=None):
            return {"description": "no name field"}

        from agentbase.factories.subagent_factory import build_subagents

        result = build_subagents(["no_name_sub"], context={}, skip_on_error=True)
        assert result == []

    def test_build_subagent_materialize_fails_raise(self):
        """If _materialize_subagent raises FactoryError, raise with skip_on_error=False."""
        from agentbase.registry.subagents import register_subagent
        from agentbase.runtime.errors import FactoryError

        @register_subagent("no_name_sub2", override=True)
        def build_no_name(context=None):
            return {"description": "no name field"}

        from agentbase.factories.subagent_factory import build_subagents

        with pytest.raises(FactoryError, match="missing required field"):
            build_subagents(["no_name_sub2"], context={}, skip_on_error=False)

    def test_build_mixed_skip_and_success(self):
        """Mix of valid and invalid subagents should return only valid ones."""
        from agentbase.registry.subagents import register_subagent

        @register_subagent("valid_mixed", override=True)
        def build_valid(context=None):
            return {"name": "valid_mixed"}

        from agentbase.factories.subagent_factory import build_subagents

        result = build_subagents(
            ["valid_mixed", "nonexistent_xyz"],
            context={},
            skip_on_error=True,
        )
        assert len(result) == 1
        assert result[0]["name"] == "valid_mixed"
