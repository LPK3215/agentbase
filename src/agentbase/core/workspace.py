"""Workspace file management with structured directory separation.

Provides a ``WorkspaceManager`` that maintains three distinct directories:
- ``workspace/`` — user's persistent workspace (shared across sessions)
- ``uploads/`` — files uploaded by users for the current session
- ``outputs/`` — files produced by the agent for the current session

This separation enables clean file lifecycle management and is
especially important for multi-tenant scenarios.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class FileEntry:
    """A file entry in the workspace."""
    path: str
    name: str
    size: int
    kind: str  # "workspace", "uploads", "outputs"
    is_dir: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "size": self.size,
            "kind": self.kind,
            "is_dir": self.is_dir,
        }


class WorkspaceManager:
    """Manages structured file directories for an agent session.

    Directory structure::

        <base_dir>/
        ├── workspace/     # persistent, shared across sessions
        ├── uploads/       # per-session user uploads
        └── outputs/       # per-session agent outputs
    """

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.workspace_dir = self.base_dir / "workspace"
        self.uploads_dir = self.base_dir / "uploads"
        self.outputs_dir = self.base_dir / "outputs"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for d in (self.workspace_dir, self.uploads_dir, self.outputs_dir):
            d.mkdir(parents=True, exist_ok=True)

    def resolve(self, kind: str, path: str) -> Path:
        """Resolve a path within a specific kind directory.

        Raises ValueError if the path escapes the directory.
        """
        kind = kind.lower()
        base = {
            "workspace": self.workspace_dir,
            "uploads": self.uploads_dir,
            "outputs": self.outputs_dir,
        }.get(kind)
        if base is None:
            raise ValueError(f"Unknown file kind: {kind}. Use workspace/uploads/outputs.")

        target = (base / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            raise ValueError(f"Path escapes {kind} directory: {path}") from None
        return target

    def write(self, kind: str, path: str, content: str | bytes) -> Path:
        """Write content to a file using atomic write (temp file + rename).

        This prevents partial writes from being visible to concurrent
        readers — the file appears atomically with its full content.
        """
        target = self.resolve(kind, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file in the same directory, then rename
        fd, tmp_path = tempfile.mkstemp(
            dir=str(target.parent),
            prefix=".tmp_",
            suffix=target.suffix or ".tmp",
        )
        try:
            with os.fdopen(fd, "w" if isinstance(content, str) else "wb") as f:
                f.write(content)
            # Atomic rename — on most OS this is atomic when on same filesystem
            os.replace(tmp_path, target)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return target

    def read(self, kind: str, path: str) -> str:
        """Read a text file from the specified directory."""
        target = self.resolve(kind, path)
        if not target.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return target.read_text(encoding="utf-8")

    def read_bytes(self, kind: str, path: str) -> bytes:
        """Read a binary file from the specified directory."""
        target = self.resolve(kind, path)
        if not target.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return target.read_bytes()

    def list_files(self, kind: str, pattern: str = "**/*") -> list[FileEntry]:
        """List files in a directory, optionally filtered by glob pattern."""
        base = {
            "workspace": self.workspace_dir,
            "uploads": self.uploads_dir,
            "outputs": self.outputs_dir,
        }.get(kind.lower())
        if base is None:
            raise ValueError(f"Unknown file kind: {kind}")

        entries: list[FileEntry] = []
        for item in sorted(base.glob(pattern)):
            if item.is_file():
                rel = str(item.relative_to(base))
                entries.append(FileEntry(
                    path=rel,
                    name=item.name,
                    size=item.stat().st_size,
                    kind=kind.lower(),
                    is_dir=False,
                ))
            elif item.is_dir():
                rel = str(item.relative_to(base))
                entries.append(FileEntry(
                    path=rel,
                    name=item.name,
                    size=0,
                    kind=kind.lower(),
                    is_dir=True,
                ))
        return entries

    def delete(self, kind: str, path: str, *, recursive: bool = False) -> bool:
        """Delete a file (or directory if ``recursive=True``) from the specified directory.

        Args:
            kind: Directory kind (workspace/uploads/outputs).
            path: Relative path to delete.
            recursive: If True and target is a directory, delete it recursively.
                       If False and target is a directory, return False.
        """
        target = self.resolve(kind, path)
        if not target.exists():
            return False
        if target.is_file():
            target.unlink()
            return True
        if target.is_dir() and recursive:
            shutil.rmtree(target)
            return True
        return False

    def move(self, kind: str, src: str, dest: str) -> Path:
        """Move a file within the same kind directory."""
        src_path = self.resolve(kind, src)
        dest_path = self.resolve(kind, dest)
        if not src_path.exists():
            raise FileNotFoundError(f"Source not found: {src}")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_path), str(dest_path))
        return dest_path

    def copy(self, kind: str, src: str, dest: str) -> Path:
        """Copy a file within the same kind directory."""
        src_path = self.resolve(kind, src)
        dest_path = self.resolve(kind, dest)
        if not src_path.exists():
            raise FileNotFoundError(f"Source not found: {src}")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if src_path.is_file():
            shutil.copy2(str(src_path), str(dest_path))
        else:
            shutil.copytree(str(src_path), str(dest_path))
        return dest_path

    def size(self, kind: str, path: str = ".") -> int:
        """Return the total size in bytes of a file or directory."""
        target = self.resolve(kind, path)
        if not target.exists():
            return 0
        if target.is_file():
            return target.stat().st_size
        total = 0
        for f in target.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
        return total

    def clear(self, kind: str | None = None, *, recursive: bool = True) -> None:
        """Clear files from a directory (or all if kind is None).

        Args:
            kind: Directory kind to clear, or None for all.
            recursive: If True, delete subdirectories recursively.
        """
        kinds = [kind] if kind else ["workspace", "uploads", "outputs"]
        for k in kinds:
            base = {
                "workspace": self.workspace_dir,
                "uploads": self.uploads_dir,
                "outputs": self.outputs_dir,
            }.get(k.lower())
            if base and base.exists():
                for item in base.iterdir():
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir() and recursive:
                        shutil.rmtree(item)

    def exists(self, kind: str, path: str) -> bool:
        """Check if a file exists in the specified directory."""
        return self.resolve(kind, path).exists()

    def get_path_for_agent(self, kind: str, path: str) -> str:
        """Get the full filesystem path for agent tool consumption."""
        return str(self.resolve(kind, path))

    def get_stats(self) -> dict[str, Any]:
        """Return disk usage statistics for all workspace directories.

        Returns a dict with per-kind file count, total size in bytes,
        and an overall summary.
        """
        stats: dict[str, Any] = {"directories": {}, "total_size": 0, "total_files": 0}
        for kind, base in [
            ("workspace", self.workspace_dir),
            ("uploads", self.uploads_dir),
            ("outputs", self.outputs_dir),
        ]:
            file_count = 0
            dir_count = 0
            total_size = 0
            if base.exists():
                for item in base.rglob("*"):
                    if item.is_file():
                        file_count += 1
                        total_size += item.stat().st_size
                    elif item.is_dir():
                        dir_count += 1
            stats["directories"][kind] = {
                "path": str(base),
                "files": file_count,
                "dirs": dir_count,
                "size_bytes": total_size,
                "size_mb": round(total_size / (1024 * 1024), 2),
            }
            stats["total_size"] += total_size
            stats["total_files"] += file_count
        stats["total_size_mb"] = round(stats["total_size"] / (1024 * 1024), 2)
        return stats
