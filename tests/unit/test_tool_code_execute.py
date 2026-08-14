"""Unit tests for the code_execute tool.

Covers the three required paths:
- normal: factory assembly + real subprocess execution
- boundary: timeout enforcement, code size limit
- error: unsupported language, runtime error in executed code
"""
from __future__ import annotations

import pytest

from agentbase.factories.tool_factory import build_tools


class TestCodeExecuteAssembly:
    """Factory assembly path — the tool must be buildable via the registry contract."""

    def test_builds_via_factory(self, bootstrapped):
        tools = build_tools(["code_execute"], skip_on_error=False)
        assert len(tools) == 1
        assert tools[0].name == "code_execute"

    def test_builds_via_factory_lenient(self, bootstrapped):
        # Default skip_on_error=True must also yield the tool (no silent skip).
        tools = build_tools(["code_execute"])
        assert len(tools) == 1


class TestCodeExecuteNormal:
    @pytest.fixture()
    def tool(self, bootstrapped):
        return build_tools(["code_execute"], skip_on_error=False)[0]

    def test_executes_python_code(self, tool):
        result = tool.invoke({"code": "print('hello agentbase')"})
        assert result["exit_code"] == 0
        assert "hello agentbase" in result["stdout"]
        assert result["timed_out"] is False

    def test_captures_stderr_and_exit_code(self, tool):
        result = tool.invoke({"code": "import sys; sys.stderr.write('oops'); sys.exit(3)"})
        assert result["exit_code"] == 3
        assert "oops" in result["stderr"]


class TestCodeExecuteBoundary:
    @pytest.fixture()
    def tool(self, bootstrapped):
        return build_tools(["code_execute"], skip_on_error=False)[0]

    def test_enforces_timeout(self, tool):
        result = tool.invoke({"code": "while True: pass", "timeout": 1})
        assert result["timed_out"] is True
        assert result["exit_code"] == -1
        assert "timed out" in result["stderr"]

    def test_rejects_oversized_code(self, tool):
        result = tool.invoke({"code": "x = 1\n" * 60_000})
        assert result["exit_code"] == 1
        assert "too large" in result["stderr"]


class TestCodeExecuteError:
    @pytest.fixture()
    def tool(self, bootstrapped):
        return build_tools(["code_execute"], skip_on_error=False)[0]

    def test_rejects_unsupported_language(self, tool):
        result = tool.invoke({"code": "console.log(1)", "language": "javascript"})
        assert result["exit_code"] == 1
        assert "Unsupported language" in result["stderr"]
        assert result["timed_out"] is False

    def test_runtime_error_is_captured(self, tool):
        result = tool.invoke({"code": "raise ValueError('boom')"})
        assert result["exit_code"] != 0
        assert "boom" in result["stderr"]
