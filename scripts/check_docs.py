#!/usr/bin/env python
"""Check documentation consistency: CLI args vs README, link reachability."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def check_cli_in_readme(root: Path) -> list[str]:
    errors: list[str] = []
    readme = root / "README.md"
    if not readme.exists():
        errors.append("AGENTBASE_DOC_001: README.md not found")
        return errors
    content = readme.read_text(encoding="utf-8")
    expected_commands = ["agentbase doctor", "agentbase agents", "agentbase extensions", "agentbase run", "agentbase stream", "agentbase resume"]
    for cmd in expected_commands:
        if cmd not in content:
            errors.append(f"AGENTBASE_DOC_001: README.md missing command example: {cmd}")
    return errors


def check_internal_links(root: Path) -> list[str]:
    errors: list[str] = []
    docs_dir = root / "docs"
    if not docs_dir.exists():
        return errors
    for md_file in [root / "README.md", *docs_dir.glob("*.md")]:
        if not md_file.exists():
            continue
        content = md_file.read_text(encoding="utf-8")
        for match in re.finditer(r"\[([^\]]+)\]\(([^)]+\.md)\)", content):
            link = match.group(2)
            if link.startswith("http"):
                continue
            target = (md_file.parent / link).resolve()
            if not target.exists():
                errors.append(f"AGENTBASE_DOC_002: {md_file.name} links to missing file: {link}")
    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = check_cli_in_readme(root) + check_internal_links(root)
    if errors:
        for err in errors:
            print(err)
        return 1
    print("Documentation checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())