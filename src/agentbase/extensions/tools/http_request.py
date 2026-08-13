"""HTTP request tool — Agent发起HTTP请求并返回结构化响应。

Tool provided:
- ``http_request`` — 发起 HTTP 请求（GET/POST/PUT/PATCH/DELETE），返回结构化结果

Features:
- 支持 GET / POST / PUT / PATCH / DELETE 方法
- 可配置超时（默认 15s，上限 30s）
- 响应大小上限（默认 1MB，硬上限 10MB）
- 重定向跟随限制（默认 5 次，上限 10 次）
- 可自定义请求头（Content-Type / Authorization / User-Agent 等）
- 请求体支持 JSON / 表单 / 纯文本
- 非 2xx 响应结构化返回（不抛异常）
- 输出 body 自动截断
- 关键路径日志可观测

Usage::

    tools:
      - http_request

The agent can then call::

    http_request(url="https://api.example.com/data", method="GET")
    http_request(url="https://api.example.com/users", method="POST",
                 json={"name": "Alice"}, headers={"Authorization": "Bearer xxx"})
"""

from __future__ import annotations

import json as json_module
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode

from langchain_core.tools import tool

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.tools import register_tool
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)

_HTTP_REQUEST_META = ExtensionMeta(
    name="http_request",
    kind="tool",
    description="Make an HTTP request and return a structured response (status, headers, body).",
    requires_context=[],
    default_enabled=False,
    tags=["http", "network", "api"],
)

# --- Safety limits --------------------------------------------------------- #

_MAX_REDIRECTS = 5          # 默认重定向次数
_MAX_REDIRECTS_CAP = 10     # 重定向硬上限
_DEFAULT_TIMEOUT = 15       # 默认超时（秒）
_MAX_TIMEOUT = 30           # 超时硬上限
_MIN_TIMEOUT = 5            # 超时下限
_MAX_RESPONSE_SIZE = 10_000_000   # 10 MB 硬上限
_DEFAULT_MAX_BODY = 50_000        # 默认 body 截断长度（字符）
_MAX_BODY_CAP = 500_000           # body 截断硬上限

_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

# 默认 User-Agent
_DEFAULT_USER_AGENT = "agentbase/0.4.0 (HTTP Request Tool)"


