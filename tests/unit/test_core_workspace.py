"""Unit tests for WorkspaceManager — file CRUD, path resolution, stats.

Covers: init/_ensure_dirs, resolve, write (text+bytes), read, read_bytes,
list_files, delete, move, copy, size, clear, exists, get_path_for_agent,
get_stats, FileEntry.to_dict.
"""
from __future__ import annotations

import pytest

from agentbase.core.workspace import FileEntry, WorkspaceManager

# ---------------------------------------------------------------------------
# __init__ / _ensure_dirs
# ---------------------------------------------------------------------------


class TestInit:
    def test_creates_directories(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        assert wm.workspace_dir.exists()
        assert wm.uploads_dir.exists()
        assert wm.outputs_dir.exists()

    def test_base_dir_resolved(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        assert wm.base_dir == tmp_path.resolve()

    def test_idempotent_init(self, tmp_path):
        wm1 = WorkspaceManager(tmp_path)
        wm2 = WorkspaceManager(tmp_path)
        assert wm1.workspace_dir == wm2.workspace_dir


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


class TestResolve:
    def test_resolve_workspace(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        path = wm.resolve("workspace", "file.txt")
        assert path == wm.workspace_dir / "file.txt"

    def test_resolve_uploads(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        path = wm.resolve("uploads", "upload.txt")
        assert path == wm.uploads_dir / "upload.txt"

    def test_resolve_outputs(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        path = wm.resolve("outputs", "output.txt")
        assert path == wm.outputs_dir / "output.txt"

    def test_resolve_case_insensitive_kind(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        path = wm.resolve("WORKSPACE", "file.txt")
        assert path == wm.workspace_dir / "file.txt"

    def test_resolve_unknown_kind_raises(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        with pytest.raises(ValueError, match="Unknown file kind"):
            wm.resolve("unknown", "file.txt")

    def test_resolve_path_escape_raises(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        with pytest.raises(ValueError, match="escapes"):
            wm.resolve("workspace", "../../etc/passwd")

    def test_resolve_nested_path(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        path = wm.resolve("workspace", "subdir/file.txt")
        assert path == wm.workspace_dir / "subdir" / "file.txt"


# ---------------------------------------------------------------------------
# write / read / read_bytes
# ---------------------------------------------------------------------------


class TestWriteRead:
    def test_write_text(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        path = wm.write("workspace", "test.txt", "hello world")
        assert path.exists()
        assert path.read_text() == "hello world"

    def test_write_bytes(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        path = wm.write("uploads", "data.bin", b"\x00\x01\x02")
        assert path.exists()
        assert path.read_bytes() == b"\x00\x01\x02"

    def test_write_nested_path(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        path = wm.write("outputs", "sub/dir/file.txt", "nested")
        assert path.exists()
        assert path.read_text() == "nested"

    def test_write_overwrites_existing(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        wm.write("workspace", "file.txt", "old")
        wm.write("workspace", "file.txt", "new")
        assert (wm.workspace_dir / "file.txt").read_text() == "new"

    def test_read_text(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        wm.write("workspace", "read.txt", "content")
        assert wm.read("workspace", "read.txt") == "content"

    def test_read_not_found(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        with pytest.raises(FileNotFoundError):
            wm.read("workspace", "nonexistent.txt")

    def test_read_bytes(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        wm.write("uploads", "binary.dat", b"\xff\xfe")
        assert wm.read_bytes("uploads", "binary.dat") == b"\xff\xfe"

    def test_read_bytes_not_found(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        with pytest.raises(FileNotFoundError):
            wm.read_bytes("uploads", "nonexistent.dat")


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------


class TestListFiles:
    def test_empty_dir(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        assert wm.list_files("workspace") == []

    def test_lists_files(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        wm.write("workspace", "a.txt", "a")
        wm.write("workspace", "b.txt", "b")
        entries = wm.list_files("workspace")
        assert len(entries) == 2
        names = [e.name for e in entries]
        assert "a.txt" in names
        assert "b.txt" in names

    def test_lists_dirs(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        (wm.workspace_dir / "subdir").mkdir()
        entries = wm.list_files("workspace")
        assert len(entries) == 1
        assert entries[0].is_dir is True

    def test_file_entry_to_dict(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        wm.write("workspace", "test.txt", "hello")
        entries = wm.list_files("workspace")
        d = entries[0].to_dict()
        assert d["path"] == "test.txt"
        assert d["name"] == "test.txt"
        assert d["size"] == 5
        assert d["kind"] == "workspace"
        assert d["is_dir"] is False

    def test_unknown_kind_raises(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        with pytest.raises(ValueError, match="Unknown file kind"):
            wm.list_files("unknown")

    def test_pattern_filter(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        wm.write("workspace", "a.txt", "a")
        wm.write("workspace", "b.log", "b")
        entries = wm.list_files("workspace", "*.txt")
        names = [e.name for e in entries]
        assert "a.txt" in names
        assert "b.log" not in names


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_file(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        wm.write("workspace", "del.txt", "content")
        assert wm.delete("workspace", "del.txt") is True
        assert not (wm.workspace_dir / "del.txt").exists()

    def test_delete_nonexistent_returns_false(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        assert wm.delete("workspace", "nonexistent.txt") is False

    def test_delete_dir_recursive(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        (wm.workspace_dir / "subdir").mkdir()
        (wm.workspace_dir / "subdir" / "file.txt").write_text("content")
        assert wm.delete("workspace", "subdir", recursive=True) is True
        assert not (wm.workspace_dir / "subdir").exists()

    def test_delete_dir_non_recursive_returns_false(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        (wm.workspace_dir / "subdir").mkdir()
        assert wm.delete("workspace", "subdir", recursive=False) is False
        assert (wm.workspace_dir / "subdir").exists()


# ---------------------------------------------------------------------------
# move / copy
# ---------------------------------------------------------------------------


class TestMoveCopy:
    def test_move_file(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        wm.write("workspace", "src.txt", "data")
        dest = wm.move("workspace", "src.txt", "dest.txt")
        assert dest.exists()
        assert not (wm.workspace_dir / "src.txt").exists()
        assert dest.read_text() == "data"

    def test_move_nonexistent_raises(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        with pytest.raises(FileNotFoundError):
            wm.move("workspace", "nonexistent.txt", "dest.txt")

    def test_copy_file(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        wm.write("workspace", "src.txt", "data")
        dest = wm.copy("workspace", "src.txt", "copy.txt")
        assert dest.exists()
        # Source still exists
        assert (wm.workspace_dir / "src.txt").exists()
        assert dest.read_text() == "data"

    def test_copy_nonexistent_raises(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        with pytest.raises(FileNotFoundError):
            wm.copy("workspace", "nonexistent.txt", "dest.txt")


# ---------------------------------------------------------------------------
# size
# ---------------------------------------------------------------------------


class TestSize:
    def test_size_of_file(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        wm.write("workspace", "sized.txt", "12345")
        assert wm.size("workspace", "sized.txt") == 5

    def test_size_of_nonexistent(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        assert wm.size("workspace", "nonexistent.txt") == 0

    def test_size_of_directory(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        wm.write("workspace", "a.txt", "1234")
        wm.write("workspace", "b.txt", "5678")
        total = wm.size("workspace", ".")
        assert total == 8  # 4 + 4


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


class TestClear:
    def test_clear_single_kind(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        wm.write("workspace", "file.txt", "content")
        wm.clear("workspace")
        assert not (wm.workspace_dir / "file.txt").exists()

    def test_clear_all_kinds(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        wm.write("workspace", "a.txt", "a")
        wm.write("uploads", "b.txt", "b")
        wm.write("outputs", "c.txt", "c")
        wm.clear()
        assert not any(wm.workspace_dir.iterdir())
        assert not any(wm.uploads_dir.iterdir())
        assert not any(wm.outputs_dir.iterdir())

    def test_clear_with_subdirs(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        wm.write("workspace", "sub/file.txt", "content")
        wm.clear("workspace", recursive=True)
        assert not (wm.workspace_dir / "sub").exists()

    def test_clear_non_recursive_keeps_subdirs(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        (wm.workspace_dir / "subdir").mkdir()
        (wm.workspace_dir / "subdir" / "file.txt").write_text("content")
        wm.write("workspace", "file.txt", "top")
        wm.clear("workspace", recursive=False)
        # Subdir kept (with its files), top file removed
        assert (wm.workspace_dir / "subdir").exists()
        assert not (wm.workspace_dir / "file.txt").exists()


# ---------------------------------------------------------------------------
# exists / get_path_for_agent
# ---------------------------------------------------------------------------


class TestExistsAndPath:
    def test_exists_true(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        wm.write("workspace", "exists.txt", "content")
        assert wm.exists("workspace", "exists.txt") is True

    def test_exists_false(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        assert wm.exists("workspace", "nonexistent.txt") is False

    def test_get_path_for_agent(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        path_str = wm.get_path_for_agent("workspace", "agent_file.txt")
        assert isinstance(path_str, str)
        assert "agent_file.txt" in path_str


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


class TestGetStats:
    def test_empty_stats(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        stats = wm.get_stats()
        assert stats["total_size"] == 0
        assert stats["total_files"] == 0
        assert "workspace" in stats["directories"]
        assert "uploads" in stats["directories"]
        assert "outputs" in stats["directories"]

    def test_stats_with_files(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        wm.write("workspace", "a.txt", "1234")
        wm.write("uploads", "b.txt", "5678")
        stats = wm.get_stats()
        assert stats["total_files"] == 2
        assert stats["total_size"] == 8
        assert stats["directories"]["workspace"]["files"] == 1
        assert stats["directories"]["uploads"]["files"] == 1
        assert stats["directories"]["workspace"]["size_bytes"] == 4

    def test_stats_with_subdirs(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        wm.write("workspace", "sub/a.txt", "data1")
        wm.write("workspace", "sub/b.txt", "data2")
        stats = wm.get_stats()
        assert stats["directories"]["workspace"]["files"] == 2
        assert stats["directories"]["workspace"]["dirs"] == 1

    def test_stats_size_mb(self, tmp_path):
        wm = WorkspaceManager(tmp_path)
        wm.write("workspace", "file.txt", "x" * 2048)
        stats = wm.get_stats()
        assert stats["total_size_mb"] == round(2048 / (1024 * 1024), 2)
        assert stats["directories"]["workspace"]["size_mb"] == round(2048 / (1024 * 1024), 2)


# ---------------------------------------------------------------------------
# FileEntry
# ---------------------------------------------------------------------------


class TestFileEntry:
    def test_file_entry_defaults(self):
        entry = FileEntry(path="a.txt", name="a.txt", size=10, kind="workspace")
        assert entry.is_dir is False

    def test_file_entry_is_dir(self):
        entry = FileEntry(path="subdir", name="subdir", size=0, kind="workspace", is_dir=True)
        assert entry.is_dir is True

    def test_to_dict(self):
        entry = FileEntry(path="file.txt", name="file.txt", size=100, kind="uploads", is_dir=False)
        d = entry.to_dict()
        assert d == {
            "path": "file.txt",
            "name": "file.txt",
            "size": 100,
            "kind": "uploads",
            "is_dir": False,
        }
