"""Web search tool — expose web search to agents.

Tool provided:
- ``web_search`` — search the web using the configured search provider
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.tools import register_tool

_SEARCH_META = ExtensionMeta(
    name="web_search",
    kind="tool",
    description="Search the web and return results.",
    requires_context=["search_provider"],
)


def _get_provider(context: dict[str, Any] | None):
    provider = (context or {}).get("search_provider")
    if provider is None:
        raise RuntimeError("search_provider not available in context")
    return provider


@register_tool("web_search", meta=_SEARCH_META)
def build_web_search_tool(context: dict[str, Any] | None = None):
    provider = (context or {}).get("search_provider")

    @tool
    def web_search(query: str, max_results: int = 5) -> str:
        """Search the web for information.

        Args:
            query: The search query string.
            max_results: Maximum number of results to return (default 5).

        Returns:
            Formatted search results with titles, URLs, and snippets.
        """
        if provider is None:
            return "Web search is not configured. Set web_search.provider in config."
        results = provider.search(query, max_results=max_results)
        if not results:
            return f"<no results for '{query}'>"

        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.title}")
            if r.url:
                lines.append(f"   URL: {r.url}")
            if r.snippet:
                lines.append(f"   {r.snippet[:200]}")
            lines.append("")
        return "\n".join(lines)

    return web_search
