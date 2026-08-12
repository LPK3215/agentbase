"""Subagent factory — assembles subagent specs from registry builders.

Subagents are defined as dict specs that ``create_deep_agent`` consumes.
Each spec must have at least a ``name`` field; ``description`` and
``system_prompt`` have sensible defaults if omitted.

Error handling:
- If a subagent builder fails, it is skipped (with a warning log)
  so the parent agent can still be assembled with the remaining subagents.
- Spec validation ensures required fields are present.
"""
from __future__ import annotations

from typing import Any

from agentbase.registry.subagents import subagent_registry
from agentbase.registry.tools import tool_registry
from agentbase.runtime.errors import ErrorCode, FactoryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


def _materialize_subagent(spec: dict[str, Any], *, context: dict[str, Any]) -> dict[str, Any]:
    """Validate and materialise a subagent spec.

    - Ensures required ``name`` field is present.
    - Provides defaults for ``description`` and ``system_prompt``.
    - Resolves tool names to tool instances from the registry.
    """
    data = dict(spec)

    # Validate required fields
    if "name" not in data:
        raise FactoryError(
            "Subagent spec missing required field 'name'",
            code=ErrorCode.FACTORY_ASSEMBLY,
        )

    # Provide defaults
    if "description" not in data:
        data["description"] = f"Subagent {data['name']}"
    if "system_prompt" not in data:
        data["system_prompt"] = f"You are the {data['name']} subagent."

    # Resolve tool names to tool instances
    tool_names = data.pop("tools", None)
    if tool_names:
        tools = []
        for name in tool_names:
            try:
                builder = tool_registry.get(name)
            except Exception:
                logger.warning(
                    "Subagent '%s': tool '%s' not registered — skipping",
                    data["name"],
                    name,
                    extra={"event": "subagent.tool_missing", "subagent": data["name"], "tool": name},
                )
                continue
            try:
                tools.append(builder(context=context))
            except TypeError:
                try:
                    tools.append(builder())
                except Exception as exc:
                    logger.warning(
                        "Subagent '%s': tool '%s' build failed — skipping: %s",
                        data["name"],
                        name,
                        exc,
                        extra={"event": "subagent.tool_failed", "subagent": data["name"], "tool": name},
                    )
        data["tools"] = tools

    return data


def build_subagents(
    names: list[str],
    *,
    context: dict[str, Any] | None = None,
    skip_on_error: bool = True,
) -> list[dict[str, Any]]:
    """Build subagent specs from registry builders.

    Args:
        names: Subagent builder names to resolve.
        context: Shared context dict passed to each builder.
        skip_on_error: If ``True`` (default), skip subagents that fail
            to build. If ``False``, raise ``FactoryError``.

    Returns:
        List of materialised subagent spec dicts.
    """
    context = context or {}
    result: list[dict[str, Any]] = []
    skipped: list[str] = []

    for name in names:
        try:
            builder = subagent_registry.get(name)
        except Exception as exc:
            if skip_on_error:
                logger.warning(
                    "Subagent '%s' not registered — skipping: %s",
                    name,
                    exc,
                    extra={"event": "subagent.not_registered", "subagent": name},
                )
                skipped.append(name)
                continue
            raise FactoryError(
                f"Subagent '{name}' not registered: {exc}",
                code=ErrorCode.REG_NOT_FOUND,
                detail={"subagent": name},
            ) from exc

        try:
            spec = builder(context=context)
        except TypeError:
            try:
                spec = builder()
            except Exception as exc:
                if skip_on_error:
                    logger.warning(
                        "Subagent '%s' builder failed — skipping: %s",
                        name,
                        exc,
                        extra={"event": "subagent.build_failed", "subagent": name, "error": str(exc)},
                    )
                    skipped.append(name)
                    continue
                raise FactoryError(
                    f"Subagent builder '{name}' failed: {exc}",
                    code=ErrorCode.FACTORY_ASSEMBLY,
                    detail={"subagent": name},
                ) from exc
        except Exception as exc:
            if skip_on_error:
                logger.warning(
                    "Subagent '%s' builder failed — skipping: %s",
                    name,
                    exc,
                    extra={"event": "subagent.build_failed", "subagent": name, "error": str(exc)},
                )
                skipped.append(name)
                continue
            raise FactoryError(
                f"Subagent builder '{name}' failed: {exc}",
                code=ErrorCode.FACTORY_ASSEMBLY,
                detail={"subagent": name},
            ) from exc

        if not isinstance(spec, dict):
            if skip_on_error:
                logger.warning(
                    "Subagent '%s' builder returned non-dict — skipping",
                    name,
                    extra={"event": "subagent.invalid_type", "subagent": name},
                )
                skipped.append(name)
                continue
            raise FactoryError(
                f"Subagent builder '{name}' must return a dict",
                code=ErrorCode.FACTORY_ASSEMBLY,
                detail={"subagent": name},
            )

        try:
            result.append(_materialize_subagent(spec, context=context))
            logger.debug("Resolved subagent: %s", name)
        except FactoryError as exc:
            if skip_on_error:
                logger.warning(
                    "Subagent '%s' materialisation failed — skipping: %s",
                    name,
                    exc,
                    extra={"event": "subagent.materialise_failed", "subagent": name, "error": str(exc)},
                )
                skipped.append(name)
                continue
            raise

    if skipped:
        logger.info(
            "Subagents assembled: %d ok, %d skipped (%s)",
            len(result),
            len(skipped),
            ", ".join(skipped),
            extra={
                "event": "subagents.assembled",
                "ok_count": len(result),
                "skipped_count": len(skipped),
                "skipped": skipped,
            },
        )
    return result
