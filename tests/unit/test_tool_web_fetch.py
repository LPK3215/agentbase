"""Unit tests for web_fetch tool — _html_to_text and web_fetch function.

Tests cover:
- _html_to_text: script/style removal, tag removal, entity decoding, whitespace
- web_fetch: invalid URL, success path, HTTP errors, content type rejection,
  content length limits, encoding detection, retry logic, truncation
"""
from __future__ import annotations

import io
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentbase.extensions.tools.web_fetch import _html_to_text, build_web_fetch_tool


# ---------------------------------------------------------------------------
# _html_to_text
# ---------------------------------------------------------------------------


class TestHtmlToText:
    def test_removes_script_and_style(self):
        html = "<script>alert('x')</script><style>body{color:red}</style><p>Hello</p>"
        result = _html_to_text(html)
        assert "alert" not in result
        assert "color:red" not in result
        assert "Hello" in result

    def test_removes_noscript(self):
        html = "<noscript>fallback</noscript><p>Main content</p>"
        result = _html_to_text(html)
        assert "fallback" not in result
        assert "Main content" in result

    def test_removes_html_comments(self):
        html = "<!-- comment --><p>Text</p>"
        result = _html_to_text(html)
        assert "comment" not in result
        assert "Text" in result

    def test_converts_br_to_newline(self):
        html = "line1<br>line2</p><p>line3"
        result = _html_to_text(html)
        assert "\n" in result

    def test_removes_all_tags(self):
        html = "<div><span><b>bold</b></span></div>"
        result = _html_to_text(html)
        assert "bold" in result
        assert "<" not in result

    def test_decodes_html_entities(self):
        html = "&amp;&lt;&gt;&quot;&#39;"
        result = _html_to_text(html)
        assert result == "&<>\"'"

    def test_normalizes_whitespace(self):
        html = "para1\n\n\n\npara2"
        result = _html_to_text(html)
        # Multiple newlines should be collapsed to at most two
        assert "\n\n\n" not in result

    def test_empty_string(self):
        assert _html_to_text("") == ""

    def test_plain_text_passthrough(self):
        assert _html_to_text("hello world") == "hello world"


# ---------------------------------------------------------------------------
# build_web_fetch_tool — factory
# ---------------------------------------------------------------------------


class TestBuildWebFetchTool:
    def test_returns_callable(self):
        tool = build_web_fetch_tool(None)
        assert tool is not None
        assert hasattr(tool, "invoke") or callable(tool)

    def test_returns_tool_with_context(self):
        tool = build_web_fetch_tool({"agent_config": MagicMock()})
        assert tool is not None


# ---------------------------------------------------------------------------
# web_fetch — invalid URL
# ---------------------------------------------------------------------------


class TestWebFetchInvalidUrl:
    def _get_tool(self):
        return build_web_fetch_tool(None)

    def test_invalid_scheme_ftp(self):
        tool = self._get_tool()
        result = tool.invoke({"url": "ftp://example.com"})
        assert "Invalid URL" in result

    def test_invalid_scheme_file(self):
        tool = self._get_tool()
        result = tool.invoke({"url": "file:///etc/passwd"})
        assert "Invalid URL" in result

    def test_no_scheme(self):
        tool = self._get_tool()
        result = tool.invoke({"url": "example.com"})
        assert "Invalid URL" in result


# ---------------------------------------------------------------------------
# web_fetch — success path
# ---------------------------------------------------------------------------


class _FakeResponse:
    """A fake HTTP response for testing."""

    def __init__(self, body=b"<html><body><p>Hello World</p></body></html>",
                 content_type="text/html; charset=utf-8",
                 content_length=None):
        self._body = body
        self.headers = {
            "Content-Type": content_type,
        }
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self._read_data = body

    def read(self, size=-1):
        if size == -1:
            data = self._read_data
            self._read_data = b""
            return data
        data = self._read_data[:size]
        self._read_data = self._read_data[size:]
        return data

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestWebFetchSuccess:
    def _get_tool(self):
        return build_web_fetch_tool(None)

    def test_success_html_content(self):
        tool = self._get_tool()
        fake_resp = _FakeResponse(body=b"<html><body><p>Hello World</p></body></html>")
        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = tool.invoke({"url": "https://example.com"})
            assert "Hello World" in result

    def test_success_json_content(self):
        tool = self._get_tool()
        fake_resp = _FakeResponse(
            body=b'{"key": "value"}',
            content_type="application/json; charset=utf-8",
        )
        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = tool.invoke({"url": "https://api.example.com"})
            assert "value" in result

    def test_success_xml_content(self):
        tool = self._get_tool()
        fake_resp = _FakeResponse(
            body=b"<root><item>text</item></root>",
            content_type="application/xml",
        )
        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = tool.invoke({"url": "https://example.com"})
            assert "text" in result

    def test_truncation(self):
        tool = self._get_tool()
        long_text = "A" * 6000
        body = f"<html><body><p>{long_text}</p></body></html>".encode()
        fake_resp = _FakeResponse(body=body)
        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = tool.invoke({"url": "https://example.com", "max_length": 100})
            assert "truncated" in result
            assert len(result) < 6000

    def test_encoding_from_charset(self):
        tool = self._get_tool()
        # Use gbk-encoded body
        body = "<html><body><p>你好世界</p></body></html>".encode("gbk")
        fake_resp = _FakeResponse(body=body, content_type="text/html; charset=gbk")
        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = tool.invoke({"url": "https://example.com"})
            assert "你好世界" in result

    def test_encoding_fallback_latin1(self):
        tool = self._get_tool()
        # Use latin-1 encoded body that's not valid utf-8
        body = b"<html><body><p>\xe9\xe0\xe8</p></body></html>"
        fake_resp = _FakeResponse(body=body, content_type="text/html")
        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = tool.invoke({"url": "https://example.com"})
            # Should decode without error
            assert len(result) > 0

    def test_encoding_utf8_errors_replace(self):
        tool = self._get_tool()
        # Invalid encoding that can't be decoded with charset or fallbacks
        body = b"<html><body><p>\xff\xfe\x00\x01</p></body></html>"
        fake_resp = _FakeResponse(body=body, content_type="text/html; charset=invalid-encoding")
        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = tool.invoke({"url": "https://example.com"})
            # Should fall back to utf-8 with errors=replace
            assert len(result) > 0


