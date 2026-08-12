"""Tool factory — assembles tool instances from registry builders.

The factory resolves tool names to their registered builder functions,
calls each with the shared context dict, and collects the results.

Error handling:
- If a tool builder raises, the error is logged and the tool is **skipped**
  rather than crashing the entire agent assembly. This allows agents to
  degrade gracefully when optional tools are unavailable (e.g. missing
  optional dependency).
- If ``skip_on_error=False``, the error is re-raised (strict mode).
"""
from __future__ import annotations

from typing import Any

from agentbase.registry.tools import tool_registry
from agentbase.runtime.errors import ErrorCode, FactoryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


def build_tools(
    names: list[str],
    *,
    context: dict[str, Any] | None = None,
    skip_on_error: bool = True,
) -> list[Any]:
    """Build tool instances from registry builders.

    Args:
        names: Tool names to resolve.
        context: Shared context dict passed to each builder.
        skip_on_error: If ``True`` (default), skip tools that fail to build
            and log a warning. If ``False``, raise ``FactoryError``.

    Returns:
        List of assembled tool instances (may be shorter than ``names``
        if some tools were skipped).
    """
    context = context or {}
    tools: list[Any] = []
    skipped: list[str] = []
    for name in names:
        try:
            builder = tool_registry.get(name)
        except Exception as exc:
            if skip_on_error:
                logger.warning(
                    "Tool '%s' not registered — skipping: %s",
                    name,
                    exc,
                    extra={"event": "tool.not_registered", "tool": name},
                )
                skipped.append(name)
                continue
            raise FactoryError(
                f"Tool '{name}' not registered: {exc}",
                code=ErrorCode.REG_NOT_FOUND,
                detail={"tool": name},
            ) from exc

        try:
            tool = builder(context=context)
        except TypeError:
            try:
                tool = builder()
            except Exception as exc:
                if skip_on_error:
                    logger.warning(
                        "Tool '%s' builder failed — skipping: %s",
                        name,
                        exc,
                        extra={"event": "tool.build_failed", "tool": name, "error": str(exc)},
                    )
                    skipped.append(name)
                    continue
                raise FactoryError(
                    f"Tool builder '{name}' failed: {exc}",
                    code=ErrorCode.FACTORY_ASSEMBLY,
                    detail={"tool": name},
                ) from exc
        except Exception as exc:
            if skip_on_error:
                logger.warning(
                    "Tool '%s' builder failed — skipping: %s",
                    name,
                    exc,
                    extra={"event": "tool.build_failed", "tool": name, "error": str(exc)},
                )
                skipped.append(name)
                continue
            raise FactoryError(
                f"Tool builder '{name}' failed: {exc}",
                code=ErrorCode.FACTORY_ASSEMBLY,
                detail={"tool": name},
            ) from exc

        if tool is None:
            if skip_on_error:
                logger.warning(
                    "Tool '%s' builder returned None — skipping",
                    name,
                    extra={"event": "tool.none_result", "tool": name},
                )
                skipped.append(name)
                continue
            raise FactoryError(
                f"Tool builder '{name}' returned None",
                code=ErrorCode.FACTORY_ASSEMBLY,
                detail={"tool": name},
            )

        tools.append(tool)
        logger.debug("Resolved tool: %s", name)

    if skipped:
        logger.info(
            "Tools assembled: %d ok, %d skipped (%s)",
            len(tools),
            len(skipped),
            ", ".join(skipped),
            extra={
                "event": "tools.assembled",
                "ok_count": len(tools),
                "skipped_count": len(skipped),
                "skipped": skipped,
            },
        )
    return tools
