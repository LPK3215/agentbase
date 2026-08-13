"""Redact output middleware — masks PII/secrets in model responses.

Wraps model calls with ``wrap_model_call`` and applies ``RedactionManager``
to the response content before returning it to the agent.

This prevents sensitive information (API keys, emails, phone numbers, etc.)
from leaking through LLM outputs.

Features:
- Uses the existing ``RedactionManager`` from ``agentbase.core.redaction``
- Configurable via ``app_config.redaction`` or ``agent_config.metadata.redact_output``
- When redaction is disabled, passes through unchanged (zero overhead)
- Handles both string and list content formats (LangChain AIMessage)
- Logs redaction actions for observability

Configuration::

    # Global config (configs/default.yaml)
    redaction:
      enabled: true
      provider: regex

    # Per-agent override (configs/agents/my_agent.yaml)
    metadata:
      redact_output:
        enabled: true
        provider: regex

Usage in agent middleware list::

    middleware:
      - redact_output
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from agentbase.core.redaction import RedactionManager
from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.middleware import register_middleware

logger = logging.getLogger("agentbase.middleware.redact_output")

_REDACT_OUTPUT_META = ExtensionMeta(
    name="redact_output",
    kind="middleware",
    description="Redact PII/secrets from model response content before returning.",
    requires_context=["app_config"],
    default_enabled=False,
    tags=["security", "redaction", "compliance"],
)


def _redact_message_content(content: Any, manager: RedactionManager) -> Any:
    """Redact PII from a LangChain message content field.

    Handles both string content and list-of-parts content (tool calls,
    multi-modal, etc.). Only text parts are redacted; non-text parts
    are passed through unchanged.

    Args:
        content: The ``content`` attribute of an AIMessage (str or list).
        manager: The RedactionManager to use.

    Returns:
        Redacted content (same type as input).
    """
    if isinstance(content, str):
        return manager.redact(content)

    if isinstance(content, list):
        result = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                redacted = manager.redact(str(item.get("text", "")))
                result.append({**item, "text": redacted})
            elif hasattr(item, "text"):
                # Handle dataclass-like objects with .text attribute
                redacted = manager.redact(str(getattr(item, "text", "")))
                try:
                    # Try to create a copy with the redacted text
                    import copy
                    copied = copy.copy(item)
                    copied.text = redacted  # type: ignore[attr-defined]
                    result.append(copied)
                except Exception:
                    result.append(item)
            else:
                result.append(item)
        return result

    return content


@register_middleware("redact_output", meta=_REDACT_OUTPUT_META)
def build_redact_output(context: dict[str, Any] | None = None):
    """Build redact_output middleware from context.

    Reads configuration from (in priority order):
    1. ``agent_config.metadata.redact_output`` (per-agent override)
    2. ``app_config.redaction`` (global config)

    If neither is enabled, the middleware is a no-op passthrough.

    Falls back to empty list if ``wrap_model_call`` is unavailable.
    """
    context = context or {}

    # --- Resolve configuration ------------------------------------------- #
    enabled = False
    provider = "regex"
    provider_kwargs: dict[str, Any] = {}

    # Global config
    app_config = context.get("app_config")
    if app_config is not None and hasattr(app_config, "redaction"):
        redaction_cfg = app_config.redaction
        enabled = redaction_cfg.enabled
        provider = redaction_cfg.provider
        provider_kwargs = dict(redaction_cfg.options)

    # Per-agent override (takes priority)
    agent_config = context.get("agent_config")
    if agent_config is not None:
        agent_redact_cfg = agent_config.metadata.get("redact_output", {})
        if "enabled" in agent_redact_cfg:
            enabled = bool(agent_redact_cfg["enabled"])
        if "provider" in agent_redact_cfg:
            provider = agent_redact_cfg["provider"]
        if "options" in agent_redact_cfg:
            provider_kwargs.update(agent_redact_cfg["options"])

    manager = RedactionManager(provider=provider, enabled=enabled)

    if not enabled:
        logger.info(
            "redact_output middleware disabled (redaction not enabled)",
            extra={"event": "redact_output.disabled"},
        )

    # --- Build middleware ------------------------------------------------ #
    try:
        from langchain.agents.middleware import wrap_model_call
    except Exception:
        logger.warning(
            "middleware disabled: name=redact_output reason=wrap_model_call_unavailable",
            extra={"event": "middleware.disabled", "middleware": "redact_output"},
        )
        return []

    @wrap_model_call
    def redact_output_middleware(request: Any, handler: Callable[[Any], Any]) -> Any:
        """Wrap a model call and redact the response content."""
        response = handler(request)

        if not manager.enabled:
            return response

        # LangChain model responses are typically AIMessage objects.
        # The content can be a string or a list of content parts.
        content = getattr(response, "content", None)
        if content is not None:
            redacted = _redact_message_content(content, manager)
            try:
                response.content = redacted
            except (AttributeError, TypeError):
                # Some response types may be frozen/immutable — best effort
                logger.debug(
                    "redact_output: could not mutate response.content (immutable type %s)",
                    type(response).__name__,
                    extra={
                        "event": "redact_output.immutable",
                        "response_type": type(response).__name__,
                    },
                )

        logger.debug(
            "redact_output: response content redacted",
            extra={"event": "redact_output.applied"},
        )
        return response

    # Attach manager for external inspection
    redact_output_middleware.redaction_manager = manager  # type: ignore[attr-defined]
    return redact_output_middleware
