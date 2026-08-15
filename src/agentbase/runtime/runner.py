from __future__ import annotations

import threading
import time
from typing import Any, Iterator

from agentbase.config.schema import AppConfig
from agentbase.factories.agent_factory import AgentFactory
from agentbase.runtime.errors import ErrorCode, RuntimeExecutionError, _classify_error
from agentbase.runtime.events import EventType, RuntimeEvent
from agentbase.runtime.logging import get_logger
from agentbase.runtime.session import Session

logger = get_logger(__name__)


def _get_usage_manager() -> Any:
    """Get the UsageManager singleton if initialised, else None."""
    try:
        from agentbase.core.usage import get_usage_manager
        return get_usage_manager()
    except RuntimeError:
        return None
    except Exception:
        return None


def _get_webhook_manager() -> Any:
    """Get the WebhookManager singleton if initialised, else None."""
    try:
        from agentbase.core.webhook import get_webhook_manager
        return get_webhook_manager()
    except RuntimeError:
        return None
    except Exception:
        return None


def _get_conversation_manager() -> Any:
    """Get the ConversationManager singleton if initialised, else None."""
    try:
        from agentbase.core.conversation import get_conversation_manager
        return get_conversation_manager()
    except RuntimeError:
        return None
    except Exception:
        return None


def _record_conversation(
    *,
    agent_name: str,
    thread_id: str,
    message: str,
    result: Any,
    metadata: dict[str, Any] | None,
    duration_ms: float,
) -> None:
    """Record a conversation if the ConversationManager is available.

    Fire-and-forget — errors are logged but never propagated.
    """
    conv_mgr = _get_conversation_manager()
    if conv_mgr is not None and conv_mgr.enabled:
        try:
            from agentbase.core.conversation import extract_messages_from_result
            messages = extract_messages_from_result(result)
            # Prepend the user's input message
            messages.insert(0, {"role": "user", "content": message})
            conv_mgr.record_conversation(
                thread_id=thread_id,
                agent_name=agent_name,
                user_id=(metadata or {}).get("user_id", ""),
                messages=messages,
                metadata=metadata or {},
                duration_ms=duration_ms,
            )
        except Exception as exc:
            logger.warning(
                "Failed to record conversation: %s",
                exc,
                extra={"event": "conversation.record_failed", "thread_id": thread_id},
            )


def _dispatch_webhook(event: str, payload: dict[str, Any]) -> None:
    """Dispatch a webhook event if the WebhookManager is available.

    This is fire-and-forget — errors are logged but never propagated.
    """
    wh_mgr = _get_webhook_manager()
    if wh_mgr is not None and wh_mgr.enabled:
        try:
            wh_mgr.dispatch_event(event=event, payload=payload)
        except Exception as exc:
            logger.warning(
                "Failed to dispatch webhook event %s: %s",
                event,
                exc,
                extra={"event": "webhook.dispatch_failed", "event_type": event},
            )


def _message_content(message: Any) -> str:
    if message is None:
        return ""
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        content = message.get("content", "")
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item))
            return "".join(parts)
        return str(content)
    content = getattr(message, "content", None)
    if content is None:
        return str(message)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                text = getattr(item, "text", None)
                parts.append(str(text if text is not None else item))
        return "".join(parts)
    return str(content)


def _extract_final_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, dict):
        messages = result.get("messages")
        if isinstance(messages, list) and messages:
            return _message_content(messages[-1])
        return str(result)
    messages = getattr(result, "messages", None)
    if isinstance(messages, list) and messages:
        return _message_content(messages[-1])
    return str(result)


