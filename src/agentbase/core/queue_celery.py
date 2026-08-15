"""Celery-backed request queue for distributed agent task processing.

Provides a ``CeleryRequestQueue`` that implements the same ``RequestQueue``
Protocol as ``MemoryRequestQueue`` and ``RedisRequestQueue``, but delegates
task execution to Celery workers via a broker (RabbitMQ/Redis).

This enables multi-process, multi-node distributed task processing for
AgentBase.

Requires ``celery`` package. Install with::

    pip install agentbase[celery]

Usage via config::

    queue:
      provider: celery
      options:
        broker_url: redis://localhost:6379/0
        result_backend: redis://localhost:6379/1

Usage programmatically::

    from agentbase.core.queue_celery import CeleryRequestQueue

    queue = CeleryRequestQueue(broker_url="redis://localhost:6379/0")
    task = queue.submit(agent_name="default", message="hello")
    result = queue.get_task(task.id)
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from agentbase.core.queue import (
    Task,
    TaskStatus,
    register_queue_provider,
)
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


class CeleryRequestQueue:
    """Celery-backed distributed request queue.

    Tasks are submitted to a Celery broker (RabbitMQ or Redis) and
    processed by Celery workers. Task state is tracked via Celery's
    ``AsyncResult`` and a local index for listing.

    The actual task handler is registered separately via
    ``register_handler()``. In a worker process, call::

        queue.register_handler(my_handler)
        queue.worker_start()

    Thread-safe via ``threading.Lock`` for the local task index.
    """

    def __init__(
        self,
        *,
        broker_url: str = "redis://localhost:6379/0",
        result_backend: str | None = None,
        app_name: str = "agentbase",
        **kwargs: Any,
    ) -> None:
        try:
            from celery import Celery
        except ImportError as exc:
            raise ImportError(
                "Celery queue requires the celery package. "
                "Install with: pip install agentbase[celery]"
            ) from exc

        self._broker_url = broker_url
        self._result_backend = result_backend or broker_url
        self._app_name = app_name

        # Create Celery app
        self._celery_app = Celery(
            app_name,
            broker=broker_url,
            backend=self._result_backend,
        )

        # Configure serialization
        self._celery_app.conf.update(
            task_serializer="json",
            result_serializer="json",
            accept_content=["json"],
            timezone="UTC",
            enable_utc=True,
            **kwargs,
        )

        # Register the task handler placeholder
        self._handler: Callable[[Task], dict[str, Any]] | None = None
        self._lock = threading.Lock()

        # Local index of submitted tasks (for listing and status tracking)
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()

        # Register the Celery task that will execute our handler
        self._register_celery_task()

        logger.info(
            "CeleryRequestQueue initialized: broker=%s",
            broker_url,
            extra={
                "event": "queue.celery.init",
                "broker_url": broker_url,
            },
        )

    def _register_celery_task(self) -> None:
        """Register the Celery task function that wraps our handler."""
        @self._celery_app.task(name=f"{self._app_name}.process_task", bind=True)
        def _process_task(self_celery, task_data: str) -> dict[str, Any]:
            """Celery task that deserializes Task, calls handler, returns result."""
            from celery import current_task

            task_dict = json.loads(task_data)
            task = _task_from_dict(task_dict)

            # The handler is stored on the Celery app (shared across workers)
            handler = getattr(current_task.app, "_agentbase_handler", None)
            if handler is None:
                raise RuntimeError(
                    "No handler registered. Call queue.register_handler() "
                    "in the worker process before starting the worker."
                )

            # Update task status to RUNNING
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now(timezone.utc).isoformat()

            result = handler(task)
            return result

        self._celery_task = _process_task

    def register_handler(self, handler: Callable[[Task], dict[str, Any]]) -> None:
        """Register the task handler function.

        This must be called in both the submitting process (to register
        the Celery task) and in the worker process (to provide the
        actual handler logic).

        Parameters
        ----------
        handler : Callable[[Task], dict[str, Any]]
            Function that processes a Task and returns a result dict.
        """
        self._handler = handler
        # Store on the Celery app so workers can access it
        self._celery_app._agentbase_handler = handler  # type: ignore[attr-defined]
        logger.info("Task handler registered for Celery queue")

    def submit(
        self,
        *,
        agent_name: str,
        message: str,
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        priority: int = 0,
        max_retries: int = 0,
    ) -> Task:
        """Submit a task to the Celery queue."""
        task = Task(
            agent_name=agent_name,
            message=message,
            thread_id=thread_id,
            metadata=metadata or {},
            priority=priority,
            max_retries=max_retries,
        )

        # Serialize task for Celery
        task_data = json.dumps(task.to_dict())

        # Submit to Celery with priority
        # Celery priority is 0-9 (higher = higher priority)
        celery_priority = min(max(priority, 0), 9)

        async_result = self._celery_task.apply_async(
            args=[task_data],
            task_id=task.id,
            priority=celery_priority,
        )

        # Store the AsyncResult for later retrieval
        task.metadata["celery_task_id"] = async_result.id

        with self._lock:
            self._tasks[task.id] = task

        logger.debug(
            "Celery: submitted task %s for agent '%s'",
            task.id,
            agent_name,
        )
        return task

    def get_task(self, task_id: str) -> Task | None:
        """Retrieve a task by ID, checking Celery's AsyncResult for status."""
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            return None

        # Get fresh state from Celery's AsyncResult
        async_result = self._celery_app.AsyncResult(task_id)
        state = self._map_celery_state(async_result.state)

        # Update task status from Celery state
        task.status = TaskStatus(state)

        # Update result/error if completed or failed
        if async_result.successful():
            result = async_result.result
            task.result = result if isinstance(result, dict) else {"value": result}
        elif async_result.failed():
            task.error = str(async_result.result) if async_result.result else "Task failed"

        if state in {"completed", "failed", "cancelled"} and task.finished_at is None:
            task.finished_at = datetime.now(timezone.utc).isoformat()

        return task

    def list_tasks(
        self,
        *,
        agent_name: str | None = None,
        status: TaskStatus | None = None,
    ) -> list[Task]:
        """List tasks from the local index.

        Note: In a distributed setup, the local index only tracks tasks
        submitted from this process. For a global view, use the Celery
        result backend directly.
        """
        with self._lock:
            task_ids = list(self._tasks.keys())

        tasks: list[Task] = []
        for tid in task_ids:
            task = self.get_task(tid)
            if task is None:
                continue
            if agent_name and task.agent_name != agent_name:
                continue
            if status and task.status != status:
                continue
            tasks.append(task)

        return sorted(tasks, key=lambda t: (-t.priority, t.created_at))

    def cancel(self, task_id: str) -> bool:
        """Cancel a pending/running task via Celery's revoke."""
        with self._lock:
            if task_id not in self._tasks:
                return False

        async_result = self._celery_app.AsyncResult(task_id)

        # Check if task is still pending/running
        if async_result.state in {"PENDING", "STARTED", "RETRY"}:
            self._celery_app.control.revoke(task_id, terminate=True)
            logger.info("Celery: revoked task %s", task_id)
            return True
        return False

    def update_task(self, task_id: str, **fields: Any) -> Task | None:
        """Update task fields locally.

        Note: This only updates the local view. In a distributed setup,
        task state is managed by Celery workers.
        """
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            return None

        for key, value in fields.items():
            if hasattr(task, key):
                setattr(task, key, value)

        return task

    def process_one(self, handler: Callable[[Task], dict[str, Any]]) -> Task | None:
        """Process one task synchronously (for compatibility).

        In Celery mode, this delegates to the handler directly rather
        than going through the broker. Useful for testing.
        """
        # Ensure handler is registered
        if self._handler is None:
            self.register_handler(handler)

        # Get the next pending task from local index
        with self._lock:
            if not self._tasks:
                return None
            # Find a pending task
            for tid, task in self._tasks.items():
                if task.status == TaskStatus.PENDING:
                    task_id = tid
                    break
            else:
                return None

        # Get task data
        task = self._tasks.get(task_id)
        if task is None:
            return None

        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(timezone.utc).isoformat()

        try:
            result = handler(task)
            task.result = result
            task.status = TaskStatus.COMPLETED
        except Exception as exc:
            task.error = str(exc)
            task.status = TaskStatus.FAILED
        finally:
            task.finished_at = datetime.now(timezone.utc).isoformat()

        return task

    def process_all(self, handler: Callable[[Task], dict[str, Any]]) -> list[Task]:
        """Process all pending tasks (for compatibility)."""
        results: list[Task] = []
        while True:
            task = self.process_one(handler)
            if task is None:
                break
            results.append(task)
        return results

    def stats(self) -> dict[str, int]:
        """Return queue statistics based on local task index."""
        with self._lock:
            task_ids = list(self._tasks.keys())

        counts: dict[str, int] = {s.value: 0 for s in TaskStatus}
        counts["total"] = len(task_ids)

        for tid in task_ids:
            async_result = self._celery_app.AsyncResult(tid)
            state = self._map_celery_state(async_result.state)
            counts[state] = counts.get(state, 0) + 1

        return counts

    def clear(self, *, include_completed: bool = True) -> int:
        """Remove completed/failed tasks from the local index."""
        with self._lock:
            to_remove: list[str] = []
            for tid, task in self._tasks.items():
                async_result = self._celery_app.AsyncResult(tid)
                state = self._map_celery_state(async_result.state)

                if state in {"completed", "failed", "cancelled"}:
                    if include_completed or state != "completed":
                        to_remove.append(tid)

            for tid in to_remove:
                self._tasks.pop(tid, None)

            return len(to_remove)

    def worker_start(self, **kwargs: Any) -> None:
        """Start the Celery worker (blocking).

        This should be called in a worker process, not the main process.
        """
        if self._handler is None:
            raise RuntimeError(
                "No handler registered. Call register_handler() first."
            )

        # Start Celery worker
        self._celery_app.worker_main(
            ["worker", "--loglevel=info", *kwargs.get("argv", [])]
        )

    def health_check(self) -> bool:
        """Check if the Celery broker is alive."""
        try:
            conn = self._celery_app.connection_for_write()
            conn.ensure_connection(max_retries=1, timeout=1.0)
            conn.close()
            return True
        except Exception:
            return False

    def close(self) -> None:
        """Close the Celery connection."""
        try:
            self._celery_app.close()
        except Exception:
            pass

    @staticmethod
    def _map_celery_state(celery_state: str) -> str:
        """Map Celery state to TaskStatus value."""
        mapping = {
            "PENDING": "pending",
            "STARTED": "running",
            "SUCCESS": "completed",
            "FAILURE": "failed",
            "REVOKED": "cancelled",
            "RETRY": "pending",
        }
        return mapping.get(celery_state, "pending")

    def _build_task_from_async_result(
        self,
        task_id: str,
        async_result: Any,
    ) -> Task:
        """Build a Task object from Celery's AsyncResult."""
        state = self._map_celery_state(async_result.state)

        # Try to get the original task data from the result
        result: dict[str, Any] | None = None
        error: str | None = None

        if async_result.successful():
            result = async_result.result if isinstance(async_result.result, dict) else {"value": async_result.result}
        elif async_result.failed():
            error = str(async_result.result) if async_result.result else "Task failed"

        # Build task from available info
        task = Task(
            id=task_id,
            status=TaskStatus(state),
            result=result,
            error=error,
            finished_at=datetime.now(timezone.utc).isoformat() if state in {"completed", "failed", "cancelled"} else None,
        )

        return task


def _task_from_dict(d: dict[str, Any]) -> Task:
    """Reconstruct a Task from a dict (for Celery worker deserialization)."""
    d["status"] = TaskStatus(d.get("status", "pending"))
    return Task(**d)


# Register Celery queue if the package is available
try:
    import celery  # noqa: F401
    register_queue_provider("celery", CeleryRequestQueue, override=True)
except ImportError:
    pass
