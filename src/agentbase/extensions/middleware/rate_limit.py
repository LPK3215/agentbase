"""Rate limit middleware — limits model calls per agent with sliding window.

Reuses the API-layer ``RateLimiter`` semantics (sliding window + burst)
but applies them at the model-call level, not the HTTP level.

When a rate limit is exceeded, the middleware raises a
``RuntimeExecutionError`` with ``ErrorCode.RATE_EXCEEDED`` so the
runner can map it to a proper error code.

Configuration via ``agent_config.metadata.rate_limit``:

.. code-block:: yaml

    metadata:
      rate_limit:
        max_requests: 20      # requests allowed per window
        window_seconds: 60    # sliding window duration
        burst: 5              # extra burst capacity
        scope: agent          # "agent" (per-agent) or "global" (all agents)

Semantics (matching the API-layer RateLimiter):
- ``max_requests`` is the base limit per ``window_seconds``.
- ``burst`` is extra short-burst capacity on top of ``max_requests``.
- Total capacity = ``max_requests + burst``.
- ``burst=0`` means hard limit at exactly ``max_requests``.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any, Callable

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.middleware import register_middleware
from agentbase.runtime.errors import ErrorCode, RuntimeExecutionError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)

_RATE_LIMIT_META = ExtensionMeta(
    name="rate_limit",
    kind="middleware",
    description="Rate-limit model calls per agent with sliding window + burst.",
    requires_context=["agent_config"],
    default_enabled=False,
)


class AgentRateLimiter:
    """Sliding-window rate limiter for model calls.

    Uses the same algorithm as the API-layer ``RateLimiter``: timestamps
    are stored per scope key (agent name or "global"), and the count
    within the sliding window must not exceed ``max_requests + burst``.

    Thread-safe via ``threading.Lock``.
    """

    def __init__(
        self,
        *,
        max_requests: int = 60,
        window_seconds: int = 60,
        burst: int = 10,
    ) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self.burst = burst
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        """Check if a request is allowed under the rate limit.

        Returns ``True`` if allowed, ``False`` if rate-limited.
        Also records the timestamp if allowed.
        """
        now = time.time()
        with self._lock:
            bucket = self._buckets[key]
            # Sliding window: remove timestamps outside the window
            recent = [t for t in bucket if now - t < self.window]
            self._buckets[key] = recent
            if len(recent) >= self.max_requests + self.burst:
                return False
            recent.append(now)
            self._buckets[key] = recent
            return True

    def get_remaining(self, key: str) -> int:
        """Get remaining requests for a key."""
        now = time.time()
        with self._lock:
            bucket = self._buckets.get(key, [])
            recent = [t for t in bucket if now - t < self.window]
            return max(0, self.max_requests + self.burst - len(recent))

    def reset(self) -> None:
        """Clear all buckets."""
        with self._lock:
            self._buckets.clear()

    @property
    def stats(self) -> dict[str, Any]:
        """Return rate limiter statistics."""
        now = time.time()
        with self._lock:
            per_key: dict[str, int] = {}
            for key, bucket in self._buckets.items():
                recent = [t for t in bucket if now - t < self.window]
                per_key[key] = len(recent)
            return {
                "max_requests": self.max_requests,
                "window_seconds": self.window,
                "burst": self.burst,
                "capacity": self.max_requests + self.burst,
                "active_keys": len(per_key),
                "per_key": per_key,
            }


@register_middleware("rate_limit", meta=_RATE_LIMIT_META)
def build_rate_limit(context: dict[str, Any] | None = None):
    """Build rate-limit middleware from agent config context.

    Reads configuration from ``agent_config.metadata.rate_limit``:
    - ``max_requests`` (default 60)
    - ``window_seconds`` (default 60)
    - ``burst`` (default 10)
    - ``scope``: ``"agent"`` (per-agent) or ``"global"`` (default ``"agent"``)
    """
    context = context or {}
    agent_config = context.get("agent_config")

    max_requests = 60
    window_seconds = 60
    burst = 10
    scope = "agent"

    if agent_config is not None:
        rl_cfg = agent_config.metadata.get("rate_limit", {})
        max_requests = int(rl_cfg.get("max_requests", 60))
        window_seconds = int(rl_cfg.get("window_seconds", 60))
        burst = int(rl_cfg.get("burst", 10))
        scope = rl_cfg.get("scope", "agent")

    limiter = AgentRateLimiter(
        max_requests=max_requests,
        window_seconds=window_seconds,
        burst=burst,
    )

    def rate_limit_invoke(invoke_fn: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap an invoke function with rate limiting."""

        def limited_invoke(*, agent_name: str = "default", **kwargs: Any) -> Any:
            key = agent_name if scope == "agent" else "global"
            if not limiter.check(key):
                remaining = limiter.get_remaining(key)
                logger.warning(
                    "Rate limited: agent=%s key=%s remaining=%d",
                    agent_name,
                    key,
                    remaining,
                    extra={
                        "event": "rate_limit.exceeded",
                        "agent": agent_name,
                        "scope": scope,
                        "remaining": remaining,
                    },
                )
                raise RuntimeExecutionError(
                    f"Rate limit exceeded for {scope}='{key}': "
                    f"max={max_requests}+{burst}/window={window_seconds}s",
                    code=ErrorCode.RATE_EXCEEDED,
                    detail={
                        "scope": scope,
                        "key": key,
                        "max_requests": max_requests,
                        "burst": burst,
                        "window_seconds": window_seconds,
                        "remaining": remaining,
                    },
                )

            logger.debug(
                "Rate limit check passed: agent=%s key=%s remaining=%d",
                agent_name,
                key,
                limiter.get_remaining(key),
                extra={
                    "event": "rate_limit.check",
                    "agent": agent_name,
                    "scope": scope,
                    "remaining": limiter.get_remaining(key),
                },
            )
            return invoke_fn(agent_name=agent_name, **kwargs)

        # Attach limiter for external inspection/reset
        limited_invoke.limiter = limiter  # type: ignore[attr-defined]
        return limited_invoke

    return rate_limit_invoke
