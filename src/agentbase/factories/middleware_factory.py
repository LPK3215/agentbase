"""Middleware factory — assembles middleware instances from registry builders.

The factory resolves middleware names to their registered builder functions,
calls each with the shared context dict, and collects the results.

Error handling:
- If a middleware builder raises, the error is logged and the middleware
  is **skipped** rather than crashing the entire agent assembly. This
  allows agents to work even when optional middleware is unavailable
  (e.g. ``langchain.agents.middleware`` not installed).
- If ``skip_on_error=False``, the error is re-raised (strict mode).

Middleware ordering:
- Builders are called in the order specified by ``names`` — this guarantees
  that middleware executes in the order the user configured.
- If a builder returns a list (e.g. from ``wrap_model_call``), items are
  extended into the result list, preserving their relative order.
"""
from __future__ import annotations

from typing import Any

from agentbase.registry.middleware import middleware_registry
from agentbase.runtime.errors import ErrorCode, FactoryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


def build_middleware(
    names: list[str],
    *,
    context: dict[str, Any] | None = None,
    skip_on_error: bool = True,
) -> list[Any]:
    """Build middleware instances from registry builders.

    Args:
        names: Middleware names to resolve, in execution order.
        context: Shared context dict passed to each builder.
        skip_on_error: If ``True`` (default), skip middleware that fail
            to build and log a warning. If ``False``, raise ``FactoryError``.

    Returns:
        List of assembled middleware instances (may be shorter than
        ``names`` if some were skipped).
    """
    context = context or {}
    items: list[Any] = []
    skipped: list[str] = []

    for name in names:
        try:
            builder = middleware_registry.get(name)
        except Exception as exc:
            if skip_on_error:
                logger.warning(
                    "Middleware '%s' not registered — skipping: %s",
                    name,
                    exc,
                    extra={"event": "middleware.not_registered", "middleware": name},
                )
                skipped.append(name)
                continue
            raise FactoryError(
                f"Middleware '{name}' not registered: {exc}",
                code=ErrorCode.REG_NOT_FOUND,
                detail={"middleware": name},
            ) from exc

        try:
            item = builder(context=context)
        except TypeError:
            try:
                item = builder()
            except Exception as exc:
                if skip_on_error:
                    logger.warning(
                        "Middleware '%s' builder failed — skipping: %s",
                        name,
                        exc,
                        extra={"event": "middleware.build_failed", "middleware": name, "error": str(exc)},
                    )
                    skipped.append(name)
                    continue
                raise FactoryError(
                    f"Middleware builder '{name}' failed: {exc}",
                    code=ErrorCode.FACTORY_ASSEMBLY,
                    detail={"middleware": name},
                ) from exc
        except Exception as exc:
            if skip_on_error:
                logger.warning(
                    "Middleware '%s' builder failed — skipping: %s",
                    name,
                    exc,
                    extra={"event": "middleware.build_failed", "middleware": name, "error": str(exc)},
                )
                skipped.append(name)
                continue
            raise FactoryError(
                f"Middleware builder '{name}' failed: {exc}",
                code=ErrorCode.FACTORY_ASSEMBLY,
                detail={"middleware": name},
            ) from exc

        if item is None:
            if skip_on_error:
                logger.warning(
                    "Middleware '%s' builder returned None — skipping",
                    name,
                    extra={"event": "middleware.none_result", "middleware": name},
                )
                skipped.append(name)
                continue
            raise FactoryError(
                f"Middleware builder '{name}' returned None",
                code=ErrorCode.FACTORY_ASSEMBLY,
                detail={"middleware": name},
            )

        # Allow builders to return lists (e.g. wrap_model_call results)
        if isinstance(item, list):
            items.extend(item)
        else:
            items.append(item)
        logger.debug("Resolved middleware: %s", name)

    if skipped:
        logger.info(
            "Middleware assembled: %d ok, %d skipped (%s)",
            len(items),
            len(skipped),
            ", ".join(skipped),
            extra={
                "event": "middleware.assembled",
                "ok_count": len(items),
                "skipped_count": len(skipped),
                "skipped": skipped,
            },
        )
    return items
