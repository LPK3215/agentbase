"""Retry middleware — bounded retries with exponential backoff.

Retries failed model calls up to ``max_attempts`` times with
exponential backoff and jitter. Non-retryable errors (authentication,
validation) are immediately re-raised without retry.

Configuration via ``agent_config.metadata.retry``:

.. code-block:: yaml

    metadata:
      retry:
        max_attempts: 3
        base_delay: 0.5        # seconds
        max_delay: 30.0         # seconds
        jitter: true            # add random jitter

Non-retryable error types (immediately raised):
- ``AuthenticationError`` — invalid API key
- ``ValidationError`` — malformed request
- ``HTTP 4xx`` (except 429) — client errors

On exhaustion the last exception is **re-raised** so that
``AgentRunner`` can classify it into a proper error code.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.middleware import register_middleware

logger = logging.getLogger("agentbase.middleware.retry")

_RETRY_META = ExtensionMeta(
    name="retry",
    kind="middleware",
    description="Retry model calls with exponential backoff and jitter.",
    requires_context=["agent_config"],
)

# Error types that should NOT be retried — they won't succeed on retry.
_NON_RETRYABLE_HINTS = (
    "authentication",
    "auth",
    "invalid_api_key",
    "invalid api key",
    "api_key_invalid",
    "unauthorized",
    "forbidden",
    "400 bad request",
    "validation",
    "invalid_request",
)


def _is_retryable(exc: Exception) -> bool:
    """Check if an exception is worth retrying.

    Returns ``False`` for authentication errors, validation errors,
    and other client-side errors that won't succeed on retry.
    """
    exc_str = str(exc).lower()
    for hint in _NON_RETRYABLE_HINTS:
        if hint in exc_str:
            return False
    # Rate limit errors (429) ARE retryable
    if "429" in exc_str or "rate limit" in exc_str:
        return True
    # Timeout and connection errors are retryable
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    # Default: retry unknown errors
    return True


def _compute_delay(
    attempt: int,
    base_delay: float,
    max_delay: float,
    jitter: bool,
) -> float:
    """Compute exponential backoff delay with optional jitter.

    Uses ``base_delay * 2^(attempt-1)`` capped at ``max_delay``,
    plus optional random jitter in [0, base_delay].
    """
    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
    if jitter:
        delay += random.uniform(0, base_delay)
    return delay


@register_middleware("retry", meta=_RETRY_META)
def build_retry(context: dict[str, Any] | None = None):
    """Retry middleware with exponential backoff.

    On exhaustion the last exception is **re-raised** (not swallowed into a
    string) so that ``AgentRunner`` can classify it into a proper error code.
    """
    context = context or {}
    agent_config = context.get("agent_config")
    max_attempts = 3
    base_delay = 0.5
    max_delay = 30.0
    jitter = True
    if agent_config is not None:
        retry_cfg = agent_config.metadata.get("retry", {})
        max_attempts = int(retry_cfg.get("max_attempts", 3))
        base_delay = float(retry_cfg.get("base_delay", 0.5))
        max_delay = float(retry_cfg.get("max_delay", 30.0))
        jitter = bool(retry_cfg.get("jitter", True))

    try:
        from langchain.agents.middleware import wrap_model_call
    except Exception:
        logger.warning(
            "middleware disabled: name=retry reason=wrap_model_call_unavailable",
            extra={"event": "middleware.disabled"},
        )
        return []

    @wrap_model_call
    def retry_middleware(request: Any, handler: Callable[[Any], Any]) -> Any:
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return handler(request)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not _is_retryable(exc):
                    logger.warning(
                        "retry skip (non-retryable) attempt=%d/%d error=%s",
                        attempt,
                        max_attempts,
                        exc,
                        extra={
                            "event": "retry.non_retryable",
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "error": str(exc),
                        },
                    )
                    raise
                if attempt < max_attempts:
                    delay = _compute_delay(attempt, base_delay, max_delay, jitter)
                    logger.info(
                        "retry attempt %d/%d failed, backing off %.2fs: %s",
                        attempt,
                        max_attempts,
                        delay,
                        exc,
                        extra={
                            "event": "retry.backoff",
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "delay_seconds": delay,
                            "error": str(exc),
                        },
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        "retry exhausted %d/%d: %s",
                        attempt,
                        max_attempts,
                        exc,
                        extra={
                            "event": "retry.exhausted",
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "error": str(exc),
                        },
                    )
        raise last_exc  # type: ignore[misc]

    return retry_middleware
