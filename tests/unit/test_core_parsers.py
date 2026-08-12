"""Unit tests for the document parser registry."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentbase.core.parsers import (
    DocumentParser,
    MarkdownParser,
    ParserRegistry,
    TextParser,
    parser_registry,
    register_parser,
)


class TestDefaultParsers:
    def test_text_parser(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("Hello, world!", encoding="utf-8")
        parser = TextParser()
        assert parser.parse(f) == "Hello, world!"

    def test_markdown_parser(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("# Title\n\nContent", encoding="utf-8")
        parser = MarkdownParser()
        assert "# Title" in parser.parse(f)

    def test_text_parser_extensions(self):
        parser = TextParser()
        assert ".txt" in parser.extensions
        assert ".py" in parser.extensions

    def test_markdown_parser_extensions(self):
        parser = MarkdownParser()
        assert ".md" in parser.extensions


class TestParserRegistry:
    def test_get_for_txt(self):
        reg = ParserRegistry()
        reg.register(TextParser())
        parser = reg.get(".txt")
        assert isinstance(parser, TextParser)

    def test_get_for_md(self):
        reg = ParserRegistry()
        reg.register(MarkdownParser())
        parser = reg.get(".md")
        assert isinstance(parser, MarkdownParser)

    def test_fallback_to_text(self):
        reg = ParserRegistry()
        reg.register(TextParser())
        # Unknown extension falls back to TextParser
        parser = reg.get(".unknown")
        assert isinstance(parser, TextParser)

    def test_get_for_path(self, tmp_path):
        reg = ParserRegistry()
        reg.register(TextParser())
        reg.register(MarkdownParser())
        path = tmp_path / "doc.md"
        parser = reg.get_for_path(path)
        assert isinstance(parser, MarkdownParser)

    def test_supported_extensions(self):
        reg = ParserRegistry()
        reg.register(TextParser())
        reg.register(MarkdownParser())
        exts = reg.supported_extensions()
        assert ".txt" in exts
        assert ".md" in exts

    def test_register_duplicate_raises(self):
        reg = ParserRegistry()
        reg.register(TextParser())
        with pytest.raises(Exception, match="already registered"):
            reg.register(TextParser())

    def test_register_override(self):
        reg = ParserRegistry()
        reg.register(TextParser())
        reg.register(TextParser(), override=True)
        assert ".txt" in reg.supported_extensions()


class TestRegisterParserDecorator:
    def test_decorator(self, tmp_path):
        @register_parser(".xyz", override=True)
        class XyzParser:
            extensions = [".xyz"]

            def parse(self, path: Path) -> str:
                return "xyz content"

        f = tmp_path / "test.xyz"
        f.write_text("dummy", encoding="utf-8")
        parser = parser_registry.get(".xyz")
        assert parser.parse(f) == "xyz content"

    def test_decorator_multiple_extensions(self):
        @register_parser(".abc", ".ABC", override=True)
        class AbcParser:
            extensions = [".abc"]

            def parse(self, path: Path) -> str:
                return "abc"

        assert ".abc" in parser_registry.supported_extensions()
        assert ".abc" in parser_registry.supported_extensions()


class TestProtocol:
    def test_text_parser_is_protocol(self):
        assert isinstance(TextParser(), DocumentParser)

    def test_markdown_parser_is_protocol(self):
        assert isinstance(MarkdownParser(), DocumentParser)
