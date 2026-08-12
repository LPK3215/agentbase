from __future__ import annotations

from pathlib import Path


def resolve_within_workspace(workspace_dir: Path, target: str | Path) -> Path:
    """Resolve ``target`` relative to ``workspace_dir`` and enforce containment.

    Raises ValueError if the resolved path escapes the workspace boundary.
    """
    workspace_dir = Path(workspace_dir).resolve()
    resolved = (workspace_dir / target).resolve() if not Path(target).is_absolute() else Path(target).resolve()
    try:
        resolved.relative_to(workspace_dir)
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace: {target}") from exc
    return resolved