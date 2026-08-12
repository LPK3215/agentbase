"""Template rendering engine — variable substitution for project scaffolding.

Supports ``{{variable}}`` syntax for variable substitution within
text files. Used by the ``agentbase init`` command to generate
project files with user-specified names, API keys, and configuration.

Features:
- ``{{variable}}`` substitution with default values
- ``{{#if variable}}...{{/if}}`` conditional blocks
- ``{{#each list}}...{{/each}}`` iteration blocks
- Template registry for user-customizable templates
- File-level template loading from disk
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Compiled regex patterns
_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_IF_RE = re.compile(r"\{\{#if\s+(\w+)\s*\}\}(.*?)\{\{/if\}\}", re.DOTALL)
_EACH_RE = re.compile(r"\{\{#each\s+(\w+)\s*\}\}(.*?)\{\{/each\}\}", re.DOTALL)


def render(template: str, variables: dict[str, Any]) -> str:
    """Render a template string with variable substitution.

    Supported syntax:
    - ``{{variable}}`` — replaced with the variable's string value
    - ``{{#if variable}}...{{/if}}`` — included only if variable is truthy
    - ``{{#each list}}...{{/each}}`` — repeated for each item in list

    Unknown variables are replaced with empty strings.
    """
    result = template

    # Process {{#each list}}...{{/each}} blocks first (innermost to outermost)
    while True:
        match = _EACH_RE.search(result)
        if not match:
            break
        list_name = match.group(1)
        body = match.group(2)
        items = variables.get(list_name, [])
        if not isinstance(items, (list, tuple)):
            items = []
        rendered_parts: list[str] = []
        for item in items:
            if isinstance(item, dict):
                # Render each item with its own variables merged
                item_vars = {**variables, **item}
                rendered_parts.append(_render_vars(body, item_vars))
            else:
                # Scalar item — use {{this}} as the variable
                item_vars = {**variables, "this": str(item)}
                rendered_parts.append(_render_vars(body, item_vars))
        result = result[: match.start()] + "".join(rendered_parts) + result[match.end():]

    # Process {{#if variable}}...{{/if}} blocks
    while True:
        match = _IF_RE.search(result)
        if not match:
            break
        var_name = match.group(1)
        body = match.group(2)
        value = variables.get(var_name)
        if value:
            rendered = _render_vars(body, variables)
        else:
            rendered = ""
        result = result[: match.start()] + rendered + result[match.end():]

    # Finally, process remaining {{variable}} substitutions
    result = _render_vars(result, variables)

    return result


def _render_vars(text: str, variables: dict[str, Any]) -> str:
    """Replace all {{variable}} occurrences in text."""
    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        value = variables.get(var_name)
        if value is None:
            return ""
        return str(value)

    return _VAR_RE.sub(replacer, text)


def render_file(template_path: Path, output_path: Path, variables: dict[str, Any]) -> Path:
    """Render a template file to an output path.

    Args:
        template_path: Path to the template file.
        output_path: Where to write the rendered file.
        variables: Variable substitutions.

    Returns:
        The output path.
    """
    content = template_path.read_text(encoding="utf-8")
    rendered = render(content, variables)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    return output_path


def render_tree(
    template_dir: Path,
    output_dir: Path,
    variables: dict[str, Any],
    *,
    overwrite: bool = False,
    skip_existing: bool = True,
) -> list[tuple[str, str]]:
    """Render an entire directory of templates.

    Walks ``template_dir`` recursively, renders each file, and writes
    to the corresponding path under ``output_dir``.

    Args:
        template_dir: Directory containing template files.
        output_dir: Destination directory.
        variables: Variable substitutions.
        overwrite: If True, overwrite existing files.
        skip_existing: If True (and overwrite=False), skip existing files.

    Returns:
        List of (action, relative_path) tuples where action is
        "create", "skip", or "overwrite".
    """
    results: list[tuple[str, str]] = []
    template_dir = Path(template_dir)
    output_dir = Path(output_dir)

    if not template_dir.exists():
        return results

    for template_file in template_dir.rglob("*"):
        if template_file.is_dir():
            continue

        # Calculate relative path
        rel_path = template_file.relative_to(template_dir)
        # Render the filename too (for {{name}}.py etc.)
        rel_path_str = render(str(rel_path), variables)
        output_path = output_dir / rel_path_str

        if output_path.exists():
            if overwrite:
                render_file(template_file, output_path, variables)
                results.append(("overwrite", rel_path_str))
            elif skip_existing:
                results.append(("skip", rel_path_str))
            else:
                raise FileExistsError(f"File already exists: {output_path}")
        else:
            render_file(template_file, output_path, variables)
            results.append(("create", rel_path_str))

    return results


@dataclass
class TemplateManifest:
    """Manifest for a template package.

    A template package is a directory containing template files plus
    a ``template.yaml`` manifest with metadata.
    """
    name: str = ""
    description: str = ""
    version: str = "1.0.0"
    author: str = ""
    variables: dict[str, str] = field(default_factory=dict)
    # Map of template file → output file (relative paths)
    files: dict[str, str] = field(default_factory=dict)


class TemplateRegistry:
    """Registry for named template packages.

    Templates can be:
    - Built-in (shipped with agentbase)
    - User-provided (loaded from disk)
    """

    def __init__(self) -> None:
        self._templates: dict[str, tuple[Path, TemplateManifest]] = {}

    def register(self, name: str, template_dir: Path, manifest: TemplateManifest | None = None) -> None:
        """Register a template package.

        Args:
            name: Template name.
            template_dir: Directory containing template files.
            manifest: Optional manifest with metadata and variable defaults.
        """
        self._templates[name] = (Path(template_dir), manifest or TemplateManifest())

    def get(self, name: str) -> tuple[Path, TemplateManifest] | None:
        """Get a registered template by name."""
        return self._templates.get(name)

    def names(self) -> list[str]:
        """Return all registered template names."""
        return sorted(self._templates.keys())

    def has(self, name: str) -> bool:
        """Check if a template is registered."""
        return name in self._templates

    def render_to(
        self,
        name: str,
        output_dir: Path,
        variables: dict[str, Any] | None = None,
        *,
        overwrite: bool = False,
    ) -> list[tuple[str, str]]:
        """Render a registered template to an output directory.

        Args:
            name: Template name.
            output_dir: Destination directory.
            variables: Variable substitutions (merged with manifest defaults).
            overwrite: If True, overwrite existing files.

        Returns:
            List of (action, relative_path) tuples.
        """
        entry = self._templates.get(name)
        if entry is None:
            raise KeyError(f"Unknown template: {name}. Available: {', '.join(self.names())}")

        template_dir, manifest = entry
        # Merge manifest defaults with user variables
        merged_vars = {**manifest.variables, **(variables or {})}
        return render_tree(
            template_dir,
            output_dir,
            merged_vars,
            overwrite=overwrite,
            skip_existing=not overwrite,
        )


# Global registry
template_registry = TemplateRegistry()


def get_builtin_template_dir() -> Path:
    """Return the directory containing built-in templates."""
    return Path(__file__).parent.parent / "templates"
