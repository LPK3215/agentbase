"""Skill management — file-based CRUD with optional YAML frontmatter.

Skills are markdown files stored under ``workspace/skills/``.  Each file may
begin with a YAML frontmatter block (``---`` delimited) containing metadata
fields ``name``, ``description``, ``triggers``.  The remainder is the skill
body that gets injected into an agent's context.

Usage::

    mgr = SkillManager(workspace_dir=Path("workspace"))
    mgr.create("code_review", "Review code before approval.", body="# ...")
    skill = mgr.get("code_review")
    mgr.delete("code_review")
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Skill:
    """A single skill record."""

    name: str
    description: str = ""
    triggers: list[str] = field(default_factory=list)
    body: str = ""
    file_path: Path | None = None

    @property
    def content(self) -> str:
        """Full markdown content (frontmatter + body)."""
        if not self.description and not self.triggers:
            return self.body
        front = {"name": self.name}
        if self.description:
            front["description"] = self.description
        if self.triggers:
            front["triggers"] = self.triggers
        return f"---\n{yaml.dump(front, allow_unicode=True, default_flow_style=False).strip()}\n---\n\n{self.body}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "triggers": self.triggers,
            "body": self.body,
            "file_path": str(self.file_path) if self.file_path else None,
        }


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def _parse_skill(raw: str, file_path: Path | None = None) -> Skill:
    """Parse a markdown skill file into a :class:`Skill`."""
    match = _FRONTMATTER_RE.match(raw)
    if match:
        meta = yaml.safe_load(match.group(1)) or {}
        body = match.group(2).strip()
    else:
        meta = {}
        body = raw.strip()
    return Skill(
        name=str(meta.get("name", file_path.stem if file_path else "")),
        description=str(meta.get("description", "")),
        triggers=list(meta.get("triggers", []) or []),
        body=body,
        file_path=file_path,
    )


class SkillManager:
    """File-based skill CRUD manager.

    Skills are stored as ``*.md`` files inside ``skills_dir``.  The manager
    handles creation, reading, updating, deletion and simple text search.

    Features:
    - Thread-safe via ``threading.Lock``
    - Atomic writes (temp file + rename) to prevent corruption
    - Path traversal protection (sanitised names)
    """

    def __init__(self, *, skills_dir: Path) -> None:
        self.skills_dir = Path(skills_dir)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(self, name: str, description: str = "", body: str = "", triggers: list[str] | None = None) -> Skill:
        """Create a new skill.  Raises ``ValueError`` if it already exists."""
        path = self._path(name)
        if path.exists():
            raise ValueError(f"Skill already exists: {name}")
        return self._write(name, description=description, body=body, triggers=triggers or [])

    def get(self, name: str) -> Skill:
        """Read a skill by name.  Raises ``KeyError`` if not found."""
        path = self._path(name)
        if not path.exists():
            raise KeyError(f"Skill not found: {name}")
        return _parse_skill(path.read_text(encoding="utf-8"), file_path=path)

    def update(
        self,
        name: str,
        *,
        description: str | None = None,
        body: str | None = None,
        triggers: list[str] | None = None,
    ) -> Skill:
        """Update fields of an existing skill.  ``None`` fields are left unchanged."""
        existing = self.get(name)
        return self._write(
            name,
            description=description if description is not None else existing.description,
            body=body if body is not None else existing.body,
            triggers=triggers if triggers is not None else existing.triggers,
        )

    def delete(self, name: str) -> bool:
        """Delete a skill.  Returns ``True`` if deleted, ``False`` if not found."""
        with self._lock:
            path = self._path(name)
            if not path.exists():
                return False
            path.unlink()
            return True

    def exists(self, name: str) -> bool:
        """Check if a skill exists."""
        return self._path(name).exists()

    def count(self) -> int:
        """Return the total number of skills."""
        return len(list(self.skills_dir.glob("*.md")))

    def list(self) -> list[Skill]:
        """List all skills sorted by name."""
        skills: list[Skill] = []
        for path in sorted(self.skills_dir.glob("*.md")):
            skills.append(_parse_skill(path.read_text(encoding="utf-8"), file_path=path))
        return skills

    def search(self, query: str) -> list[Skill]:
        """Simple case-insensitive text search across name, description and body."""
        q = query.lower()
        return [s for s in self.list() if q in s.name.lower() or q in s.description.lower() or q in s.body.lower()]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _path(self, name: str) -> Path:
        # Prevent path traversal: only allow alphanumeric, underscore, hyphen.
        safe = re.sub(r"[^A-Za-z0-9_\-]", "_", name)
        return self.skills_dir / f"{safe}.md"

    def _write(self, name: str, *, description: str, body: str, triggers: list[str]) -> Skill:
        skill = Skill(name=name, description=description, triggers=triggers, body=body, file_path=self._path(name))
        content = skill.content
        target = self._path(name)
        # Atomic write: write to temp file, then rename
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.skills_dir),
            prefix=".tmp_skill_",
            suffix=".md",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, target)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return skill
