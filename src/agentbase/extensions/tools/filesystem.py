"""Filesystem tools — read, write, and search files within the workspace.

All file operations are constrained to the workspace directory to prevent
path traversal attacks. The ``resolve_within_workspace`` function enforces
this by resolving the path and checking that it stays within the boundary.

Tools provided:
- ``read_file`` — read a text file (with size limit and binary detection)
- ``write_file`` — write content to a file
- ``grep`` — search file contents with a regex pattern

Safety features:
- Path traversal protection via ``resolve_within_workspace``
- File size limit on read (default 1MB)
- Binary file detection (refuses to read binary as text)
- Match limit on grep (default 200 results)
- File encoding always UTF-8 for writes
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from agentbase.extensions._meta import ExtensionMeta
from agentbase.extensions.tools._workspace import resolve_within_workspace
from agentbase.registry.tools import register_tool

_READ_META = ExtensionMeta(
    name="read_file", kind="tool", description="Read a file within workspace.", requires_context=["workspace_dir"]
)
_WRITE_META = ExtensionMeta(
    name="write_file", kind="tool", description="Write content to a file within workspace.", requires_context=["workspace_dir"]
)
_GREP_META = ExtensionMeta(
    name="grep", kind="tool", description="Search file contents with a regex pattern.", requires_context=["workspace_dir"]
)

# Safety limits
_MAX_READ_SIZE = 1_048_576  # 1 MB max read size
_MAX_GREP_RESULTS = 200      # Max grep matches before truncation
_MAX_GREP_FILES = 1000      # Max files to scan in grep
_BINARY_CHECK_BYTES = 1024  # First N bytes to check for binary content


def _workspace_path(context: dict[str, Any] | None) -> Path:
    context = context or {}
    workspace = context.get("workspace_dir")
    if workspace is None:
        root_dir = context.get("root_dir")
        workspace = Path(root_dir) / "workspace" if root_dir else Path("workspace")
    return Path(workspace)


def _is_binary(path: Path) -> bool:
    """Check if a file appears to be binary by reading the first N bytes.

    A file is considered binary if it contains null bytes or has a
    high proportion of non-text bytes in the first 1024 bytes.
    """
    try:
        with open(path, "rb") as f:
            chunk = f.read(_BINARY_CHECK_BYTES)
        if b"\x00" in chunk:
            return True
        # Check for high proportion of non-text bytes
        text_chars = bytes(range(32, 127)) + b"\n\r\t\f\b"
        non_text = sum(1 for b in chunk if b not in text_chars)
        return non_text / max(len(chunk), 1) > 0.30
    except Exception:
        return False


@register_tool("read_file", meta=_READ_META)
def build_read_file_tool(context: dict[str, Any] | None = None):
    workspace_path = _workspace_path(context)

    @tool
    def read_file(path: str) -> str:
        """Read the contents of a file within the workspace."""
        try:
            target = resolve_within_workspace(workspace_path, path)
        except ValueError as exc:
            return str(exc)
        if not target.exists():
            return f"Path not found: {path}"
        if target.is_dir():
            return f"Path is a directory: {path}"
        # Check file size
        file_size = target.stat().st_size
        if file_size > _MAX_READ_SIZE:
            return f"File too large: {file_size} bytes (max {_MAX_READ_SIZE} bytes). Path: {path}"
        # Check for binary content
        if _is_binary(target):
            return f"Binary file detected (not readable as text). Path: {path}"
        try:
            return target.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return f"Read failed: {exc}"

    return read_file


@register_tool("write_file", meta=_WRITE_META)
def build_write_file_tool(context: dict[str, Any] | None = None):
    workspace_path = _workspace_path(context)

    @tool
    def write_file(path: str, content: str) -> str:
        """Write content to a file within the workspace."""
        try:
            target = resolve_within_workspace(workspace_path, path)
        except ValueError as exc:
            return str(exc)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} chars to {path}"
        except Exception as exc:  # noqa: BLE001
            return f"Write failed: {exc}"

    return write_file


@register_tool("grep", meta=_GREP_META)
def build_grep_tool(context: dict[str, Any] | None = None):
    workspace_path = _workspace_path(context)

    @tool
    def grep(pattern: str, path: str = ".") -> str:
        """Search file contents under a path with a regex pattern."""
        try:
            target = resolve_within_workspace(workspace_path, path)
        except ValueError as exc:
            return str(exc)
        if not target.exists():
            return f"Path not found: {path}"

        files = [target] if target.is_file() else sorted(target.rglob("*"))
        # Limit number of files to scan
        if len(files) > _MAX_GREP_FILES:
            files = files[:_MAX_GREP_FILES]

        results: list[str] = []
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            return f"Invalid pattern: {exc}"

        for f in files:
            if not f.is_file():
                continue
            # Skip binary files
            if _is_binary(f):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if compiled.search(line):
                    rel = f.relative_to(workspace_path.resolve()).as_posix()
                    results.append(f"{rel}:{lineno}:{line}")
            if len(results) >= _MAX_GREP_RESULTS:
                results.append("... (truncated at 200 matches)")
                break
        return "\n".join(results) if results else "<no matches>"

    return grep
