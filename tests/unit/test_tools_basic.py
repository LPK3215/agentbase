"""Unit tests for basic tools (echo, get_time, list_workspace)."""
from __future__ import annotations


class TestEchoTool:
    def test_echo(self):
        from agentbase.extensions.tools.basic import build_echo_tool

        tool_fn = build_echo_tool(context={})
        result = tool_fn.invoke({"text": "hello world"})
        assert result == "hello world"

    def test_echo_empty(self):
        from agentbase.extensions.tools.basic import build_echo_tool

        tool_fn = build_echo_tool(context={})
        result = tool_fn.invoke({"text": ""})
        assert result == ""


class TestGetTimeTool:
    def test_returns_iso_format(self):
        from agentbase.extensions.tools.basic import build_get_time_tool

        tool_fn = build_get_time_tool(context={})
        result = tool_fn.invoke({})
        # Should be ISO format with timezone
        assert "T" in result
        assert "+" in result or "Z" in result

    def test_returns_utc(self):
        from agentbase.extensions.tools.basic import build_get_time_tool

        tool_fn = build_get_time_tool(context={})
        result = tool_fn.invoke({})
        # UTC should end with +00:00
        assert "+00:00" in result


class TestListWorkspaceTool:
    def test_list_files(self, tmp_path):
        from agentbase.extensions.tools.basic import build_list_workspace_tool

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "file1.txt").write_text("a")
        (workspace / "file2.md").write_text("b")
        subdir = workspace / "subdir"
        subdir.mkdir()

        tool_fn = build_list_workspace_tool(context={"workspace_dir": workspace})
        result = tool_fn.invoke({"relative_path": "."})
        assert "file1.txt" in result
        assert "file2.md" in result
        assert "subdir" in result
        assert "DIR" in result
        assert "FILE" in result

    def test_list_empty(self, tmp_path):
        from agentbase.extensions.tools.basic import build_list_workspace_tool

        workspace = tmp_path / "empty_ws"
        workspace.mkdir()

        tool_fn = build_list_workspace_tool(context={"workspace_dir": workspace})
        result = tool_fn.invoke({"relative_path": "."})
        assert "empty" in result.lower()

    def test_list_file_target(self, tmp_path):
        from agentbase.extensions.tools.basic import build_list_workspace_tool

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "single.txt").write_text("hello", encoding="utf-8")

        tool_fn = build_list_workspace_tool(context={"workspace_dir": workspace})
        result = tool_fn.invoke({"relative_path": "single.txt"})
        assert "FILE" in result
        assert "single.txt" in result

    def test_list_path_not_found(self, tmp_path):
        from agentbase.extensions.tools.basic import build_list_workspace_tool

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        tool_fn = build_list_workspace_tool(context={"workspace_dir": workspace})
        result = tool_fn.invoke({"relative_path": "nonexistent"})
        assert "not found" in result.lower()

    def test_list_path_traversal(self, tmp_path):
        from agentbase.extensions.tools.basic import build_list_workspace_tool

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        tool_fn = build_list_workspace_tool(context={"workspace_dir": workspace})
        result = tool_fn.invoke({"relative_path": "../../../etc"})
        assert "escapes" in result.lower() or "workspace" in result.lower()

    def test_list_fallback_workspace(self, tmp_path):
        """When workspace_dir is not in context, fallback to root_dir/workspace."""
        from agentbase.extensions.tools.basic import build_list_workspace_tool

        root = tmp_path
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "fallback.txt").write_text("test")

        tool_fn = build_list_workspace_tool(context={"root_dir": root})
        result = tool_fn.invoke({"relative_path": "."})
        assert "fallback.txt" in result


class TestReadFileTool:
    def test_read_existing(self, tmp_path):
        from agentbase.extensions.tools.filesystem import build_read_file_tool

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "doc.txt").write_text("Hello content", encoding="utf-8")

        tool_fn = build_read_file_tool(context={"workspace_dir": workspace})
        result = tool_fn.invoke({"path": "doc.txt"})
        assert "Hello content" in result

    def test_read_not_found(self, tmp_path):
        from agentbase.extensions.tools.filesystem import build_read_file_tool

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        tool_fn = build_read_file_tool(context={"workspace_dir": workspace})
        result = tool_fn.invoke({"path": "missing.txt"})
        assert "not found" in result.lower() or "Path not found" in result

    def test_read_directory(self, tmp_path):
        from agentbase.extensions.tools.filesystem import build_read_file_tool

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "subdir").mkdir()

        tool_fn = build_read_file_tool(context={"workspace_dir": workspace})
        result = tool_fn.invoke({"path": "subdir"})
        assert "directory" in result.lower()


class TestWriteFileTool:
    def test_write_new(self, tmp_path):
        from agentbase.extensions.tools.filesystem import build_write_file_tool

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        tool_fn = build_write_file_tool(context={"workspace_dir": workspace})
        result = tool_fn.invoke({"path": "new.txt", "content": "new content"})
        assert "Written" in result or "wrote" in result.lower()
        assert (workspace / "new.txt").read_text() == "new content"

    def test_write_overwrite(self, tmp_path):
        from agentbase.extensions.tools.filesystem import build_write_file_tool

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "existing.txt").write_text("old", encoding="utf-8")

        tool_fn = build_write_file_tool(context={"workspace_dir": workspace})
        tool_fn.invoke({"path": "existing.txt", "content": "new"})
        assert (workspace / "existing.txt").read_text() == "new"


class TestGrepTool:
    def test_grep_found(self, tmp_path):
        from agentbase.extensions.tools.filesystem import build_grep_tool

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "code.py").write_text("def hello():\n    print('world')\n", encoding="utf-8")

        tool_fn = build_grep_tool(context={"workspace_dir": workspace})
        result = tool_fn.invoke({"pattern": "hello"})
        assert "hello" in result or "code.py" in result

    def test_grep_no_match(self, tmp_path):
        from agentbase.extensions.tools.filesystem import build_grep_tool

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "code.py").write_text("def foo():\n    pass\n", encoding="utf-8")

        tool_fn = build_grep_tool(context={"workspace_dir": workspace})
        result = tool_fn.invoke({"pattern": "nonexistent_pattern_xyz"})
        assert "no match" in result.lower() or "No match" in result or result.strip() == ""
