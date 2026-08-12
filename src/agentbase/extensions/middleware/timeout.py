"""Timeout middleware — aborts model calls after a configured duration.

Uses ``concurrent.futures.ThreadPoolExecutor`` to enforce a hard timeout
on model calls. A shared executor is reused across calls to avoid the
overhead of thread creation/destruction on every invocation.

Note: the underlying thread continues in the background (a known CPython
limitation for synchronous timeouts); the important guarantee is that the
caller is *unblocked* and receives a ``TimeoutError``.

Configuration via ``agent_config.metadata.timeout``:

.. code-block:: yaml

    metadata:
      timeout:
        seconds: 30
"""
from __future__ import annotations

import concurrent.futures
import logging
from typing import Any, Callable

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.middleware import register_middleware

logger = logging.getLogger("agentbase.middleware.timeout")

_TIMEOUT_META = ExtensionMeta(
    name="timeout",
    kind="middleware",
    description="Timeout model calls after N seconds.",
    requires_context=["agent_config"],
)


@register_middleware("timeout", meta=_TIMEOUT_META)
def build_timeout(context: dict[str, Any] | None = None):
    """Timeout middleware: aborts model calls after a configured duration.

    Reuses a single ``ThreadPoolExecutor`` across calls for efficiency.
    """
    context = context or {}
    agent_config = context.get("agent_config")
    seconds = 30
    if agent_config is not None:
        seconds = int(agent_config.metadata.get("timeout", {}).get("seconds", 30))

    try:
        from langchain.agents.middleware import wrap_model_call
    except Exception:
        logger.warning(
            "middleware disabled: name=timeout reason=wrap_model_call_unavailable",
            extra={"event": "middleware.disabled"},
        )
        return []

    # Shared executor — reuse threads instead of creating a new pool per call
    _executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=4,
        thread_name_prefix="agentbase-timeout",
    )

    @wrap_model_call
    def timeout_middleware(request: Any, handler: Callable[[Any], Any]) -> Any:
        future = _executor.submit(handler, request)
        try:
            return future.result(timeout=seconds)
        except concurrent.futures.TimeoutError:
            logger.warning(
                "model call timed out after %ds",
                seconds,
                extra={
                    "event": "timeout.exceeded",
                    "timeout_seconds": seconds,
                },
            )
            raise TimeoutError(f"model call timed out after {seconds}s") from None

    return timeout_middleware
