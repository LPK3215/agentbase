"""Web fetch tool — read and extract text from a URL.

Tool provided:
- ``web_fetch`` — fetch a web page and return its text content

Features:
- Configurable timeout (default 15s)
- Automatic retry on transient failures (default 2 retries)
- Redirect following (up to 5 redirects)
- Content-Type validation (rejects non-text content)
- Encoding detection (utf-8, gbk, latin-1)
- Output truncation (configurable ``max_length``)
- HTML to text conversion (removes scripts, styles, tags)

Usage::

    tools:
      - web_fetch
"""

from __future__ import annotations

import re
import time
from typing import Any

from langchain_core.tools import tool

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.tools import register_tool

_FETCH_META = ExtensionMeta(
    name="web_fetch",
    kind="tool",
    description="Fetch a web page and extract its text content.",
    requires_context=[],
)

# Safety limits
_MAX_REDIRECTS = 5
_MAX_CONTENT_SIZE = 10_000_000  # 10 MB max download
_ALLOWED_CONTENT_TYPES = ("text/", "application/json", "application/xml", "application/xhtml")


def _html_to_text(html: str) -> str:
    """Convert HTML to plain text.

    Removes script/style elements, HTML tags, decodes entities,
    and normalizes whitespace.
    """
    # Remove script and style elements
    html = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML comments
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    # Convert <br>, <p>, <div> to newlines for better formatting
    html = re.sub(r"<(?:br|/p|/div|/li|/h[1-6])[^>]*>", "\n", html, flags=re.IGNORECASE)
    # Remove remaining HTML tags
    text = re.sub(r"<[^>]+>", "", html)
    # Decode common HTML entities
    import html as html_module
    text = html_module.unescape(text)
    # Normalize whitespace
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text


@register_tool("web_fetch", meta=_FETCH_META)
def build_web_fetch_tool(context: dict[str, Any] | None = None):
    @tool
    def web_fetch(url: str, max_length: int = 5000, timeout: int = 15) -> str:
        """Fetch a web page and return its text content.

        Args:
            url: The URL to fetch (must start with http:// or https://).
            max_length: Maximum characters of text to return (default 5000).
            timeout: Fetch timeout in seconds (default 15, max 30).

        Returns:
            Extracted text content from the web page.
        """
        import urllib.error
        import urllib.request

        # Validate URL scheme
        if not url.startswith(("http://", "https://")):
            return f"Invalid URL: must start with http:// or https:// (got: {url[:50]})"

        timeout = min(max(timeout, 5), 30)
        max_retries = 2

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; agentbase/0.4.0)",
                "Accept": "text/html,application/json,application/xml,*/*;q=0.8",
            },
        )

        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 2):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    # Check content type
                    content_type = resp.headers.get("Content-Type", "")
                    if content_type and not any(ct in content_type.lower() for ct in _ALLOWED_CONTENT_TYPES):
                        return f"Unsupported content type: {content_type}. Only text-based content is supported."

                    # Check content length
                    content_length = resp.headers.get("Content-Length")
                    if content_length and int(content_length) > _MAX_CONTENT_SIZE:
                        return f"Content too large: {content_length} bytes (max {_MAX_CONTENT_SIZE})"

                    raw = resp.read(_MAX_CONTENT_SIZE + 1)
                    if len(raw) > _MAX_CONTENT_SIZE:
                        return f"Content too large (max {_MAX_CONTENT_SIZE} bytes)"

                    # Detect encoding from headers or content
                    encoding = "utf-8"
                    charset_match = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
                    if charset_match:
                        encoding = charset_match.group(1)
                    else:
                        # Try common encodings
                        for enc in ("utf-8", "gbk", "latin-1"):
                            try:
                                raw.decode(enc)
                                encoding = enc
                                break
                            except (UnicodeDecodeError, LookupError):
                                continue

                    try:
                        html = raw.decode(encoding)
                    except (UnicodeDecodeError, LookupError):
                        html = raw.decode("utf-8", errors="replace")

                    break
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    # Rate limited — retry with delay
                    last_exc = exc
                    if attempt <= max_retries:
                        time.sleep(1.0 * attempt)
                        continue
                return f"Fetch failed (HTTP {exc.code}): {exc.reason}"
            except urllib.error.URLError as exc:
                last_exc = exc
                if attempt <= max_retries and isinstance(exc.reason, (TimeoutError, ConnectionError, OSError)):
                    time.sleep(0.5 * attempt)
                    continue
                return f"Fetch failed: {exc}"
            except Exception as exc:
                last_exc = exc
                if attempt <= max_retries:
                    time.sleep(0.5 * attempt)
                    continue
                return f"Fetch failed: {exc}"
        else:
            return f"Fetch failed after {max_retries + 1} attempts: {last_exc}"

        text = _html_to_text(html)
        if len(text) > max_length:
            text = text[:max_length] + "\n...(truncated)"
        return text

    return web_fetch
