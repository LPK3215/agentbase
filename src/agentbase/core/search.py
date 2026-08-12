"""Web search provider registry — pluggable internet search for agents.

The scaffold provides the **interface** and a **zero-config default**
(DuckDuckGo HTML search, no API key needed).  Users register real search
providers (Tavily, SerpAPI, Google CSE, Bing, etc.) using whichever
library they prefer.

Usage::

    from agentbase.core.search import register_search_provider

    @register_search_provider("tavily")
    class TavilySearch:
        def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
            from tavily import TavilyClient
            client = TavilyClient(api_key="...")
            resp = client.search(query, max_results=max_results)
            return [SearchResult(title=r["title"], url=r["url"], snippet=r["content"])
                    for r in resp["results"]]

Interface::

    class SearchProvider(Protocol):
        def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]: ...
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from urllib.parse import quote_plus

from agentbase.runtime.errors import RegistryError


@dataclass
class SearchResult:
    """A single web search result."""

    title: str
    url: str
    snippet: str = ""
    source: str = ""  # which provider returned this

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
        }


@runtime_checkable
class SearchProvider(Protocol):
    """Protocol for web search providers."""

    def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        """Search the web and return results."""
        ...


# ---------------------------------------------------------------------------
# Default provider: DuckDuckGo HTML (zero config, no API key)
# ---------------------------------------------------------------------------

class DuckDuckGoSearch:
    """DuckDuckGo HTML search — no API key required.

    Uses the lightweight HTML endpoint and parses results with regex.
    For production use, consider registering a Tavily/SerpAPI provider.

    Features:
    - Configurable timeout (default 10s)
    - Automatic retry on transient failures (default 2 retries)
    - Result deduplication by URL
    - Rate limiting via sequential requests (no concurrent burst)
    """

    def __init__(self, *, timeout: int = 10, max_retries: int = 2) -> None:
        self._timeout = min(max(timeout, 5), 30)
        self._max_retries = max_retries

    def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        import urllib.request

        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; agentbase/0.4.0)"},
        )

        html = None
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 2):
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    html = resp.read().decode("utf-8", errors="replace")
                break
            except Exception as exc:
                last_exc = exc
                if attempt <= self._max_retries and isinstance(exc, (TimeoutError, ConnectionError, OSError)):
                    import time
                    time.sleep(0.5 * attempt)
                    continue
                return [SearchResult(
                    title="Search failed",
                    url="",
                    snippet=f"Web search error: {exc}",
                    source="duckduckgo",
                )]

        if html is None:
            return [SearchResult(
                title="Search failed",
                url="",
                snippet=f"Web search failed after {self._max_retries + 1} attempts: {last_exc}",
                source="duckduckgo",
            )]

        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        # Parse result links and snippets from DDG HTML
        link_pattern = re.compile(
            r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        snippet_pattern = re.compile(
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            re.DOTALL,
        )

        links = link_pattern.findall(html)
        snippets = snippet_pattern.findall(html)

        for i, (raw_url, raw_title) in enumerate(links):
            if len(results) >= max_results:
                break
            # Clean HTML tags from title
            title = re.sub(r"<[^>]+>", "", raw_title).strip()
            # DDG wraps URLs in a redirect; extract actual URL
            if "uddg=" in raw_url:
                from urllib.parse import parse_qs, urlparse
                parsed = urlparse(raw_url)
                params = parse_qs(parsed.query)
                actual_url = params.get("uddg", [raw_url])[0]
            else:
                actual_url = raw_url

            # Deduplicate by URL
            if actual_url in seen_urls:
                continue
            seen_urls.add(actual_url)

            snippet = ""
            if i < len(snippets):
                snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()

            results.append(SearchResult(
                title=title,
                url=actual_url,
                snippet=snippet,
                source="duckduckgo",
            ))

        return results


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class SearchRegistry:
    """Thread-safe registry mapping names to search provider instances."""

    def __init__(self) -> None:
        self._providers: dict[str, SearchProvider] = {}
        self._lock = threading.RLock()

    def register(self, name: str, provider: SearchProvider, *, override: bool = False) -> None:
        key = name.strip().lower()
        if not key:
            raise RegistryError("Cannot register empty search provider name")
        with self._lock:
            if key in self._providers and not override:
                raise RegistryError(f"Search provider already registered: {key}")
            self._providers[key] = provider

    def get(self, name: str) -> SearchProvider:
        key = name.strip().lower()
        with self._lock:
            if key not in self._providers:
                available = ", ".join(sorted(self._providers)) or "<empty>"
                raise RegistryError(f"Unknown search provider: {key}. Available: {available}")
            return self._providers[key]

    def has(self, name: str) -> bool:
        with self._lock:
            return name.strip().lower() in self._providers

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._providers.keys())

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._providers)

    def unregister(self, name: str) -> bool:
        """Remove a provider. Returns True if removed."""
        key = name.strip().lower()
        with self._lock:
            if key not in self._providers:
                return False
            self._providers.pop(key, None)
            return True


# Global singleton
search_registry = SearchRegistry()

# Register default with configurable timeout/retries
search_registry.register("duckduckgo", DuckDuckGoSearch(timeout=10, max_retries=2))


def register_search_provider(name: str, *, override: bool = False):
    """Decorator: register a web search provider class.

    Usage::

        @register_search_provider("tavily")
        class TavilySearch:
            def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
                ...
    """
    def _wrap(cls):
        instance = cls()
        search_registry.register(name, instance, override=override)
        return cls

    return _wrap


# ---------------------------------------------------------------------------
# Tavily search provider (requires tavily-python package + API key)
# ---------------------------------------------------------------------------

class TavilySearch:
    """Tavily AI search provider.

    Requires the ``tavily-python`` package and ``TAVILY_API_KEY``
    environment variable.

    Usage::

        # In config:
        web_search:
          provider: tavily
          options:
            api_key: tvly-xxx  # or set TAVILY_API_KEY env var

        # Or programmatically:
        from agentbase.core.search import TavilySearch
        provider = TavilySearch(api_key="tvly-xxx")
    """

    def __init__(self, api_key: str | None = None) -> None:
        import os
        self._api_key = api_key or os.environ.get("TAVILY_API_KEY", "")
        self._client = None

    def _get_client(self):
        if self._client is None:
            if not self._api_key:
                raise RuntimeError(
                    "Tavily search requires TAVILY_API_KEY. "
                    "Set it as an environment variable or pass api_key=."
                )
            try:
                from tavily import TavilyClient
            except ImportError as exc:
                raise ImportError(
                    "Tavily search requires the tavily-python package. "
                    "Install with: pip install tavily-python"
                ) from exc
            self._client = TavilyClient(api_key=self._api_key)
        return self._client

    def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        """Search the web using Tavily AI API."""
        client = self._get_client()
        resp = client.search(query, max_results=max_results)
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
                source="tavily",
            )
            for r in resp.get("results", [])
        ]


# Register Tavily provider if the package is available
try:
    import tavily  # noqa: F401
    search_registry.register("tavily", TavilySearch(), override=True)
except ImportError:
    pass
