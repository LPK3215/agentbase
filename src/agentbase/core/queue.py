"""Request queue for asynchronous agent task processing.

Provides a pluggable queue interface so long-running agent invocations
can be submitted asynchronously and results retrieved later.

Default: ``MemoryRequestQueue`` — zero-dependency, in-process.
Register custom implementations (Redis, RabbitMQ, etc.) with
``@register_queue_provider``.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Protocol, runtime_checkable


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    """A queued agent task."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    agent_name: str = ""
    message: str = ""
    thread_id: str | None = None
    status: TaskStatus = TaskStatus.PENDING
    result: dict[str, Any] | None = None
    error: str | None = None
    priority: int = 0  # higher = processed first
    retry_count: int = 0
    max_retries: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_name": self.agent_name,
            "message": self.message,
            "thread_id": self.thread_id,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "priority": self.priority,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "metadata": self.metadata,
        }


@runtime_checkable
class RequestQueue(Protocol):
    """Protocol for async task queues."""

    def submit(
        self,
        *,
        agent_name: str,
        message: str,
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        """Submit a new task to the queue."""
        ...

    def get_task(self, task_id: str) -> Task | None:
        """Retrieve a task by ID."""
        ...

    def list_tasks(self, *, agent_name: str | None = None, status: TaskStatus | None = None) -> list[Task]:
        """List tasks, optionally filtered."""
        ...

    def cancel(self, task_id: str) -> bool:
        """Cancel a pending task."""
        ...

    def update_task(self, task_id: str, **fields: Any) -> Task | None:
        """Update task fields (used by workers)."""
        ...


class MemoryRequestQueue:
    """In-process request queue.

    Tasks are stored in memory. A worker function processes them
    synchronously when ``process_one`` is called.

    Features:
    - Thread-safe via ``threading.Lock``
    - Priority support (higher ``priority`` value = processed first)
    - Automatic retry on failure (up to ``max_retries`` per task)
    - Dead-letter storage for permanently failed tasks
    - Statistics (pending/running/completed/failed counts)
    """

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._dead_letter: dict[str, Task] = {}
        self._lock = threading.Lock()

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
        task = Task(
            agent_name=agent_name,
            message=message,
            thread_id=thread_id,
            metadata=metadata or {},
            priority=priority,
            max_retries=max_retries,
        )
        with self._lock:
            self._tasks[task.id] = task
        return task

    def get_task(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id) or self._dead_letter.get(task_id)

    def list_tasks(self, *, agent_name: str | None = None, status: TaskStatus | None = None) -> list[Task]:
        with self._lock:
            tasks = list(self._tasks.values())
        if agent_name:
            tasks = [t for t in tasks if t.agent_name == agent_name]
        if status:
            tasks = [t for t in tasks if t.status == status]
        return sorted(tasks, key=lambda t: (-t.priority, t.created_at))

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status in {TaskStatus.PENDING, TaskStatus.RUNNING}:
                task.status = TaskStatus.CANCELLED
                task.finished_at = datetime.now(timezone.utc).isoformat()
                return True
        return False

    def update_task(self, task_id: str, **fields: Any) -> Task | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            for key, value in fields.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            return task

    def process_one(self, handler: Callable[[Task], dict[str, Any]]) -> Task | None:
        """Process the next pending task using ``handler``.

        Tasks are processed in priority order (highest first), then by
        creation time. If a task fails and ``retry_count < max_retries``,
        it is re-queued for another attempt. Otherwise it is moved to
        the dead-letter store.

        Returns the completed (or failed) task, or ``None`` if no pending.
        """
        with self._lock:
            # Find highest-priority pending task
            pending = [t for t in self._tasks.values() if t.status == TaskStatus.PENDING]
            if not pending:
                return None
            pending.sort(key=lambda t: (-t.priority, t.created_at))
            task = pending[0]
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now(timezone.utc).isoformat()

        try:
            result = handler(task)
            task.result = result
            task.status = TaskStatus.COMPLETED
        except Exception as exc:
            task.error = str(exc)
            task.retry_count += 1
            if task.retry_count <= task.max_retries:
                # Re-queue for retry (up to max_retries additional attempts)
                task.status = TaskStatus.PENDING
                task.started_at = None
            else:
                # Max retries exhausted — move to dead-letter
                task.status = TaskStatus.FAILED
                with self._lock:
                    self._dead_letter[task.id] = task
                    self._tasks.pop(task.id, None)
        finally:
            task.finished_at = datetime.now(timezone.utc).isoformat()
        return task

    def process_all(self, handler: Callable[[Task], dict[str, Any]]) -> list[Task]:
        """Process all pending tasks. Returns completed tasks."""
        results: list[Task] = []
        while True:
            task = self.process_one(handler)
            if task is None:
                break
            results.append(task)
        return results

    def stats(self) -> dict[str, int]:
        """Return queue statistics."""
        with self._lock:
            counts = {s.value: 0 for s in TaskStatus}
            for task in self._tasks.values():
                counts[task.status.value] = counts.get(task.status.value, 0) + 1
            counts["dead_letter"] = len(self._dead_letter)
            counts["total"] = len(self._tasks) + len(self._dead_letter)
            return counts

    def clear(self, *, include_completed: bool = True) -> int:
        """Remove completed/failed tasks. Returns count of removed tasks."""
        with self._lock:
            to_remove = []
            statuses = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
            if not include_completed:
                statuses = {TaskStatus.FAILED, TaskStatus.CANCELLED}
            for tid, task in self._tasks.items():
                if task.status in statuses:
                    to_remove.append(tid)
            for tid in to_remove:
                self._tasks.pop(tid, None)
            return len(to_remove)

    def get_dead_letter_tasks(self) -> list[Task]:
        """Return all tasks in the dead-letter store."""
        with self._lock:
            return list(self._dead_letter.values())

    def requeue(self, task_id: str) -> bool:
        """Re-queue a dead-letter task for another attempt."""
        with self._lock:
            task = self._dead_letter.pop(task_id, None)
            if task is None:
                return False
            task.status = TaskStatus.PENDING
            task.retry_count = 0
            task.error = None
            task.started_at = None
            task.finished_at = None
            self._tasks[task.id] = task
            return True


class QueueRegistry:
    """Thread-safe registry for queue providers."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., RequestQueue]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        factory: Callable[..., RequestQueue],
        *,
        override: bool = False,
    ) -> None:
        key = name.lower()
        with self._lock:
            if key in self._factories and not override:
                raise ValueError(f"Queue provider '{name}' is already registered")
            self._factories[key] = factory

    def create(self, name: str, **kwargs: Any) -> RequestQueue:
        key = name.lower()
        with self._lock:
            if key not in self._factories:
                available = ", ".join(sorted(self._factories.keys())) or "<empty>"
                raise KeyError(f"Unknown queue provider: {name}. Available: {available}")
            factory = self._factories[key]
        return factory(**kwargs)

    def has(self, name: str) -> bool:
        with self._lock:
            return name.lower() in self._factories

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._factories.keys())

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._factories)

    def unregister(self, name: str) -> bool:
        """Remove a factory. Returns True if removed."""
        key = name.lower()
        with self._lock:
            if key not in self._factories:
                return False
            self._factories.pop(key, None)
            return True


# Global registry
queue_registry = QueueRegistry()
queue_registry.register("memory", MemoryRequestQueue)


def register_queue_provider(name: str, *, override: bool = False):
    """Decorator to register a queue provider."""

    def decorator(factory: Callable[..., RequestQueue]):
        queue_registry.register(name, factory, override=override)
        return factory

    return decorator


# ---------------------------------------------------------------------------
# Redis-backed queue (persistent, multi-process safe)
# ---------------------------------------------------------------------------

class RedisRequestQueue:
    """Redis-backed persistent request queue.

    Tasks are stored as JSON in Redis. Survives process restarts.
    Supports multiple workers consuming from the same queue.

    Requires ``redis`` package. Install with: ``pip install redis``.

    Usage::

        from agentbase.core.queue import RedisRequestQueue

        queue = RedisRequestQueue(host="localhost", port=6379, db=0)
        task = queue.submit(agent_name="default", message="hello")
        result = queue.process_one(handler)

    Or via config::

        queue:
          provider: redis
          options:
            host: localhost
            port: 6379
            db: 0
    """

    PREFIX = "agentbase:task:"
    INDEX_KEY = "agentbase:tasks:index"
    PENDING_KEY = "agentbase:tasks:pending"

    def __init__(
        self,
        *,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        url: str | None = None,
    ) -> None:
        self._url = url
        self._host = host
        self._port = port
        self._db = db
        self._password = password
        self._client = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import redis
            except ImportError as exc:
                raise ImportError(
                    "Redis queue requires the redis package. "
                    "Install with: pip install redis"
                ) from exc
            if self._url:
                self._client = redis.from_url(self._url, decode_responses=True)
            else:
                self._client = redis.Redis(
                    host=self._host,
                    port=self._port,
                    db=self._db,
                    password=self._password,
                    decode_responses=True,
                )
        return self._client

    def _serialize(self, task: Task) -> str:
        import json
        return json.dumps(task.to_dict())

    def _deserialize(self, data: str) -> Task:
        import json
        d = json.loads(data)
        d["status"] = TaskStatus(d["status"])
        return Task(**d)

    def submit(
        self,
        *,
        agent_name: str,
        message: str,
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        task = Task(
            agent_name=agent_name,
            message=message,
            thread_id=thread_id,
            metadata=metadata or {},
        )
        client = self._get_client()
        client.set(self.PREFIX + task.id, self._serialize(task))
        client.lpush(self.PENDING_KEY, task.id)
        client.sadd(self.INDEX_KEY, task.id)
        return task

    def get_task(self, task_id: str) -> Task | None:
        client = self._get_client()
        data = client.get(self.PREFIX + task_id)
        if data is None:
            return None
        return self._deserialize(data)

    def list_tasks(self, *, agent_name: str | None = None, status: TaskStatus | None = None) -> list[Task]:
        client = self._get_client()
        task_ids = client.smembers(self.INDEX_KEY)
        tasks: list[Task] = []
        for tid in task_ids:
            data = client.get(self.PREFIX + tid)
            if data:
                task = self._deserialize(data)
                if agent_name and task.agent_name != agent_name:
                    continue
                if status and task.status != status:
                    continue
                tasks.append(task)
        return sorted(tasks, key=lambda t: t.created_at)

    def cancel(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if task and task.status in {TaskStatus.PENDING, TaskStatus.RUNNING}:
            client = self._get_client()
            task.status = TaskStatus.CANCELLED
            task.finished_at = datetime.now(timezone.utc).isoformat()
            client.set(self.PREFIX + task_id, self._serialize(task))
            client.lrem(self.PENDING_KEY, 0, task_id)
            return True
        return False

    def update_task(self, task_id: str, **fields: Any) -> Task | None:
        task = self.get_task(task_id)
        if task is None:
            return None
        for key, value in fields.items():
            if hasattr(task, key):
                setattr(task, key, value)
        client = self._get_client()
        client.set(self.PREFIX + task_id, self._serialize(task))
        return task

    def process_one(self, handler: Callable[[Task], dict[str, Any]]) -> Task | None:
        """Atomically pop one pending task and process it."""

        client = self._get_client()
        # BRPOPLPUSH-style: pop from pending, move to processing
        task_id = client.rpop(self.PENDING_KEY)
        if task_id is None:
            return None

        data = client.get(self.PREFIX + task_id)
        if data is None:
            return None

        task = self._deserialize(data)
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(timezone.utc).isoformat()
        client.set(self.PREFIX + task_id, self._serialize(task))

        try:
            result = handler(task)
            task.result = result
            task.status = TaskStatus.COMPLETED
        except Exception as exc:
            task.error = str(exc)
            task.status = TaskStatus.FAILED
        finally:
            task.finished_at = datetime.now(timezone.utc).isoformat()
            client.set(self.PREFIX + task_id, self._serialize(task))

        return task

    def process_all(self, handler: Callable[[Task], dict[str, Any]]) -> list[Task]:
        results: list[Task] = []
        while True:
            task = self.process_one(handler)
            if task is None:
                break
            results.append(task)
        return results


# Register Redis queue if the package is available
try:
    import redis  # noqa: F401
    queue_registry.register("redis", RedisRequestQueue, override=True)
except ImportError:
    pass
