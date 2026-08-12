"""Document parser registry — pluggable file-to-text extraction.

The scaffold provides the **framework** for document parsing, not the
implementations.  Default parsers for plain text and Markdown are included.
Users register their own parsers for PDF, Word, HTML, etc. using whichever
library they prefer (pymupdf, pdfplumber, python-docx, beautifulsoup…).

Usage::

    from agentbase.core.parsers import register_parser, TextParser

    # Built-in: txt and md already work
    # User registers a PDF parser:
    @register_parser("pdf")
    class MyPdfParser(DocumentParser):
        extensions = [".pdf"]
        def parse(self, path: Path) -> str:
            import pymupdf
            doc = pymupdf.open(str(path))
            return "\\n".join(page.get_text() for page in doc)

Interface::

    class DocumentParser(Protocol):
        extensions: list[str]       # e.g. [".pdf", ".PDF"]
        def parse(self, path: Path) -> str: ...
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Protocol, runtime_checkable

from agentbase.runtime.errors import RegistryError


@runtime_checkable
class DocumentParser(Protocol):
    """Protocol for document parsers.

    Implementations must define ``extensions`` (list of file suffixes they
    handle, case-insensitive) and a ``parse(path) -> str`` method.
    """

    extensions: list[str]

    def parse(self, path: Path) -> str:
        """Extract text content from a file.  Raises on parse failure."""
        ...


# ---------------------------------------------------------------------------
# Default parsers
# ---------------------------------------------------------------------------

class TextParser:
    """Parser for plain text files (.txt, .log, .csv, .json, .yaml, etc.)."""

    extensions = [".txt", ".log", ".csv", ".json", ".yaml", ".yml", ".xml", ".ini", ".cfg", ".toml", ".py", ".js", ".ts", ".sql", ".sh", ".bat"]

    def parse(self, path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace")


class MarkdownParser:
    """Parser for Markdown files — returns raw markdown text."""

    extensions = [".md", ".markdown", ".rst"]

    def parse(self, path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class ParserRegistry:
    """Thread-safe registry mapping file extensions to parser instances.

    Lookup order:
    1. Exact extension match (e.g. ``.pdf`` → PdfParser)
    2. Fallback to ``TextParser`` for unknown extensions
    """

    def __init__(self) -> None:
        self._by_ext: dict[str, DocumentParser] = {}
        self._default = TextParser()
        self._lock = threading.RLock()

    def register(self, parser: DocumentParser, *, override: bool = False) -> None:
        seen: set[str] = set()
        with self._lock:
            for ext in parser.extensions:
                key = ext.lower()
                if key in seen:
                    continue
                seen.add(key)
                if key in self._by_ext and not override:
                    raise RegistryError(f"Parser already registered for extension: {key}")
                self._by_ext[key] = parser

    def get(self, extension: str) -> DocumentParser:
        """Get parser for a file extension.  Falls back to TextParser."""
        with self._lock:
            return self._by_ext.get(extension.lower(), self._default)

    def has(self, extension: str) -> bool:
        """Check if a parser is registered for the given extension."""
        with self._lock:
            return extension.lower() in self._by_ext

    def get_for_path(self, path: Path) -> DocumentParser:
        """Get parser for a file path based on its suffix."""
        return self.get(path.suffix)

    def supported_extensions(self) -> list[str]:
        with self._lock:
            return sorted(self._by_ext.keys())

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._by_ext)

    def unregister(self, extension: str) -> bool:
        """Remove a parser for a specific extension. Returns True if removed."""
        key = extension.lower()
        with self._lock:
            if key not in self._by_ext:
                return False
            self._by_ext.pop(key, None)
            return True


# Global singleton
parser_registry = ParserRegistry()

# Register defaults
parser_registry.register(TextParser())
parser_registry.register(MarkdownParser())


def register_parser(*extensions: str, override: bool = False):
    """Decorator: register a parser class for given extensions.

    Usage::

        @register_parser(".pdf", ".PDF")
        class PdfParser:
            extensions = [".pdf"]
            def parse(self, path: Path) -> str: ...
    """
    def _wrap(cls):
        instance = cls()
        # Merge decorator extensions with class-level extensions
        all_exts = list(extensions) + list(getattr(instance, "extensions", []))
        if all_exts:
            instance.extensions = all_exts
        parser_registry.register(instance, override=override)
        return cls

    return _wrap
