"""Unit tests for the web_search tool."""
from __future__ import annotations

from agentbase.core.search import SearchResult


class TestWebSearchTool:
    def test_build_and_call(self):
        """Test that web_search tool can be built and called."""
        from agentbase.extensions.tools.search_ops import build_web_search_tool

        class FakeProvider:
            def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
                return [
                    SearchResult(title=f"Result for {query}", url=f"https://example.com/{i}", snippet="Snippet", source="fake")
                    for i in range(3)
                ]

        tool_fn = build_web_search_tool(context={"search_provider": FakeProvider()})
        result = tool_fn.invoke({"query": "test query", "max_results": 3})
        assert "Result for test query" in result
        assert "https://example.com" in result

    def test_no_results(self):
        from agentbase.extensions.tools.search_ops import build_web_search_tool

        class EmptyProvider:
            def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
                return []

        tool_fn = build_web_search_tool(context={"search_provider": EmptyProvider()})
        result = tool_fn.invoke({"query": "nothing", "max_results": 5})
        assert "no results" in result

    def test_missing_provider_returns_error(self):
        from agentbase.extensions.tools.search_ops import build_web_search_tool

        tool_fn = build_web_search_tool(context=None)
        result = tool_fn.invoke({"query": "test", "max_results": 5})
        assert "not configured" in result.lower()
