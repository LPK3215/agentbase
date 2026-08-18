"""Unit tests for the http_request tool.

Covers three paths:
- Normal: successful GET / POST with JSON body, custom headers, redirect following.
- Boundary: invalid URL, invalid method, body conflict, response truncation, timeout clamp,
  redirect limit, empty response.
- Error: connection failure, HTTP error (4xx/5xx), timeout.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from agentbase.extensions.tools.http_request import (
    _MIN_TIMEOUT,
    _validate_method,
    _validate_url,
    build_http_request_tool,
)

# ---------------------------------------------------------------------------
# Helpers — lightweight local HTTP server for integration-style tests.
# ---------------------------------------------------------------------------


class _MockHandler(BaseHTTPRequestHandler):
    """HTTP handler that returns configurable responses for testing."""

    # Class-level response config (set by test code before starting server).
    _response_status: int = 200
    _response_body: str = '{"ok": true}'
    _response_content_type: str = "application/json"
    _response_headers: dict[str, str] = {}
    _last_request: dict[str, Any] = {}

    def log_message(self, format, *args):  # noqa: A002 — suppress stderr noise.
        pass

    def _record_request(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b""
        _MockHandler._last_request = {
            "method": self.command,
            "path": self.path,
            "headers": dict(self.headers),
            "body": body.decode("utf-8", errors="replace"),
        }

    def _send_configured_response(self) -> None:
        self.send_response(self._response_status)
        self.send_header("Content-Type", self._response_content_type)
        self.send_header("Content-Length", str(len(self._response_body.encode("utf-8"))))
        for k, v in self._response_headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(self._response_body.encode("utf-8"))

    def do_GET(self) -> None:
        self._record_request()
        self._send_configured_response()

    def do_POST(self) -> None:
        self._record_request()
        self._send_configured_response()

    def do_PUT(self) -> None:
        self._record_request()
        self._send_configured_response()

    def do_PATCH(self) -> None:
        self._record_request()
        self._send_configured_response()

    def do_DELETE(self) -> None:
        self._record_request()
        self._send_configured_response()


class _MockServer:
    """Context manager that starts/stops a local HTTP server."""

    def __init__(
        self,
        status: int = 200,
        body: str = '{"ok": true}',
        content_type: str = "application/json",
        extra_headers: dict[str, str] | None = None,
    ):
        _MockHandler._response_status = status
        _MockHandler._response_body = body
        _MockHandler._response_content_type = content_type
        _MockHandler._response_headers = extra_headers or {}
        _MockHandler._last_request = {}

        self._server = HTTPServer(("127.0.0.1", 0), _MockHandler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )

    @property
    def url(self) -> str:
        port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}"

    def __enter__(self) -> "_MockServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    @property
    def last_request(self) -> dict[str, Any]:
        return _MockHandler._last_request


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


class TestValidation:
    def test_validate_url_valid_http(self):
        assert _validate_url("http://example.com") is None

    def test_validate_url_valid_https(self):
        assert _validate_url("https://example.com/path?q=1") is None

    def test_validate_url_invalid_scheme(self):
        err = _validate_url("ftp://example.com")
        assert err is not None
        assert "http://" in err

    def test_validate_url_empty(self):
        err = _validate_url("")
        assert err is not None
        assert "required" in err.lower()

    def test_validate_method_valid(self):
        for m in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            assert _validate_method(m) is None

    def test_validate_method_invalid(self):
        err = _validate_method("HEAD")
        assert err is not None
        assert "GET" in err

    def test_validate_method_empty(self):
        err = _validate_method("")
        assert err is not None


# ---------------------------------------------------------------------------
# Normal path — successful requests
# ---------------------------------------------------------------------------


class TestHttpRequestNormal:
    def test_get_success(self):
        """GET request returns structured response with status 200."""
        with _MockServer(status=200, body='{"hello": "world"}') as srv:
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({"url": srv.url, "method": "GET"})

        assert result["status_code"] == 200
        assert "hello" in result["body"]
        assert result["error"] is None
        assert result["elapsed_ms"] >= 0
        assert "content-type" in result["headers"]

    def test_post_with_json_body(self):
        """POST with JSON body sends correct Content-Type and body."""
        with _MockServer(status=201, body='{"created": true}') as srv:
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({
                "url": srv.url,
                "method": "POST",
                "json_body": {"name": "Alice", "age": 30},
            })

        assert result["status_code"] == 201
        assert result["error"] is None

        # Verify the server received the JSON body
        last = srv.last_request
        assert last["method"] == "POST"
        assert "Alice" in last["body"]
        ct = last["headers"].get("Content-Type", "")
        assert "application/json" in ct

    def test_post_with_form_data(self):
        """POST with form_data sends URL-encoded body."""
        with _MockServer(status=200, body="OK") as srv:
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({
                "url": srv.url,
                "method": "POST",
                "form_data": {"key": "value", "num": "42"},
            })

        assert result["status_code"] == 200
        last = srv.last_request
        assert "key=value" in last["body"] or "num=42" in last["body"]
        ct = last["headers"].get("Content-Type", "")
        assert "x-www-form-urlencoded" in ct

    def test_post_with_raw_body(self):
        """POST with raw text body sends text/plain."""
        with _MockServer(status=200, body="OK") as srv:
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({
                "url": srv.url,
                "method": "POST",
                "body": "plain text body",
            })

        assert result["status_code"] == 200
        last = srv.last_request
        assert last["body"] == "plain text body"
        ct = last["headers"].get("Content-Type", "")
        assert "text/plain" in ct

    def test_custom_headers(self):
        """Custom headers are sent in the request."""
        with _MockServer(status=200, body="{}") as srv:
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({
                "url": srv.url,
                "method": "GET",
                "headers": {"X-Custom-Header": "test-value", "Accept": "application/json"},
            })

        assert result["status_code"] == 200
        last = srv.last_request
        assert last["headers"].get("X-Custom-Header") == "test-value"

    def test_put_method(self):
        """PUT request works correctly."""
        with _MockServer(status=200, body='{"updated": true}') as srv:
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({
                "url": srv.url,
                "method": "PUT",
                "json_body": {"id": 1, "value": "new"},
            })

        assert result["status_code"] == 200
        assert result["error"] is None

    def test_delete_method(self):
        """DELETE request works correctly."""
        with _MockServer(status=204, body="") as srv:
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({"url": srv.url, "method": "DELETE"})

        assert result["status_code"] == 204
        assert result["error"] is None

    def test_patch_method(self):
        """PATCH request works correctly."""
        with _MockServer(status=200, body='{"patched": true}') as srv:
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({
                "url": srv.url,
                "method": "PATCH",
                "json_body": {"field": "value"},
            })

        assert result["status_code"] == 200
        assert result["error"] is None

    def test_default_method_is_get(self):
        """Default method should be GET."""
        with _MockServer(status=200, body="{}") as srv:
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({"url": srv.url})

        assert result["status_code"] == 200
        assert srv.last_request["method"] == "GET"

    def test_redirect_followed(self):
        """Redirects are followed by default."""
        # Create a separate handler class for the target server so that
        # the two servers don't share class-level state.
        class _TargetHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                body = '{"final": true}'
                self.send_header("Content-Length", str(len(body.encode("utf-8"))))
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))

        class _RedirectHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002
                pass

            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", target_url)
                self.send_header("Content-Length", "0")
                self.end_headers()

        target_server = HTTPServer(("127.0.0.1", 0), _TargetHandler)
        target_thread = threading.Thread(target=target_server.serve_forever, daemon=True)
        target_thread.start()
        try:
            target_url = f"http://127.0.0.1:{target_server.server_address[1]}"

            redirect_server = HTTPServer(("127.0.0.1", 0), _RedirectHandler)
            redirect_thread = threading.Thread(target=redirect_server.serve_forever, daemon=True)
            redirect_thread.start()
            try:
                redirect_url = f"http://127.0.0.1:{redirect_server.server_address[1]}"

                tool_fn = build_http_request_tool(context={})
                result = tool_fn.invoke({"url": redirect_url, "method": "GET"})
            finally:
                redirect_server.shutdown()
                redirect_server.server_close()
                redirect_thread.join(timeout=5)
        finally:
            target_server.shutdown()
            target_server.server_close()
            target_thread.join(timeout=5)

        assert result["status_code"] == 200
        assert "final" in result["body"]


# ---------------------------------------------------------------------------
# Boundary path — edge cases
# ---------------------------------------------------------------------------


class TestHttpRequestBoundary:
    def test_invalid_url_returns_error(self):
        """Invalid URL scheme returns structured error, not exception."""
        tool_fn = build_http_request_tool(context={})
        result = tool_fn.invoke({"url": "ftp://example.com", "method": "GET"})

        assert result["status_code"] == -1
        assert result["error"] is not None
        assert "http://" in result["error"]

    def test_empty_url_returns_error(self):
        """Empty URL returns error."""
        tool_fn = build_http_request_tool(context={})
        result = tool_fn.invoke({"url": "", "method": "GET"})

        assert result["status_code"] == -1
        assert result["error"] is not None

    def test_invalid_method_returns_error(self):
        """Unsupported method returns structured error."""
        with _MockServer(status=200, body="{}") as srv:
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({"url": srv.url, "method": "HEAD"})

        assert result["status_code"] == -1
        assert result["error"] is not None
        assert "HEAD" in result["error"]

    def test_body_conflict_returns_error(self):
        """Providing both json_body and body returns error."""
        with _MockServer(status=200, body="{}") as srv:
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({
                "url": srv.url,
                "method": "POST",
                "json_body": {"key": "val"},
                "body": "raw text",
            })

        assert result["status_code"] == -1
        assert result["error"] is not None
        assert "only one" in result["error"].lower()

    def test_response_body_truncation(self):
        """Response body is truncated to max_body_length."""
        big_body = "x" * 10000
        with _MockServer(status=200, body=big_body, content_type="text/plain") as srv:
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({
                "url": srv.url,
                "method": "GET",
                "max_body_length": 1000,
            })

        assert result["status_code"] == 200
        assert len(result["body"]) <= 1020  # 1000 + truncation marker
        assert "truncated" in result["body"]

    def test_timeout_clamped_to_min(self):
        """Timeout below minimum is clamped to _MIN_TIMEOUT."""
        tool_fn = build_http_request_tool(context={})
        # Just verify the tool accepts a very low timeout without crashing.
        # The actual clamping happens internally.
        with _MockServer(status=200, body="{}") as srv:
            result = tool_fn.invoke({"url": srv.url, "method": "GET", "timeout": 0})
        assert result["status_code"] == 200

    def test_timeout_clamped_to_max(self):
        """Timeout above maximum is clamped to _MAX_TIMEOUT."""
        tool_fn = build_http_request_tool(context={})
        with _MockServer(status=200, body="{}") as srv:
            result = tool_fn.invoke({"url": srv.url, "method": "GET", "timeout": 999})
        assert result["status_code"] == 200

    def test_follow_redirects_false(self):
        """When follow_redirects=False, redirects are not followed."""
        class _RedirectOnlyHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002
                pass

            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:99999/fake")
                self.send_header("Content-Length", "0")
                self.end_headers()

        redirect_server = HTTPServer(("127.0.0.1", 0), _RedirectOnlyHandler)
        redirect_thread = threading.Thread(target=redirect_server.serve_forever, daemon=True)
        redirect_thread.start()
        try:
            redirect_url = f"http://127.0.0.1:{redirect_server.server_address[1]}"

            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({
                "url": redirect_url,
                "method": "GET",
                "follow_redirects": False,
            })
        finally:
            redirect_server.shutdown()
            redirect_server.server_close()
            redirect_thread.join(timeout=5)

        assert result["status_code"] == 302
        assert result["error"] is not None  # 302 is treated as HTTP error by urllib
        assert "302" in result["error"]

    def test_empty_response_body(self):
        """Empty response body is handled correctly."""
        with _MockServer(status=200, body="", content_type="text/plain") as srv:
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({"url": srv.url, "method": "GET"})

        assert result["status_code"] == 200
        assert result["body"] == ""
        assert result["error"] is None

    def test_headers_lowercase(self):
        """Response header keys are normalized to lowercase."""
        with _MockServer(
            status=200,
            body="{}",
            extra_headers={"X-Custom-Header": "val"},
        ) as srv:
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({"url": srv.url, "method": "GET"})

        assert result["status_code"] == 200
        assert "x-custom-header" in result["headers"]

    def test_structured_return_keys(self):
        """Response dict always has all required keys."""
        with _MockServer(status=200, body="{}") as srv:
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({"url": srv.url, "method": "GET"})

        required_keys = {"status_code", "headers", "body", "url", "elapsed_ms", "error"}
        assert set(result.keys()) == required_keys

    def test_build_tool_with_none_context(self):
        """Tool can be built with None context."""
        tool_fn = build_http_request_tool(context=None)
        assert hasattr(tool_fn, "invoke")


# ---------------------------------------------------------------------------
# Error path — failures
# ---------------------------------------------------------------------------


class TestHttpRequestError:
    def test_connection_failure(self):
        """Connection to unreachable port returns error."""
        tool_fn = build_http_request_tool(context={})
        result = tool_fn.invoke({
            "url": "http://127.0.0.1:1",  # Port 1 is not a valid HTTP server.
            "method": "GET",
            "timeout": 5,
        })

        assert result["status_code"] == -1
        assert result["error"] is not None
        assert result["body"] == ""

    def test_http_error_404(self):
        """HTTP 404 returns structured error with status code."""
        with _MockServer(status=404, body='{"error": "not found"}') as srv:
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({"url": srv.url, "method": "GET"})

        assert result["status_code"] == 404
        assert result["error"] is not None
        assert "404" in result["error"]
        assert "not found" in result["body"]

    def test_http_error_500(self):
        """HTTP 500 returns structured error with status code."""
        with _MockServer(status=500, body="Internal Server Error") as srv:
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({"url": srv.url, "method": "GET"})

        assert result["status_code"] == 500
        assert result["error"] is not None
        assert "500" in result["error"]

    def test_timeout_error(self):
        """Request timeout returns error with timeout message."""
        # Create a server that deliberately delays response beyond timeout.
        class _SlowHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002
                pass

            def do_GET(self):
                import time as _time
                _time.sleep(10)  # Sleep 10 seconds.
                self.send_response(200)
                self.end_headers()

        slow_server = HTTPServer(("127.0.0.1", 0), _SlowHandler)
        slow_thread = threading.Thread(target=slow_server.serve_forever, daemon=True)
        slow_thread.start()
        try:
            port = slow_server.server_address[1]
            slow_url = f"http://127.0.0.1:{port}"

            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({
                "url": slow_url,
                "method": "GET",
                "timeout": _MIN_TIMEOUT,  # Use minimum allowed timeout (5s).
            })
        finally:
            slow_server.shutdown()
            slow_server.server_close()
            slow_thread.join(timeout=10)

        assert result["status_code"] == -1
        assert result["error"] is not None
        assert "timed out" in result["error"].lower()


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestHttpRequestRegistry:
    def test_registered_in_tool_registry(self, bootstrapped):
        """Tool is registered in tool_registry after bootstrap."""
        from agentbase.registry.tools import tool_registry

        assert tool_registry.has("http_request")

    def test_build_via_factory(self, bootstrapped):
        """Tool can be built through the build_tools factory."""
        from agentbase.factories.tool_factory import build_tools

        tools = build_tools(["http_request"], context={})
        assert len(tools) == 1
        assert hasattr(tools[0], "invoke")

    def test_meta_default_disabled(self, bootstrapped):
        """Tool metadata has default_enabled=False."""
        from agentbase.registry.tools import tool_registry

        meta = tool_registry.get_meta("http_request")
        assert meta is not None
        assert meta.default_enabled is False
        assert meta.kind == "tool"
        assert "http" in meta.tags

    def test_strict_mode_unknown_still_works(self, bootstrapped):
        """Building http_request in strict mode works."""
        from agentbase.factories.tool_factory import build_tools

        tools = build_tools(["http_request"], context={}, skip_on_error=False)
        assert len(tools) == 1


# ---------------------------------------------------------------------------
# Supplementary tests for missing coverage
# ---------------------------------------------------------------------------


class TestHttpRequestExtras:
    def test_response_too_large(self):
        """Response exceeding _MAX_RESPONSE_SIZE returns 'too large' message."""
        from agentbase.extensions.tools.http_request import _MAX_RESPONSE_SIZE

        big_body = "x" * (_MAX_RESPONSE_SIZE + 100)
        with _MockServer(status=200, body=big_body, content_type="text/plain") as srv:
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({"url": srv.url, "method": "GET"})

        assert result["status_code"] == 200
        assert "too large" in result["body"]

    def test_charset_detection(self):
        """Content-Type with charset is used for decoding."""
        with _MockServer(
            status=200,
            body="hello world",
            content_type="text/plain; charset=utf-8",
        ) as srv:
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({"url": srv.url, "method": "GET"})

        assert result["status_code"] == 200
        assert "hello" in result["body"]

    def test_decode_fallback_on_bad_encoding(self):
        """When encoding from Content-Type is invalid, falls back to utf-8 replace."""
        # Use a mock to simulate a response with invalid charset
        from unittest.mock import MagicMock, patch

        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.headers = {"Content-Type": "text/plain; charset=invalid-encoding"}
        fake_resp.url = "http://example.com"
        raw = "héllo wörld".encode("utf-8")
        fake_resp.read.return_value = raw
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.build_opener") as mock_opener:
            mock_opener.return_value.open.return_value = fake_resp
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({"url": "http://example.com", "method": "GET"})

        assert result["status_code"] == 200
        assert result["error"] is None

    def test_http_error_read_exception(self):
        """HTTPError where reading the error body raises is handled gracefully."""
        import urllib.error
        from unittest.mock import patch

        def fake_open(*args, **kwargs):
            raise urllib.error.HTTPError(
                url="http://example.com/404",
                code=404,
                msg="Not Found",
                hdrs={"Content-Type": "text/plain"},
                fp=None,
            )

        with patch("urllib.request.build_opener") as mock_opener:
            mock_opener.return_value.open.side_effect = fake_open
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({"url": "http://example.com", "method": "GET"})

        assert result["status_code"] == 404
        assert result["error"] is not None
        assert "404" in result["error"]

    def test_unexpected_error_during_request(self):
        """Unexpected non-HTTP exceptions are caught and returned as error dict."""
        from unittest.mock import patch

        with patch("urllib.request.build_opener") as mock_opener:
            mock_opener.return_value.open.side_effect = RuntimeError("unexpected failure")
            tool_fn = build_http_request_tool(context={})
            result = tool_fn.invoke({"url": "http://example.com", "method": "GET"})

        assert result["status_code"] == -1
        assert result["error"] is not None
        assert "Unexpected error" in result["error"]
