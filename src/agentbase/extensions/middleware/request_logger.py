"""Request logger middleware — structured logging for model calls.

Logs the start and end of every model call with:
- Model name
- Duration (milliseconds)
- Success/failure status
- Error summary (on failure)

Uses LangChain's ``wrap_model_call`` when available. If the middleware
API is not available, returns an empty list so agent assembly still succeeds.

Usage in config::

    middleware:
      - request_logger
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.middleware import register_middleware

logger = logging.getLogger("agentbase.middleware.request_logger")

_REQUEST_LOGGER_META = ExtensionMeta(
    name="request_logger",
    kind="middleware",
    description="Log model call start/end with model name and duration.",
    requires_context=[],
    default_enabled=True,
)


@register_middleware("request_logger", meta=_REQUEST_LOGGER_META)
def build_request_logger(context: dict[str, Any] | None = None):
    """Best-effort request logger middleware.

    Logs model call start/end with duration tracking and structured
    ``extra`` fields for downstream log aggregation.

    If ``wrap_model_call`` is not available, returns an empty list
    so agent assembly still succeeds.
    """
    try:
        from langchain.agents.middleware import wrap_model_call
    except Exception:
        logger.warning(
            "middleware disabled: name=request_logger reason=wrap_model_call_unavailable",
            extra={"event": "middleware.disabled", "agent": None, "thread_id": None, "duration_ms": None},
        )
        return []

    @wrap_model_call
    def request_logger(request: Any, handler: Callable[[Any], Any]) -> Any:
        model_name = getattr(getattr(request, "model", None), "model_name", None) or getattr(
            getattr(request, "model", None), "model", None
        )
        logger.info(
            "model_call start model=%s",
            model_name,
            extra={"event": "model_call.start", "model": model_name},
        )
        start = time.time()
        try:
            response = handler(request)
        except Exception as exc:
            duration_ms = (time.time() - start) * 1000
            logger.error(
                "model_call failed model=%s duration_ms=%.1f error=%s",
                model_name,
                duration_ms,
                exc,
                extra={
                    "event": "model_call.error",
                    "model": model_name,
                    "duration_ms": duration_ms,
                    "error": str(exc),
                },
            )
            raise
        duration_ms = (time.time() - start) * 1000
        logger.info(
            "model_call end model=%s duration_ms=%.1f",
            model_name,
            duration_ms,
            extra={
                "event": "model_call.end",
                "model": model_name,
                "duration_ms": duration_ms,
            },
        )
        return response

    return request_logger
