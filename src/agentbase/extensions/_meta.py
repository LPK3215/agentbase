"""Extension metadata — describes a registered extension's identity and dependencies.

Used by the registry system to track:
- Name and kind (tool/middleware/subagent)
- Human-readable description
- Context requirements (what must be provided by the agent factory)
- Default enabled state
- Version for compatibility checking
- Dependencies on other extensions (ensures load order)
- Optional tags for filtering and display
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExtensionMeta:
    """Metadata describing a registered extension.

    Attributes:
        name: Unique extension identifier.
        kind: Extension kind — ``"tool"``, ``"middleware"``, ``"subagent"``.
        description: Human-readable description for CLI and docs.
        requires_context: Context keys that must be provided by the
            agent factory (e.g. ``["memory_manager", "knowledge_base"]``).
        default_enabled: Whether the extension is enabled by default.
        version: Semantic version string for compatibility checking.
        dependencies: Names of other extensions that must be loaded
            before this one. The bootstrap process ensures dependency
            order.
        tags: Optional tags for filtering (e.g. ``["file", "io"]``).
        author: Optional author name.
        homepage: Optional URL for documentation.
    """

    name: str
    kind: str
    description: str
    requires_context: list[str] = field(default_factory=list)
    default_enabled: bool = False
    version: str = "1.0.0"
    dependencies: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    author: str = ""
    homepage: str = ""

    def to_dict(self) -> dict[str, object]:
        """Serialise metadata for display."""
        return {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "requires_context": list(self.requires_context),
            "default_enabled": self.default_enabled,
            "version": self.version,
            "dependencies": list(self.dependencies),
            "tags": list(self.tags),
            "author": self.author,
            "homepage": self.homepage,
        }
