from __future__ import annotations

from pathlib import Path

from agentbase.extensions.tools.filesystem import build_grep_tool, build_read_file_tool, build_write_file_tool


def _ctx(workspace: Path) -> dict:
    return {"workspace_dir": workspace}


def test_read_file_normal(tmp_workspace):
    (tmp_workspace / "test.txt").write_text("hello world", encoding="utf-8")
    tool = build_read_file_tool(_ctx(tmp_workspace))
    result = tool.invoke({"path": "test.txt"})
    assert result == "hello world"


def test_read_file_escape(tmp_workspace):
    tool = build_read_file_tool(_ctx(tmp_workspace))
    result = tool.invoke({"path": "../secret.txt"})
    assert "escapes workspace" in result


def test_read_file_not_found(tmp_workspace):
    tool = build_read_file_tool(_ctx(tmp_workspace))
    result = tool.invoke({"path": "nonexistent.txt"})
    assert "not found" in result


def test_write_file_normal(tmp_workspace):
    tool = build_write_file_tool(_ctx(tmp_workspace))
    result = tool.invoke({"path": "output.txt", "content": "data"})
    assert "Wrote" in result
    assert (tmp_workspace / "output.txt").read_text(encoding="utf-8") == "data"


def test_write_file_escape(tmp_workspace):
    tool = build_write_file_tool(_ctx(tmp_workspace))
    result = tool.invoke({"path": "../../etc/passwd", "content": "bad"})
    assert "escapes workspace" in result


def test_grep_match(tmp_workspace):
    (tmp_workspace / "a.txt").write_text("foo\nbar\nbaz\n", encoding="utf-8")
    tool = build_grep_tool(_ctx(tmp_workspace))
    result = tool.invoke({"pattern": "ba", "path": "."})
    assert "bar" in result
    assert "baz" in result


def test_grep_no_match(tmp_workspace):
    (tmp_workspace / "a.txt").write_text("foo\n", encoding="utf-8")
    tool = build_grep_tool(_ctx(tmp_workspace))
    result = tool.invoke({"pattern": "xyz", "path": "."})
    assert "no matches" in result


def test_grep_escape(tmp_workspace):
    tool = build_grep_tool(_ctx(tmp_workspace))
    result = tool.invoke({"pattern": "x", "path": "../../etc"})
    assert "escapes workspace" in result