# ---------------------------------------------------------------------------
# web_fetch — error paths
# ---------------------------------------------------------------------------


class TestWebFetchErrors:
    def _get_tool(self):
        return build_web_fetch_tool(None)

    def test_unsupported_content_type(self):
        import urllib.error

        tool = self._get_tool()
        fake_resp = _FakeResponse(
            body=b"binary data",
            content_type="application/octet-stream",
        )
        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = tool.invoke({"url": "https://example.com"})
            assert "Unsupported content type" in result

    def test_content_too_large_from_header(self):
        tool = self._get_tool()
        fake_resp = _FakeResponse(
            body=b"data",
            content_type="text/html",
            content_length=20_000_000,
        )
        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = tool.invoke({"url": "https://example.com"})
            assert "Content too large" in result

    def test_content_too_large_from_body(self):
        tool = self._get_tool()
        # Body larger than _MAX_CONTENT_SIZE
        large_body = b"x" * (10_000_001)
        fake_resp = _FakeResponse(body=large_body, content_type="text/html")
        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = tool.invoke({"url": "https://example.com"})
            assert "Content too large" in result

    def test_http_error_404(self):
        import urllib.error

        tool = self._get_tool()
        exc = urllib.error.HTTPError(
            url="https://example.com",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=exc):
            result = tool.invoke({"url": "https://example.com"})
            assert "404" in result

    def test_http_error_500(self):
        import urllib.error

        tool = self._get_tool()
        exc = urllib.error.HTTPError(
            url="https://example.com",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=exc):
            result = tool.invoke({"url": "https://example.com"})
            assert "500" in result

    def test_http_error_429_retries(self):
        import urllib.error

        tool = self._get_tool()
        exc = urllib.error.HTTPError(
            url="https://example.com",
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=None,
        )
        # First two calls raise 429, third succeeds
        fake_resp = _FakeResponse(body=b"<html><body><p>OK</p></body></html>")
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise exc
            return fake_resp

        with patch("urllib.request.urlopen", side_effect=side_effect):
            with patch("time.sleep"):
                result = tool.invoke({"url": "https://example.com"})
                assert "OK" in result

    def test_http_error_429_exhausted(self):
        import urllib.error

        tool = self._get_tool()
        exc = urllib.error.HTTPError(
            url="https://example.com",
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=exc):
            with patch("time.sleep"):
                result = tool.invoke({"url": "https://example.com"})
                assert "429" in result or "Too Many" in result

    def test_url_error_timeout_retry_success(self):
        import urllib.error

        tool = self._get_tool()
        timeout_exc = urllib.error.URLError(reason=TimeoutError("timed out"))
        fake_resp = _FakeResponse(body=b"<html><body><p>OK</p></body></html>")
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise timeout_exc
            return fake_resp

        with patch("urllib.request.urlopen", side_effect=side_effect):
            with patch("time.sleep"):
                result = tool.invoke({"url": "https://example.com"})
                assert "OK" in result

    def test_url_error_no_retry_for_generic_reason(self):
        import urllib.error

        tool = self._get_tool()
        exc = urllib.error.URLError(reason=ValueError("bad url"))
        with patch("urllib.request.urlopen", side_effect=exc):
            with patch("time.sleep"):
                result = tool.invoke({"url": "https://example.com"})
                assert "Fetch failed" in result

    def test_url_error_exhausted_retries(self):
        import urllib.error

        tool = self._get_tool()
        exc = urllib.error.URLError(reason=ConnectionError("connection refused"))
        with patch("urllib.request.urlopen", side_effect=exc):
            with patch("time.sleep"):
                result = tool.invoke({"url": "https://example.com"})
                assert "Fetch failed" in result

    def test_generic_exception_retry_success(self):
        tool = self._get_tool()
        fake_resp = _FakeResponse(body=b"<html><body><p>OK</p></body></html>")
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("transient")
            return fake_resp

        with patch("urllib.request.urlopen", side_effect=side_effect):
            with patch("time.sleep"):
                result = tool.invoke({"url": "https://example.com"})
                assert "OK" in result

    def test_generic_exception_exhausted(self):
        tool = self._get_tool()
        exc = RuntimeError("persistent error")
        with patch("urllib.request.urlopen", side_effect=exc):
            with patch("time.sleep"):
                result = tool.invoke({"url": "https://example.com"})
                assert "Fetch failed" in result

    def test_429_exhausted_all_retries(self):
        """All retries exhausted via 429 — last attempt returns HTTP error message."""
        import urllib.error

        tool = self._get_tool()
        exc = urllib.error.HTTPError(
            url="https://example.com",
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=exc):
            with patch("time.sleep"):
                result = tool.invoke({"url": "https://example.com"})
                assert "429" in result
