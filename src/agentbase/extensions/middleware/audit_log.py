"""Audit log middleware — automatic audit recording for model calls.

Intercepts every model call and records an ``AuditEvent`` to the
``AuditManager``, capturing:

- **Actor**: agent name (from context) or ``"system"``
- **Action**: ``"model.call"``
- **Resource**: model name (e.g. ``"gpt-4"``)
- **Result**: ``"success"`` or ``"failure"``
- **Detail**: ``duration_ms``, ``error`` (on failure), ``thread_id``

This enables enterprise compliance forensics without modifying agent
business logic.

Uses LangChain's ``wrap_model_call`` when available. If the middleware
API is not available, returns an empty list so agent assembly still succeeds.

Requires ``audit.enabled = true`` in config. When audit is disabled,
the middleware is a no-op (returns empty list).

Usage in config::

    audit:
      enabled: true
      provider: sqlite

    middleware:
      - audit_log
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.middleware import register_middleware

logger = logging.getLogger("agentbase.middleware.audit_log")

_AUDIT_LOG_META = ExtensionMeta(
    name="audit_log",
    kind="middleware",
    description="Record audit events for model calls (actor, action, result, duration).",
    requires_context=["audit_manager"],
    default_enabled=False,
)


@register_middleware("audit_log", meta=_AUDIT_LOG_META)
def build_audit_log(context: dict[str, Any] | None = None):
    """Audit log middleware — records AuditEvent for each model call.

    Extracts the ``AuditManager`` from the context dict. If not found
    or if ``wrap_model_call`` is unavailable, returns an empty list.

    Parameters
    ----------
    context : dict, optional
        Build context containing ``audit_manager`` and optionally
        ``agent_name`` and ``thread_id``.
    """
    ctx = context or {}
    audit_manager = ctx.get("audit_manager")
    agent_name = ctx.get("agent_name", "system")
    thread_id = ctx.get("thread_id")

    if audit_manager is None or not getattr(audit_manager, "enabled", False):
        logger.debug(
            "audit_log middleware disabled: no audit_manager or audit not enabled",
            extra={"event": "middleware.audit_log.disabled"},
        )
        return []

    try:
        from langchain.agents.middleware import wrap_model_call
    except Exception:
        logger.warning(
            "middleware disabled: name=audit_log reason=wrap_model_call_unavailable",
            extra={"event": "middleware.disabled", "middleware": "audit_log"},
        )
        return []

    @wrap_model_call
    def audit_log(request: Any, handler: Callable[[Any], Any]) -> Any:
        model_name = (
            getattr(getattr(request, "model", None), "model_name", None)
            or getattr(getattr(request, "model", None), "model", None)
            or "unknown"
        )

        start = time.time()
        try:
            response = handler(request)
            duration_ms = (time.time() - start) * 1000

            # Record successful call
            audit_manager.record_event(
                actor=agent_name,
                action="model.call",
                resource=f"model:{model_name}",
                result="success",
                detail={
                    "duration_ms": round(duration_ms, 2),
                    "thread_id": thread_id,
                },
            )
            return response

        except Exception as exc:
            duration_ms = (time.time() - start) * 1000

            # Record failed call
            audit_manager.record_event(
                actor=agent_name,
                action="model.call",
                resource=f"model:{model_name}",
                result="failure",
                detail={
                    "duration_ms": round(duration_ms, 2),
                    "thread_id": thread_id,
                    "error": str(exc),
                },
            )
            raise

    return audit_log
