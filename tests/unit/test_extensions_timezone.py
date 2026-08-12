from __future__ import annotations

from agentbase.extensions.tools.timezone import build_now_local_tool


def test_now_local_utc():
    tool = build_now_local_tool({})
    result = tool.invoke({"timezone_name": "UTC"})
    assert "T" in result
    assert "Invalid" not in result


def test_now_local_default():
    tool = build_now_local_tool({})
    result = tool.invoke({})
    assert "T" in result


def test_now_local_valid_timezone():
    tool = build_now_local_tool({})
    result = tool.invoke({"timezone_name": "America/New_York"})
    assert "Invalid" not in result


def test_now_local_invalid_timezone():
    tool = build_now_local_tool({})
    result = tool.invoke({"timezone_name": "Not/A/Real/Zone"})
    assert "Invalid" in result