class _LimitedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """HTTPRedirectHandler subclass that enforces a max redirect count.

    The ``max_redirects`` attribute is set on each instance before use.
    A shared counter dict is passed to track redirect attempts.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        counter = getattr(self, "_redirect_counter", None)
        limit = getattr(self, "_max_redirects", _MAX_REDIRECTS)
        if counter is not None:
            counter["count"] += 1
            if counter["count"] > limit:
                return None  # Stop following redirects.
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_url(url: str) -> str | None:
    """验证 URL 格式，返回错误消息或 None（表示合法）。

    Args:
        url: 待验证的 URL 字符串。

    Returns:
        错误消息字符串，或 None 表示 URL 合法。
    """
    if not url or not isinstance(url, str):
        return "URL is required and must be a non-empty string."
    if not url.startswith(("http://", "https://")):
        return f"Invalid URL: must start with http:// or https:// (got: {url[:50]})"
    return None


def _validate_method(method: str) -> str | None:
    """验证 HTTP 方法，返回错误消息或 None。

    Args:
        method: HTTP 方法字符串（大写）。

    Returns:
        错误消息字符串，或 None 表示方法合法。
    """
    if method not in _ALLOWED_METHODS:
        allowed = ", ".join(sorted(_ALLOWED_METHODS))
        return f"Unsupported method: '{method}'. Allowed: {allowed}."
    return None


def _build_request(
    url: str,
    method: str,
    headers: dict[str, str],
    json_body: Any | None,
    form_data: dict[str, str] | None,
    raw_body: str | None,
) -> urllib.request.Request:
    """构建 urllib Request 对象。

    Args:
        url: 请求 URL。
        method: HTTP 方法（大写）。
        headers: 请求头字典。
        json_body: JSON 请求体（将被序列化）。
        form_data: 表单数据（将被 URL 编码）。
        raw_body: 原始请求体文本。

    Returns:
        urllib.request.Request 对象。

    Raises:
        ValueError: 当 json_body / form_data / raw_body 同时提供时。
    """
    body: bytes | None = None
    final_headers = {"User-Agent": _DEFAULT_USER_AGENT}
    final_headers.update(headers)

    body_count = sum(1 for x in (json_body, form_data, raw_body) if x is not None)
    if body_count > 1:
        raise ValueError(
            "Only one of 'json', 'form_data', 'body' can be provided at a time."
        )

    if json_body is not None:
        body = json_module.dumps(json_body).encode("utf-8")
        final_headers.setdefault("Content-Type", "application/json")
    elif form_data is not None:
        body = urlencode(form_data).encode("utf-8")
        final_headers.setdefault(
            "Content-Type", "application/x-www-form-urlencoded"
        )
    elif raw_body is not None:
        body = raw_body.encode("utf-8")
        final_headers.setdefault("Content-Type", "text/plain")

    req = urllib.request.Request(url, data=body, headers=final_headers, method=method)
    return req


def _truncate_body(text: str, max_length: int) -> str:
    """截断 body 到指定长度。

    Args:
        text: 原始文本。
        max_length: 最大字符数。

    Returns:
        截断后的文本（超出时追加截断标记）。
    """
    if len(text) > max_length:
        return text[:max_length] + "\n...(truncated)"
    return text


@register_tool("http_request", meta=_HTTP_REQUEST_META)
def build_http_request_tool(context: dict[str, Any] | None = None):
    """构建 http_request 工具实例。

    Args:
        context: 共享上下文字典（此工具不依赖任何上下文）。

    Returns:
        langchain Tool 实例。
    """

    @tool
    def http_request(
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | list | None = None,
        form_data: dict[str, str] | None = None,
        body: str | None = None,
        timeout: int = _DEFAULT_TIMEOUT,
        max_body_length: int = _DEFAULT_MAX_BODY,
        follow_redirects: bool = True,
    ) -> dict[str, Any]:
        """Make an HTTP request and return a structured response.

        Args:
            url: The URL to request (must start with http:// or https://).
            method: HTTP method — GET / POST / PUT / PATCH / DELETE (default GET).
            headers: Optional request headers dict.
            json_body: JSON body to send (sets Content-Type to application/json).
            form_data: Form data to send (URL-encoded body).
            body: Raw text body to send.
            timeout: Request timeout in seconds (default 15, min 5, max 30).
            max_body_length: Max chars of response body to return (default 50000).
            follow_redirects: Whether to follow HTTP redirects (default True, max 5).

        Returns:
            dict with keys:
                - status_code: HTTP status code (int, -1 on connection failure).
                - headers: Response headers dict (lowercase keys).
                - body: Response body text (truncated to max_body_length).
                - url: Final URL after redirects.
                - elapsed_ms: Request duration in milliseconds.
                - error: Error message if request failed (None on success).
        """
        method = method.upper().strip()

        # --- 参数校验 ------------------------------------------------------- #
        url_err = _validate_url(url)
        if url_err:
            logger.warning(
                "http_request invalid URL: %s",
                url[:80],
                extra={"event": "http_request.invalid_url", "url_prefix": url[:80]},
            )
            return {
                "status_code": -1,
                "headers": {},
                "body": "",
                "url": url,
                "elapsed_ms": 0,
                "error": url_err,
            }

        method_err = _validate_method(method)
        if method_err:
            logger.warning(
                "http_request invalid method: %s",
                method,
                extra={"event": "http_request.invalid_method", "method": method},
            )
            return {
                "status_code": -1,
                "headers": {},
                "body": "",
                "url": url,
                "elapsed_ms": 0,
                "error": method_err,
            }

        # Clamp timeout
        timeout = min(max(timeout, _MIN_TIMEOUT), _MAX_TIMEOUT)
        # Clamp max_body_length
        max_body_length = min(max(max_body_length, 1000), _MAX_BODY_CAP)

        # Redirect policy
        max_redirects = _MAX_REDIRECTS if follow_redirects else 0

        req_headers = headers or {}

        # --- 构建请求 ------------------------------------------------------- #
        try:
            req = _build_request(url, method, req_headers, json_body, form_data, body)
        except ValueError as exc:
            logger.warning(
                "http_request body conflict: %s",
                exc,
                extra={"event": "http_request.body_conflict", "error": str(exc)},
            )
            return {
                "status_code": -1,
                "headers": {},
                "body": "",
                "url": url,
                "elapsed_ms": 0,
                "error": str(exc),
            }

        # --- 发送请求 ------------------------------------------------------- #
        start = time.monotonic()
        current_url = url
        last_error: str | None = None

        # Custom redirect handler to enforce max_redirects.
        redirect_counter: dict[str, int] = {"count": 0}
        redirect_handler = _LimitedRedirectHandler()
        redirect_handler._max_redirects = max_redirects
        redirect_handler._redirect_counter = redirect_counter

        opener = urllib.request.build_opener(redirect_handler)

        try:
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read(_MAX_RESPONSE_SIZE + 1)
                if len(raw) > _MAX_RESPONSE_SIZE:
                    return {
                        "status_code": resp.status,
                        "headers": {k.lower(): v for k, v in resp.headers.items()},
                        "body": f"Response too large (exceeds {_MAX_RESPONSE_SIZE} bytes)",
                        "url": resp.url or current_url,
                        "elapsed_ms": int((time.monotonic() - start) * 1000),
                        "error": None,
                    }

                # Detect encoding
                encoding = "utf-8"
                content_type = resp.headers.get("Content-Type", "")
                charset_idx = content_type.lower().find("charset=")
                if charset_idx != -1:
                    charset_part = content_type[charset_idx + 8:].split(";")[0].strip()
                    if charset_part:
                        encoding = charset_part

                try:
                    body_text = raw.decode(encoding)
                except (UnicodeDecodeError, LookupError):
                    body_text = raw.decode("utf-8", errors="replace")

                body_text = _truncate_body(body_text, max_body_length)
                elapsed = int((time.monotonic() - start) * 1000)

                logger.info(
                    "http_request %s %s → %d (%dms)",
                    method,
                    url[:80],
                    resp.status,
                    elapsed,
                    extra={
                        "event": "http_request.success",
                        "method": method,
                        "url": url,
                        "status_code": resp.status,
                        "elapsed_ms": elapsed,
                    },
                )

                return {
                    "status_code": resp.status,
                    "headers": {k.lower(): v for k, v in resp.headers.items()},
                    "body": body_text,
                    "url": resp.url or current_url,
                    "elapsed_ms": elapsed,
                    "error": None,
                }

        except urllib.error.HTTPError as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            # 读取错误响应体
            err_body = ""
            try:
                err_raw = exc.read(_MAX_RESPONSE_SIZE + 1)
                if len(err_raw) <= _MAX_RESPONSE_SIZE:
                    err_body = err_raw.decode("utf-8", errors="replace")
                    err_body = _truncate_body(err_body, max_body_length)
                else:
                    err_body = f"Response too large (exceeds {_MAX_RESPONSE_SIZE} bytes)"
            except Exception:
                pass

            logger.warning(
                "http_request %s %s → HTTP %d (%dms)",
                method,
                url[:80],
                exc.code,
                elapsed,
                extra={
                    "event": "http_request.http_error",
                    "method": method,
                    "url": url,
                    "status_code": exc.code,
                    "elapsed_ms": elapsed,
                },
            )

            return {
                "status_code": exc.code,
                "headers": {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {},
                "body": err_body,
                "url": current_url,
                "elapsed_ms": elapsed,
                "error": f"HTTP {exc.code}: {exc.reason}",
            }

        except urllib.error.URLError as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            last_error = f"URL error: {exc.reason}"
            logger.warning(
                "http_request %s %s → URLError (%dms): %s",
                method,
                url[:80],
                elapsed,
                exc.reason,
                extra={
                    "event": "http_request.url_error",
                    "method": method,
                    "url": url,
                    "elapsed_ms": elapsed,
                    "error": str(exc.reason),
                },
            )
            return {
                "status_code": -1,
                "headers": {},
                "body": "",
                "url": current_url,
                "elapsed_ms": elapsed,
                "error": last_error,
            }

        except TimeoutError:
            elapsed = int((time.monotonic() - start) * 1000)
            last_error = f"Request timed out after {timeout}s"
            logger.warning(
                "http_request %s %s → timeout (%dms)",
                method,
                url[:80],
                elapsed,
                extra={
                    "event": "http_request.timeout",
                    "method": method,
                    "url": url,
                    "elapsed_ms": elapsed,
                    "timeout": timeout,
                },
            )
            return {
                "status_code": -1,
                "headers": {},
                "body": "",
                "url": current_url,
                "elapsed_ms": elapsed,
                "error": last_error,
            }

        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            last_error = f"Unexpected error: {exc}"
            logger.error(
                "http_request %s %s → unexpected error (%dms): %s",
                method,
                url[:80],
                elapsed,
                exc,
                extra={
                    "event": "http_request.unexpected_error",
                    "method": method,
                    "url": url,
                    "elapsed_ms": elapsed,
                    "error": str(exc),
                },
                exc_info=True,
            )
            return {
                "status_code": -1,
                "headers": {},
                "body": "",
                "url": current_url,
                "elapsed_ms": elapsed,
                "error": last_error,
            }

    return http_request