class AgentRunner:
    """Executes agent invoke / stream / resume operations.

    Features:
    - Concurrency control via ``threading.Semaphore`` — limits the number
      of concurrent agent invocations to prevent overwhelming the LLM API.
    - Active session tracking — all sessions are registered in the
      :class:`SessionRegistry` for observability and timeout enforcement.
    - Tracer integration — spans are created for invoke/stream/resume.
    - Structured logging with ``duration_ms`` and ``request_id``.
    """

    def __init__(
        self,
        *,
        factory: AgentFactory,
        app_config: AppConfig,
        max_concurrent: int | None = None,
    ) -> None:
        self.factory = factory
        self.app_config = app_config
        # Concurrency limiter — default to the configured max_concurrency
        max_conc = max_concurrent or app_config.runtime.max_concurrency
        self._semaphore = threading.Semaphore(max_conc)

    def _build_input(self, message: str) -> dict[str, Any]:
        return {
            "messages": [
                {
                    "role": "user",
                    "content": message,
                }
            ]
        }

    def invoke(
        self,
        *,
        agent: Any,
        agent_name: str,
        message: str,
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session = Session.create(agent_name=agent_name, thread_id=thread_id, metadata=metadata)
        config = session.runnable_config(self.app_config.runtime.recursion_limit)
        payload = self._build_input(message)

        request_id = session.request_id or "-"
        logger.info(
            "Invoke agent=%s thread_id=%s request_id=%s",
            agent_name,
            session.thread_id,
            request_id,
            extra={"event": "agent.invoke", "thread_id": session.thread_id, "agent": agent_name, "request_id": request_id},
        )

        # Trace the invocation if a tracer is available
        tracer = self._get_tracer()
        span = None
        if tracer is not None:
            span = tracer.start_span(
                f"agent.invoke:{agent_name}",
                agent=agent_name,
                thread_id=session.thread_id,
                request_id=request_id,
            )

        session.mark_running()
        start_time = time.time()
        try:
            with self._semaphore:
                result = agent.invoke(payload, config=config)
        except TypeError:
            try:
                with self._semaphore:
                    result = agent.invoke(payload, config)
            except Exception as exc:  # noqa: BLE001
                session.mark_failed()
                if span is not None:
                    span.finish(status="error", error=str(exc))
                raise RuntimeExecutionError(
                    f"invoke failed: {exc}",
                    code=ErrorCode.RT_INVOKE_FAILED,
                    detail={"agent": agent_name, "thread_id": session.thread_id},
                ) from exc
        except Exception as exc:  # noqa: BLE001
            session.mark_failed()
            if span is not None:
                span.finish(status="error", error=str(exc))
            raise RuntimeExecutionError(
                f"invoke failed: {exc}",
                code=ErrorCode.RT_INVOKE_FAILED,
                detail={"agent": agent_name, "thread_id": session.thread_id},
            ) from exc
        finally:
            duration_ms = (time.time() - start_time) * 1000
            if span is not None:
                span.set_attribute("duration_ms", duration_ms)
                span.finish()

        session.mark_completed()

        # Record token usage if the UsageManager is available
        usage_mgr = _get_usage_manager()
        if usage_mgr is not None and usage_mgr.enabled:
            try:
                from agentbase.core.usage import extract_usage_from_result
                usage = extract_usage_from_result(result)
                if usage["prompt_tokens"] or usage["completion_tokens"]:
                    usage_mgr.record(
                        agent=agent_name,
                        model=metadata.get("model", "") if metadata else "",
                        prompt_tokens=usage["prompt_tokens"],
                        completion_tokens=usage["completion_tokens"],
                        total_tokens=usage["total_tokens"],
                        thread_id=session.thread_id,
                        request_id=request_id,
                        duration_ms=duration_ms,
                    )
            except Exception as exc:
                logger.warning("Failed to record usage: %s", exc, extra={"event": "usage.record_failed"})

        # Dispatch webhook event for invoke completion
        _dispatch_webhook(
            "agent.invoke.completed",
            {
                "agent": agent_name,
                "thread_id": session.thread_id,
                "request_id": request_id,
                "duration_ms": duration_ms,
                "model": metadata.get("model", "") if metadata else "",
            },
        )

        # Record conversation history if the ConversationManager is available
        _record_conversation(
            agent_name=agent_name,
            thread_id=session.thread_id,
            message=message,
            result=result,
            metadata=metadata,
            duration_ms=duration_ms,
        )

        logger.info(
            "Invoke completed agent=%s thread_id=%s duration_ms=%.1f",
            agent_name,
            session.thread_id,
            duration_ms,
            extra={
                "event": "agent.invoke.done",
                "thread_id": session.thread_id,
                "agent": agent_name,
                "duration_ms": duration_ms,
            },
        )

        return {
            "thread_id": session.thread_id,
            "agent": agent_name,
            "result": result,
            "output_text": _extract_final_text(result),
        }

    def stream(
        self,
        *,
        agent: Any,
        agent_name: str,
        message: str,
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[RuntimeEvent]:
        session = Session.create(agent_name=agent_name, thread_id=thread_id, metadata=metadata)
        config = session.runnable_config(self.app_config.runtime.recursion_limit)
        payload = self._build_input(message)
        stream_modes = self.app_config.runtime.stream_modes

        request_id = session.request_id or "-"
        logger.info(
            "Stream start agent=%s thread_id=%s request_id=%s",
            agent_name,
            session.thread_id,
            request_id,
            extra={"event": "agent.stream", "thread_id": session.thread_id, "agent": agent_name},
        )

        # Trace the stream if a tracer is available
        tracer = self._get_tracer()
        span = None
        if tracer is not None:
            span = tracer.start_span(
                f"agent.stream:{agent_name}",
                agent=agent_name,
                thread_id=session.thread_id,
                request_id=request_id,
            )

        yield RuntimeEvent(
            type=EventType.RUN_STARTED,
            thread_id=session.thread_id,
            agent=agent_name,
            data={"message": message},
        )

        session.mark_running()
        start_time = time.time()
        try:
            try:
                event_iter = agent.stream(payload, config=config, stream_mode=stream_modes)
            except TypeError:
                try:
                    event_iter = agent.stream(payload, config=config)
                except TypeError:
                    event_iter = agent.stream(payload, config)
        except Exception as exc:  # noqa: BLE001
            session.mark_failed()
            if span is not None:
                span.finish(status="error", error=str(exc))
            yield RuntimeEvent(
                type=EventType.RUN_ERROR,
                thread_id=session.thread_id,
                agent=agent_name,
                data={"error": str(exc), "error_code": _classify_error(exc)},
            )
            raise RuntimeExecutionError(
                f"stream failed: {exc}",
                code=ErrorCode.RT_STREAM_FAILED,
            ) from exc

        final_text = ""
        interrupt_seen = False
        # Hold the semaphore for the entire iteration — not just iterator
        # creation.  ``agent.stream()`` returns a lazy iterator; the actual
        # LLM calls happen during ``for event in event_iter``.  If the
        # semaphore only wraps iterator creation, max_concurrency is
        # effectively bypassed for streaming.
        self._semaphore.acquire()
        try:
            for event in event_iter:
                normalized = self._normalize_event(event, thread_id=session.thread_id, agent_name=agent_name)
                if normalized.type == EventType.MESSAGE_FINAL:
                    final_text = str(normalized.data.get("text") or final_text)
                elif normalized.type == EventType.MESSAGE_DELTA:
                    chunk = str(normalized.data.get("text") or "")
                    if chunk:
                        final_text += chunk
                elif normalized.type == EventType.INTERRUPT:
                    interrupt_seen = True
                yield normalized
        except Exception as exc:  # noqa: BLE001
            session.mark_failed()
            if span is not None:
                span.finish(status="error", error=str(exc))
            yield RuntimeEvent(
                type=EventType.RUN_ERROR,
                thread_id=session.thread_id,
                agent=agent_name,
                data={"error": str(exc), "error_code": _classify_error(exc)},
            )
            raise RuntimeExecutionError(
                f"stream iteration failed: {exc}",
                code=ErrorCode.RT_STREAM_FAILED,
            ) from exc
        finally:
            self._semaphore.release()

        if not interrupt_seen:
            session.mark_completed()
            yield RuntimeEvent(
                type=EventType.RUN_FINISHED,
                thread_id=session.thread_id,
                agent=agent_name,
                data={"output_text": final_text},
            )

        duration_ms = (time.time() - start_time) * 1000
        if span is not None:
            span.set_attribute("duration_ms", duration_ms)
            span.finish()

        # Record token usage if the UsageManager is available
        usage_mgr = _get_usage_manager()
        if usage_mgr is not None and usage_mgr.enabled:
            try:
                from agentbase.core.usage import extract_usage_from_result
                usage = extract_usage_from_result(final_text)
                if usage["prompt_tokens"] or usage["completion_tokens"]:
                    usage_mgr.record(
                        agent=agent_name,
                        model="",
                        prompt_tokens=usage["prompt_tokens"],
                        completion_tokens=usage["completion_tokens"],
                        total_tokens=usage["total_tokens"],
                        thread_id=session.thread_id,
                        request_id=request_id,
                        duration_ms=duration_ms,
                    )
            except Exception as exc:
                logger.warning("Failed to record stream usage: %s", exc, extra={"event": "usage.record_failed"})

        # Dispatch webhook event for stream completion
        _dispatch_webhook(
            "agent.stream.completed",
            {
                "agent": agent_name,
                "thread_id": session.thread_id,
                "request_id": request_id,
                "duration_ms": duration_ms,
                "output_text": final_text[:500] if final_text else "",
            },
        )

        # Record conversation history if the ConversationManager is available
        _record_conversation(
            agent_name=agent_name,
            thread_id=session.thread_id,
            message=message,
            result={"messages": [{"role": "user", "content": message}, {"role": "assistant", "content": final_text}]},
            metadata=metadata,
            duration_ms=duration_ms,
        )

        logger.info(
            "Stream completed agent=%s thread_id=%s duration_ms=%.1f",
            agent_name,
            session.thread_id,
            duration_ms,
            extra={
                "event": "agent.stream.done",
                "thread_id": session.thread_id,
                "agent": agent_name,
                "duration_ms": duration_ms,
            },
        )

    def resume(
        self,
        *,
        agent: Any,
        agent_name: str,
        thread_id: str,
        decision: dict[str, Any] | Any,
    ) -> dict[str, Any]:
        self._check_thread_exists(thread_id)
        session = Session.create(agent_name=agent_name, thread_id=thread_id)
        config = session.runnable_config(self.app_config.runtime.recursion_limit)

        logger.info(
            "Resume agent=%s thread_id=%s",
            agent_name,
            session.thread_id,
            extra={"event": "agent.resume", "thread_id": session.thread_id, "agent": agent_name},
        )

        try:
            from langgraph.types import Command
        except Exception:
            Command = None  # type: ignore

        # Trace the resume if a tracer is available
        tracer = self._get_tracer()
        span = None
        if tracer is not None:
            span = tracer.start_span(
                f"agent.resume:{agent_name}",
                agent=agent_name,
                thread_id=session.thread_id,
            )

        start_time = time.time()
        session.mark_running()
        try:
            with self._semaphore:
                if Command is not None:
                    result = agent.invoke(Command(resume=decision), config=config)
                else:
                    result = agent.invoke({"resume": decision}, config=config)
        except Exception as exc:  # noqa: BLE001
            session.mark_failed()
            if span is not None:
                span.finish(status="error", error=str(exc))
            raise RuntimeExecutionError(
                f"resume failed: {exc}",
                code=ErrorCode.RT_RESUME_FAILED,
            ) from exc
        finally:
            duration_ms = (time.time() - start_time) * 1000
            if span is not None:
                span.set_attribute("duration_ms", duration_ms)
                span.finish()

        session.mark_completed()

        # Record token usage if the UsageManager is available
        usage_mgr = _get_usage_manager()
        if usage_mgr is not None and usage_mgr.enabled:
            try:
                from agentbase.core.usage import extract_usage_from_result
                usage = extract_usage_from_result(result)
                if usage["prompt_tokens"] or usage["completion_tokens"]:
                    usage_mgr.record(
                        agent=agent_name,
                        model="",
                        prompt_tokens=usage["prompt_tokens"],
                        completion_tokens=usage["completion_tokens"],
                        total_tokens=usage["total_tokens"],
                        thread_id=session.thread_id,
                        request_id=session.request_id or "-",
                        duration_ms=duration_ms,
                    )
            except Exception as exc:
                logger.warning("Failed to record resume usage: %s", exc, extra={"event": "usage.record_failed"})

        # Dispatch webhook event for resume completion
        _dispatch_webhook(
            "agent.resume.completed",
            {
                "agent": agent_name,
                "thread_id": session.thread_id,
                "request_id": session.request_id or "-",
                "duration_ms": duration_ms,
            },
        )

        # Record conversation history if the ConversationManager is available
        _record_conversation(
            agent_name=agent_name,
            thread_id=session.thread_id,
            message="(resume)",
            result=result,
            metadata=None,
            duration_ms=duration_ms,
        )

        logger.info(
            "Resume completed agent=%s thread_id=%s duration_ms=%.1f",
            agent_name,
            session.thread_id,
            duration_ms,
            extra={
                "event": "agent.resume.done",
                "thread_id": session.thread_id,
                "agent": agent_name,
                "duration_ms": duration_ms,
            },
        )

        return {
            "thread_id": session.thread_id,
            "agent": agent_name,
            "result": result,
            "output_text": _extract_final_text(result),
        }

    def _get_tracer(self) -> Any | None:
        """Get the tracer from the factory, if available."""
        try:
            return self.factory.tracer
        except Exception:
            return None

    def _check_thread_exists(self, thread_id: str) -> None:
        """Pre-validate that ``thread_id`` has a checkpoint record.

        For sync check pointers (sqlite, memory) we call ``get_tuple``
        directly.  For async-only checkers (postgres) we try to run the
        coroutine in a *new* event loop — but only when no loop is already
        running in the current thread (otherwise we'd hit
        ``RuntimeError: This event loop is already running``).  If the
        check cannot be performed we skip it rather than blocking the user.
        """
        try:
            checkpointer = self.factory.checkpointer
            config = {"configurable": {"thread_id": thread_id}}
            tuple_result = None
            if hasattr(checkpointer, "get_tuple"):
                tuple_result = checkpointer.get_tuple(config)
            elif hasattr(checkpointer, "aget_tuple"):
                import asyncio

                try:
                    asyncio.get_running_loop()
                    # A loop is already running — can't call run_until_complete
                    # from here.  Skip validation to avoid RuntimeError.
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    try:
                        tuple_result = loop.run_until_complete(checkpointer.aget_tuple(config))
                    finally:
                        loop.close()
            if tuple_result is None:
                raise RuntimeExecutionError("Session not found or already completed", code="AGENTBASE_RT_002")
        except RuntimeExecutionError:
            raise
        except Exception:
            pass

    def _normalize_event(self, event: Any, *, thread_id: str, agent_name: str) -> RuntimeEvent:
        # deepagents/langgraph stream payloads vary by mode and version.
        if isinstance(event, tuple) and len(event) == 2:
            mode, payload = event
            return self._from_mode_payload(mode=str(mode), payload=payload, thread_id=thread_id, agent_name=agent_name)

        if isinstance(event, dict):
            # messages mode often yields {"messages": [...]} or message chunk dicts
            if "messages" in event:
                messages = event.get("messages") or []
                text = _message_content(messages[-1]) if messages else ""
                return RuntimeEvent(
                    type=EventType.MESSAGE_FINAL,
                    thread_id=thread_id,
                    agent=agent_name,
                    data={"text": text, "raw": event},
                )
            if event.get("type") in {item.value for item in EventType}:
                return RuntimeEvent(
                    type=EventType(event["type"]),
                    thread_id=thread_id,
                    agent=agent_name,
                    data=event.get("data", event),
                )
            return RuntimeEvent(
                type=EventType.UPDATE,
                thread_id=thread_id,
                agent=agent_name,
                data={"raw": event},
            )

        # AI message chunk objects
        text = _message_content(event)
        if text:
            return RuntimeEvent(
                type=EventType.MESSAGE_DELTA,
                thread_id=thread_id,
                agent=agent_name,
                data={"text": text, "raw": repr(event)},
            )

        return RuntimeEvent(
            type=EventType.RAW,
            thread_id=thread_id,
            agent=agent_name,
            data={"raw": repr(event)},
        )

    def _from_mode_payload(
        self,
        *,
        mode: str,
        payload: Any,
        thread_id: str,
        agent_name: str,
    ) -> RuntimeEvent:
        mode_l = mode.lower()
        if mode_l in {"messages", "messages-tuple"}:
            if isinstance(payload, tuple) and payload:
                text = _message_content(payload[0])
            else:
                text = _message_content(payload)
            return RuntimeEvent(
                type=EventType.MESSAGE_DELTA,
                thread_id=thread_id,
                agent=agent_name,
                data={"text": text, "mode": mode},
            )

        if mode_l in {"updates", "values"}:
            if isinstance(payload, dict) and "interrupts" in payload:
                interrupts = payload.get("interrupts") or []
                reason = ""
                resume_point = ""
                if isinstance(interrupts, list) and interrupts:
                    first = interrupts[0]
                    if isinstance(first, dict):
                        reason = str(first.get("reason", first.get("value", "")))
                        resume_point = str(first.get("resume_point", first.get("ns", "")))
                    else:
                        reason = str(first)
                return RuntimeEvent(
                    type=EventType.INTERRUPT,
                    thread_id=thread_id,
                    agent=agent_name,
                    data={"reason": reason, "resume_point": resume_point, "thread_id": thread_id, "raw": payload},
                )
            if isinstance(payload, dict) and "messages" in payload:
                messages = payload.get("messages") or []
                text = _message_content(messages[-1]) if messages else ""
                return RuntimeEvent(
                    type=EventType.MESSAGE_FINAL if mode_l == "values" else EventType.UPDATE,
                    thread_id=thread_id,
                    agent=agent_name,
                    data={"text": text, "raw": payload, "mode": mode},
                )
            return RuntimeEvent(
                type=EventType.UPDATE,
                thread_id=thread_id,
                agent=agent_name,
                data={"raw": payload, "mode": mode},
            )

        if mode_l in {"events"}:
            event_name = getattr(payload, "event", None) or (payload.get("event") if isinstance(payload, dict) else None)
            name = str(event_name or "")
            if "tool" in name and "start" in name:
                et = EventType.TOOL_START
            elif "tool" in name and ("end" in name or "success" in name):
                et = EventType.TOOL_END
            else:
                et = EventType.RAW
            return RuntimeEvent(
                type=et,
                thread_id=thread_id,
                agent=agent_name,
                data={"raw": payload, "mode": mode},
            )

        return RuntimeEvent(
            type=EventType.RAW,
            thread_id=thread_id,
            agent=agent_name,
            data={"raw": payload, "mode": mode},
        )

    def get_stats(self) -> dict[str, Any]:
        """Return runtime statistics for observability.

        Includes session counts from the global SessionRegistry
        and concurrency limiter information.
        """
        from agentbase.runtime.session import get_session_registry

        registry = get_session_registry()
        session_counts = registry.count_by_status()
        return {
            "sessions": session_counts,
            "max_concurrency": self.app_config.runtime.max_concurrency,
            "recursion_limit": self.app_config.runtime.recursion_limit,
            "default_agent": self.app_config.runtime.default_agent,
        }
