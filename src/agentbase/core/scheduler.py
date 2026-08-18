"""Scheduled task service — cron / interval driven agent execution.

Provides a pluggable scheduler that allows the platform to:
- Create recurring tasks that invoke a specific agent on a fixed interval
  (``interval_seconds``) or a 5-field cron expression (``cron_expr``)
- Pause / resume / manually trigger tasks
- Track execution history (runs) with status, duration, and error details
- Aggregate statistics (enabled/paused counts, success/failure rates)

Pluggable backends:
- ``InMemoryScheduleProvider`` (default) — zero-config, thread-safe, background
  tick thread + worker pool for concurrent execution
- ``NullScheduleProvider`` — no-op, zero overhead when disabled
- Register custom providers with ``@register_schedule_provider("name")``

Cron expressions use the standard 5-field format
(``minute hour day-of-month month day-of-week``) with support for
``*``, ``*/n``, ``a-b``, ``a-b/n``, and comma-separated lists. Day-of-week
runs 0-7 where both 0 and 7 mean Sunday. When both day-of-month and
day-of-week are restricted, a time matches if *either* field matches
(Vixie cron semantics).

Usage::

    from agentbase.core.scheduler import ScheduleManager

    manager = ScheduleManager(provider="memory", enabled=True)

    task = manager.create_task(
        name="daily-report",
        agent_name="researcher",
        message="Generate the daily summary report.",
        schedule_type="cron",
        cron_expr="0 8 * * *",          # every day at 08:00
    )

    # Wire the executor that runs when a task fires
    manager.set_executor(lambda task: invoke_agent(task.agent_name, task.message))
"""
from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from agentbase.runtime.errors import RegistryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)

Executor = Callable[["ScheduledTask"], Any]


def _now() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _now().isoformat()


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO 8601 timestamp; returns None for empty/invalid values."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


# ---------------------------------------------------------------------------
# Cron expression parsing (pure Python, 5-field Vixie-style)
# ---------------------------------------------------------------------------

_FIELD_RANGES = (
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day of month
    (1, 12),   # month
    (0, 7),    # day of week (0 and 7 = Sunday)
)


def parse_cron_field(expr: str, lo: int, hi: int) -> set[int]:
    """Parse a single cron field into the set of matching integers.

    Supports ``*``, ``*/n``, ``a``, ``a-b``, ``a-b/n``, and comma-separated
    combinations of these.

    Args:
        expr: The cron field expression (e.g. ``"*/15"``, ``"1-5"``, ``"1,15"``).
        lo: Minimum allowed value (inclusive).
        hi: Maximum allowed value (inclusive).

    Returns:
        Sorted set of matching integer values.

    Raises:
        RegistryError: If the expression is empty, malformed, or out of range.
    """
    expr = expr.strip()
    if not expr:
        raise RegistryError("Empty cron field")

    values: set[int] = set()
    for part in expr.split(","):
        part = part.strip()
        if not part:
            raise RegistryError(f"Malformed cron field: {expr!r}")
        step = 1
        if "/" in part:
            base, _, step_s = part.partition("/")
            try:
                step = int(step_s)
            except ValueError:
                raise RegistryError(f"Invalid cron step in {part!r}") from None
            if step <= 0:
                raise RegistryError(f"Cron step must be positive: {part!r}")
        else:
            base = part

        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, _, b = base.partition("-")
            try:
                start, end = int(a), int(b)
            except ValueError:
                raise RegistryError(f"Invalid cron range: {base!r}") from None
        else:
            try:
                start = end = int(base)
            except ValueError:
                raise RegistryError(f"Invalid cron value: {base!r}") from None

        if start < lo or end > hi or start > end:
            raise RegistryError(
                f"Cron value out of range [{lo}-{hi}]: {part!r} in {expr!r}"
            )
        values.update(range(start, end + 1, step))
    return values


def parse_cron(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    """Parse a full 5-field cron expression.

    Returns:
        Tuple of ``(minutes, hours, days_of_month, months, days_of_week)``
        with day-of-week normalised to 0-6 (7 → 0, both mean Sunday).

    Raises:
        RegistryError: If the expression does not have exactly 5 fields or
            any field is invalid.
    """
    fields = expr.strip().split()
    if len(fields) != 5:
        raise RegistryError(
            f"Cron expression must have exactly 5 fields (min hour dom mon dow): {expr!r}"
        )
    minutes = parse_cron_field(fields[0], *_FIELD_RANGES[0])
    hours = parse_cron_field(fields[1], *_FIELD_RANGES[1])
    doms = parse_cron_field(fields[2], *_FIELD_RANGES[2])
    months = parse_cron_field(fields[3], *_FIELD_RANGES[3])
    dows = parse_cron_field(fields[4], *_FIELD_RANGES[4])
    if 7 in dows:
        dows.add(0)
        dows.discard(7)
    return minutes, hours, doms, months, dows


def next_cron_time(expr: str, after: datetime) -> datetime:
    """Compute the next fire time strictly after ``after``.

    Args:
        expr: 5-field cron expression.
        after: Reference datetime (aware or naive UTC).

    Returns:
        The next matching datetime (timezone-aware UTC).

    Raises:
        RegistryError: If the expression is invalid or no match exists within
            4 years (e.g. Feb 30).
    """
    minutes, hours, doms, months, dows = parse_cron(expr)
    dom_star = doms == set(range(1, 32))
    dow_star = dows == set(range(7))

    if after.tzinfo is None:
        after = after.replace(tzinfo=UTC)
    t = after.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)

    def day_ok(dt: datetime) -> bool:
        # Vixie semantics — if both dom and dow are restricted, match when
        # EITHER matches; otherwise both must match (one is `*`).
        dom_ok = dt.day in doms
        # datetime.weekday(): Mon=0..Sun=6 → cron dow: Sun=0..Sat=6
        dow_ok = ((dt.weekday() + 1) % 7) in dows
        if not dom_star and not dow_star:
            return dom_ok or dow_ok
        return dom_ok and dow_ok

    # Hierarchical search budgets: months (4y) → days (per month) → hours →
    # minutes. Counters only guarantee termination; matches return directly.
    for _ in range(49):  # month budget
        if t.month not in months:
            if t.month == 12:
                t = t.replace(year=t.year + 1, month=1, day=1, hour=0, minute=0)
            else:
                t = t.replace(month=t.month + 1, day=1, hour=0, minute=0)
            continue
        for _ in range(63):  # day budget (rolls into next month → month loop)
            if t.month not in months:
                break
            if not day_ok(t):
                t = (t + timedelta(days=1)).replace(hour=0, minute=0)
                continue
            for _ in range(25):  # hour budget (rolls into next day → day loop)
                if t.hour not in hours:
                    t = (t + timedelta(hours=1)).replace(minute=0)
                    continue
                for _ in range(61):  # minute budget (rolls into next hour)
                    if t.minute not in minutes:
                        t += timedelta(minutes=1)
                        continue
                    return t
                continue
            continue
        continue
    raise RegistryError(f"Cron expression never matches within 4 years: {expr!r}")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ScheduledTask:
    """A recurring task that invokes an agent on a schedule.

    Attributes:
        name: Human-readable task name (unique per manager instance).
        agent_name: Name of the agent to invoke when the task fires.
        message: The input message passed to the agent.
        schedule_type: ``"interval"`` (fixed seconds) or ``"cron"`` (5-field).
        interval_seconds: Fixed interval in seconds (interval tasks only).
        cron_expr: 5-field cron expression (cron tasks only).
        enabled: Whether the task is active (paused tasks keep their config).
        thread_id: Optional checkpoint thread for conversation continuity.
            Empty string means a fresh thread per run.
        next_run_at: ISO 8601 UTC timestamp of the next scheduled fire.
        last_run_at: ISO 8601 UTC timestamp of the last fired run.
        last_status: Status of the most recent run (success/failed/skipped).
        run_count: Total number of executed runs.
        error_count: Total number of failed runs.
        metadata: Arbitrary key-value metadata for extensibility.
        created_at: ISO 8601 UTC timestamp (auto-set).
        updated_at: ISO 8601 UTC timestamp (auto-set).
        id: Auto-assigned task ID.
    """

    name: str
    agent_name: str
    message: str = ""
    schedule_type: str = "interval"
    interval_seconds: float = 3600.0
    cron_expr: str = ""
    enabled: bool = True
    thread_id: str = ""
    next_run_at: str = ""
    last_run_at: str = ""
    last_status: str = ""
    run_count: int = 0
    error_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = ""
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:16]
        self.schedule_type = self.schedule_type.strip().lower()

    def compute_next_run(self, after: datetime | None = None) -> datetime:
        """Compute and return the next fire time after ``after`` (UTC now).

        Raises:
            RegistryError: For cron tasks with an invalid expression or an
                interval task with a non-positive interval.
        """
        base = after or _now()
        if self.schedule_type == "cron":
            return next_cron_time(self.cron_expr, base)
        if self.schedule_type == "interval":
            if self.interval_seconds <= 0:
                raise RegistryError("interval_seconds must be positive")
            return base + timedelta(seconds=self.interval_seconds)
        raise RegistryError(f"Unknown schedule_type: {self.schedule_type!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "agent_name": self.agent_name,
            "message": self.message,
            "schedule_type": self.schedule_type,
            "interval_seconds": self.interval_seconds,
            "cron_expr": self.cron_expr,
            "enabled": self.enabled,
            "thread_id": self.thread_id,
            "next_run_at": self.next_run_at,
            "last_run_at": self.last_run_at,
            "last_status": self.last_status,
            "run_count": self.run_count,
            "error_count": self.error_count,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class TaskRun:
    """Execution record of a single scheduled task fire.

    Attributes:
        task_id: ID of the scheduled task.
        status: ``running`` / ``success`` / ``failed`` / ``skipped``.
        started_at: ISO 8601 UTC timestamp.
        finished_at: ISO 8601 UTC timestamp (empty while running).
        duration_ms: Execution duration in milliseconds.
        error: Error message for failed runs.
        output_summary: Short summary of the executor's return value.
        trigger: ``"schedule"`` (automatic) or ``"manual"`` (API trigger).
        id: Auto-assigned run ID.
    """

    task_id: str
    status: str = "running"
    started_at: str = field(default_factory=_now_iso)
    finished_at: str = ""
    duration_ms: float = 0.0
    error: str = ""
    output_summary: str = ""
    trigger: str = "schedule"
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:16]

    @property
    def is_finished(self) -> bool:
        return self.status in ("success", "failed", "skipped")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "output_summary": self.output_summary,
            "trigger": self.trigger,
        }


@dataclass
class ScheduleFilter:
    """Filter criteria for querying scheduled tasks."""

    agent_name: str | None = None
    enabled: bool | None = None
    name: str | None = None
    limit: int = 100
    offset: int = 0


@dataclass
class RunFilter:
    """Filter criteria for querying task runs."""

    task_id: str | None = None
    status: str | None = None
    trigger: str | None = None
    since: str | None = None
    until: str | None = None
    limit: int = 100
    offset: int = 0


@dataclass
class ScheduleStats:
    """Aggregate scheduler statistics."""

    total: int = 0
    enabled: int = 0
    paused: int = 0
    by_agent: dict[str, int] = field(default_factory=dict)
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    running: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "enabled": self.enabled,
            "paused": self.paused,
            "by_agent": dict(self.by_agent),
            "total_runs": self.total_runs,
            "successful_runs": self.successful_runs,
            "failed_runs": self.failed_runs,
            "running": self.running,
        }


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class ScheduleProvider(Protocol):
    """Protocol for scheduler backends.

    Implementations must be thread-safe. ``start``/``stop`` control the
    scheduling loop lifecycle.
    """

    def create_task(self, task: ScheduledTask) -> ScheduledTask:
        """Store a scheduled task. Returns the task with ID and next_run_at."""
        ...

    def get_task(self, task_id: str) -> ScheduledTask | None:
        """Get a task by ID. Returns None if not found."""
        ...

    def list_tasks(self, filter: ScheduleFilter | None = None) -> list[ScheduledTask]:
        """Query tasks matching the filter."""
        ...

    def update_task(self, task_id: str, changes: dict[str, Any]) -> ScheduledTask | None:
        """Update fields on an existing task. Returns updated or None."""
        ...

    def delete_task(self, task_id: str) -> bool:
        """Delete a task (and its runs). Returns True if deleted."""
        ...

    def pause_task(self, task_id: str) -> ScheduledTask | None:
        """Disable a task (keeps config). Returns updated or None."""
        ...

    def resume_task(self, task_id: str) -> ScheduledTask | None:
        """Re-enable a task and recompute next_run_at. Returns updated or None."""
        ...

    def trigger_task(self, task_id: str) -> TaskRun | None:
        """Fire a task immediately (manual trigger). Returns the run or None."""
        ...

    def list_runs(self, filter: RunFilter | None = None) -> list[TaskRun]:
        """Query execution history matching the filter."""
        ...

    def get_stats(self) -> ScheduleStats:
        """Get aggregate statistics."""
        ...

    def set_executor(self, executor: Executor | None) -> None:
        """Set (or clear) the callable invoked when a task fires."""
        ...

    def start(self) -> None:
        """Start the scheduling loop."""
        ...

    def stop(self) -> None:
        """Stop the scheduling loop and release resources."""
        ...


# ---------------------------------------------------------------------------
# Null provider (no-op, zero overhead)
# ---------------------------------------------------------------------------

class NullScheduleProvider:
    """No-op scheduler provider — all operations return empty/None.

    Used when scheduling is disabled (``scheduler.enabled=false``).
    """

    def create_task(self, task: ScheduledTask) -> ScheduledTask:
        return task

    def get_task(self, task_id: str) -> ScheduledTask | None:
        return None

    def list_tasks(self, filter: ScheduleFilter | None = None) -> list[ScheduledTask]:
        return []

    def update_task(
        self, task_id: str, changes: dict[str, Any]
    ) -> ScheduledTask | None:
        return None

    def delete_task(self, task_id: str) -> bool:
        return False

    def pause_task(self, task_id: str) -> ScheduledTask | None:
        return None

    def resume_task(self, task_id: str) -> ScheduledTask | None:
        return None

    def trigger_task(self, task_id: str) -> TaskRun | None:
        return None

    def list_runs(self, filter: RunFilter | None = None) -> list[TaskRun]:
        return []

    def get_stats(self) -> ScheduleStats:
        return ScheduleStats()

    def set_executor(self, executor: Executor | None) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


# ---------------------------------------------------------------------------
# In-memory provider (default, zero-config)
# ---------------------------------------------------------------------------

class InMemoryScheduleProvider:
    """In-memory scheduler — thread-safe, background tick loop + worker pool.

    Stores tasks and runs in memory (lost on process restart). The tick
    thread wakes every ``tick_seconds`` and dispatches due tasks to a
    ``ThreadPoolExecutor`` so slow agent calls never block the loop.
    """

    def __init__(
        self,
        *,
        max_tasks: int = 1_000,
        max_runs: int = 10_000,
        tick_seconds: float = 1.0,
        max_workers: int = 4,
        autostart: bool = True,
        executor: Executor | None = None,
    ) -> None:
        if tick_seconds <= 0:
            raise RegistryError("tick_seconds must be positive")
        if max_workers < 1:
            raise RegistryError("max_workers must be >= 1")
        self._lock = threading.RLock()
        self._tasks: dict[str, ScheduledTask] = {}
        self._runs: dict[str, TaskRun] = {}
        self._max_tasks = max_tasks
        self._max_runs = max_runs
        self._tick_seconds = tick_seconds
        self._executor: Executor | None = executor
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="agentbase-scheduler"
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._running_lock = threading.Lock()
        self._started = False
        if autostart:
            self.start()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Start the background tick loop (idempotent)."""
        with self._running_lock:
            if self._started:
                return
            self._started = True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop, name="agentbase-scheduler-tick", daemon=True
            )
            self._thread.start()
            logger.info(
                "Scheduler started: tick=%.3fs", self._tick_seconds,
                extra={"event": "scheduler.started", "tick_seconds": self._tick_seconds},
            )

    def stop(self) -> None:
        """Stop the tick loop and shut down the worker pool."""
        with self._running_lock:
            if not self._started:
                return
            self._started = False
            self._stop_event.set()
            thread = self._thread
            self._thread = None
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=self._tick_seconds * 5 + 5.0)
        self._pool.shutdown(wait=False)
        logger.info("Scheduler stopped", extra={"event": "scheduler.stopped"})

    def close(self) -> None:
        self.stop()
        with self._lock:
            self._tasks.clear()
            self._runs.clear()

    def __del__(self) -> None:  # best-effort cleanup
        try:
            self.stop()
        except Exception:  # noqa: BLE001, S110 — best-effort cleanup
            pass

    # -- loop / dispatch ---------------------------------------------------

    def _loop(self) -> None:
        while not self._stop_event.wait(self._tick_seconds):
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 — loop must never die
                logger.error(
                    "Scheduler tick failed: %s", exc,
                    extra={"event": "scheduler.tick_error", "error": str(exc)},
                )

    def tick(self) -> int:
        """Dispatch all due tasks once. Returns the number dispatched.

        Mostly useful for tests and manual driving of the scheduler.
        """
        now = _now()
        due: list[ScheduledTask] = []
        with self._lock:
            for task in self._tasks.values():
                if not task.enabled:
                    continue
                nxt = _parse_iso(task.next_run_at)
                if nxt is not None and nxt <= now:
                    try:
                        task.next_run_at = task.compute_next_run(now).isoformat()
                    except RegistryError as exc:
                        # Invalid schedule after an update: pause the task so
                        # it doesn't spin, and record the reason.
                        task.enabled = False
                        task.last_status = "failed"
                        logger.error(
                            "Scheduled task %s has invalid schedule, paused: %s",
                            task.id, exc,
                            extra={"event": "scheduler.invalid_schedule", "task_id": task.id},
                        )
                        continue
                    task.last_run_at = _now_iso()
                    due.append(task)
        for task in due:
            self._pool.submit(self._execute, task, "schedule")
        if due:
            logger.info(
                "Scheduler dispatched %d due task(s)", len(due),
                extra={"event": "scheduler.dispatch", "count": len(due)},
            )
        return len(due)

    def _execute(self, task: ScheduledTask, trigger: str) -> TaskRun:
        """Execute a task once and record the run. Runs in a worker thread."""
        run = TaskRun(task_id=task.id, trigger=trigger)
        started = time.monotonic()
        with self._lock:
            self._store_run(run)
        executor = self._executor
        if executor is None:
            run.status = "skipped"
            run.error = "no executor configured"
            self._finish_run(run, task, started)
            return run
        try:
            result = executor(task)
            run.status = "success"
            run.output_summary = _summarize(result)
        except Exception as exc:  # noqa: BLE001 — executor errors are data
            run.status = "failed"
            run.error = str(exc)
            logger.error(
                "Scheduled task %s failed: %s", task.id, exc,
                extra={
                    "event": "scheduler.task_failed",
                    "task_id": task.id,
                    "trigger": trigger,
                    "error": str(exc),
                },
            )
        self._finish_run(run, task, started)
        return run

    def _finish_run(self, run: TaskRun, task: ScheduledTask, started: float) -> None:
        run.finished_at = _now_iso()
        run.duration_ms = round((time.monotonic() - started) * 1000, 3)
        with self._lock:
            existing = self._tasks.get(task.id)
            if existing is not None:
                existing.run_count += 1
                existing.last_status = run.status
                if run.status == "failed":
                    existing.error_count += 1
                existing.updated_at = _now_iso()
            if run.id in self._runs:
                self._runs[run.id] = run

    def _store_run(self, run: TaskRun) -> None:
        # FIFO eviction when capacity reached
        if len(self._runs) >= self._max_runs:
            oldest = min(self._runs, key=lambda k: self._runs[k].started_at)
            del self._runs[oldest]
        self._runs[run.id] = run

    # -- CRUD --------------------------------------------------------------

    def create_task(self, task: ScheduledTask) -> ScheduledTask:
        with self._lock:
            # Enforce unique names
            for existing in self._tasks.values():
                if existing.name == task.name:
                    raise RegistryError(f"Scheduled task name already exists: {task.name!r}")
            # FIFO eviction when capacity reached
            if len(self._tasks) >= self._max_tasks:
                oldest = min(self._tasks, key=lambda k: self._tasks[k].created_at)
                del self._tasks[oldest]
            if not task.next_run_at:
                task.next_run_at = task.compute_next_run().isoformat()
            task.updated_at = _now_iso()
            self._tasks[task.id] = task
            logger.info(
                "Scheduled task created: id=%s name=%s agent=%s type=%s next=%s",
                task.id, task.name, task.agent_name, task.schedule_type, task.next_run_at,
                extra={
                    "event": "scheduler.task_created",
                    "task_id": task.id,
                    "task_name": task.name,
                    "agent_name": task.agent_name,
                },
            )
            return task

    def get_task(self, task_id: str) -> ScheduledTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self, filter: ScheduleFilter | None = None) -> list[ScheduledTask]:
        with self._lock:
            tasks = list(self._tasks.values())
        return _apply_schedule_filter(tasks, filter)

    def update_task(self, task_id: str, changes: dict[str, Any]) -> ScheduledTask | None:
        with self._lock:
            existing = self._tasks.get(task_id)
            if existing is None:
                return None
            schedule_changed = False
            for key, value in changes.items():
                if key in ("id", "created_at", "run_count", "error_count", "last_run_at"):
                    continue
                if hasattr(existing, key):
                    if key in ("schedule_type", "interval_seconds", "cron_expr"):
                        schedule_changed = True
                    setattr(existing, key, value)
            if schedule_changed:
                # Validate the new schedule by computing the next fire time.
                existing.next_run_at = existing.compute_next_run().isoformat()
            existing.updated_at = _now_iso()
            logger.info(
                "Scheduled task updated: id=%s fields=%s",
                task_id, list(changes.keys()),
                extra={"event": "scheduler.task_updated", "task_id": task_id},
            )
            return existing

    def delete_task(self, task_id: str) -> bool:
        with self._lock:
            if task_id not in self._tasks:
                return False
            del self._tasks[task_id]
            for run_id in [r for r, run in self._runs.items() if run.task_id == task_id]:
                del self._runs[run_id]
            logger.info(
                "Scheduled task deleted: id=%s", task_id,
                extra={"event": "scheduler.task_deleted", "task_id": task_id},
            )
            return True

    def pause_task(self, task_id: str) -> ScheduledTask | None:
        with self._lock:
            existing = self._tasks.get(task_id)
            if existing is None:
                return None
            existing.enabled = False
            existing.updated_at = _now_iso()
            logger.info(
                "Scheduled task paused: id=%s", task_id,
                extra={"event": "scheduler.task_paused", "task_id": task_id},
            )
            return existing

    def resume_task(self, task_id: str) -> ScheduledTask | None:
        with self._lock:
            existing = self._tasks.get(task_id)
            if existing is None:
                return None
            existing.enabled = True
            # Recompute next run so a long-paused task doesn't fire in a burst.
            existing.next_run_at = existing.compute_next_run().isoformat()
            existing.updated_at = _now_iso()
            logger.info(
                "Scheduled task resumed: id=%s next=%s", task_id, existing.next_run_at,
                extra={"event": "scheduler.task_resumed", "task_id": task_id},
            )
            return existing

    def trigger_task(self, task_id: str) -> TaskRun | None:
        """Fire a task immediately in a worker thread. Works on paused tasks."""
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            return None
        run = TaskRun(task_id=task_id, trigger="manual")
        with self._lock:
            self._store_run(run)
        task.last_run_at = _now_iso()
        self._pool.submit(self._execute_manual, task_id, run)
        logger.info(
            "Scheduled task manually triggered: id=%s", task_id,
            extra={"event": "scheduler.task_triggered", "task_id": task_id},
        )
        return run

    def _execute_manual(self, task_id: str, run: TaskRun) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            run.status = "failed"
            run.error = f"Task deleted before manual trigger executed: {task_id}"
            run.finished_at = _now_iso()
            return
        started = time.monotonic()
        executor = self._executor
        if executor is None:
            run.status = "skipped"
            run.error = "no executor configured"
        else:
            try:
                result = executor(task)
                run.status = "success"
                run.output_summary = _summarize(result)
            except Exception as exc:  # noqa: BLE001
                run.status = "failed"
                run.error = str(exc)
        self._finish_run(run, task, started)

    def list_runs(self, filter: RunFilter | None = None) -> list[TaskRun]:
        with self._lock:
            runs = list(self._runs.values())
        return _apply_run_filter(runs, filter)

    def get_stats(self) -> ScheduleStats:
        with self._lock:
            tasks = list(self._tasks.values())
            runs = list(self._runs.values())
        by_agent: dict[str, int] = {}
        for t in tasks:
            by_agent[t.agent_name] = by_agent.get(t.agent_name, 0) + 1
        return ScheduleStats(
            total=len(tasks),
            enabled=sum(1 for t in tasks if t.enabled),
            paused=sum(1 for t in tasks if not t.enabled),
            by_agent=by_agent,
            total_runs=len(runs),
            successful_runs=sum(1 for r in runs if r.status == "success"),
            failed_runs=sum(1 for r in runs if r.status == "failed"),
            running=sum(1 for r in runs if r.status == "running"),
        )

    def set_executor(self, executor: Executor | None) -> None:
        self._executor = executor


def _summarize(result: Any) -> str:
    """Build a short summary string from an executor's return value."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result[:500]
    if isinstance(result, dict):
        for key in ("output_text", "output", "result", "text"):
            if key in result and result[key] is not None:
                return _summarize(result[key])
        return str(result)[:500]
    return str(result)[:500]


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def _apply_schedule_filter(
    tasks: list[ScheduledTask], flt: ScheduleFilter | None
) -> list[ScheduledTask]:
    if flt is None:
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)
    result: list[ScheduledTask] = []
    for t in tasks:
        if flt.agent_name is not None and t.agent_name != flt.agent_name:
            continue
        if flt.enabled is not None and t.enabled != flt.enabled:
            continue
        if flt.name is not None and flt.name not in t.name:
            continue
        result.append(t)
    result.sort(key=lambda t: t.created_at, reverse=True)
    if flt.offset > 0:
        result = result[flt.offset:]
    if flt.limit > 0:
        result = result[:flt.limit]
    return result


def _apply_run_filter(runs: list[TaskRun], flt: RunFilter | None) -> list[TaskRun]:
    if flt is None:
        return sorted(runs, key=lambda r: r.started_at, reverse=True)
    result: list[TaskRun] = []
    for r in runs:
        if flt.task_id is not None and r.task_id != flt.task_id:
            continue
        if flt.status is not None and r.status != flt.status:
            continue
        if flt.trigger is not None and r.trigger != flt.trigger:
            continue
        if flt.since is not None and r.started_at < flt.since:
            continue
        if flt.until is not None and r.started_at >= flt.until:
            continue
        result.append(r)
    result.sort(key=lambda r: r.started_at, reverse=True)
    if flt.offset > 0:
        result = result[flt.offset:]
    if flt.limit > 0:
        result = result[:flt.limit]
    return result


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class ScheduleRegistry:
    """Thread-safe registry for scheduler providers."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., ScheduleProvider]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        factory: Callable[..., ScheduleProvider],
        *,
        override: bool = False,
    ) -> None:
        key = name.strip().lower()
        with self._lock:
            if not key:
                raise RegistryError("Cannot register empty schedule provider name")
            if key in self._factories and not override:
                raise RegistryError(f"Schedule provider already registered: {key}")
            self._factories[key] = factory

    def create(self, name: str, **kwargs: Any) -> ScheduleProvider:
        key = name.strip().lower()
        with self._lock:
            if key not in self._factories:
                available = ", ".join(sorted(self._factories)) or "<empty>"
                raise RegistryError(
                    f"Unknown schedule provider: {key}. Available: {available}"
                )
            factory = self._factories[key]
        return factory(**kwargs)

    def has(self, name: str) -> bool:
        with self._lock:
            return name.strip().lower() in self._factories

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._factories.keys())

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._factories)

    def unregister(self, name: str) -> bool:
        key = name.strip().lower()
        with self._lock:
            if key not in self._factories:
                return False
            self._factories.pop(key, None)
            return True


# Global singleton
schedule_registry = ScheduleRegistry()

# Register defaults
schedule_registry.register("null", NullScheduleProvider)
schedule_registry.register("memory", InMemoryScheduleProvider)


def register_schedule_provider(name: str, *, override: bool = False):
    """Decorator: register a scheduler provider class.

    Usage::

        @register_schedule_provider("redis")
        class RedisScheduleProvider:
            def create_task(self, task): ...
    """
    def decorator(factory: Callable[..., ScheduleProvider]):
        schedule_registry.register(name, factory, override=override)
        return factory
    return decorator


# ---------------------------------------------------------------------------
# Manager — high-level facade
# ---------------------------------------------------------------------------

class ScheduleManager:
    """High-level scheduled task manager.

    Wraps a ``ScheduleProvider`` for task CRUD, lifecycle control (pause /
    resume / trigger), execution history, and statistics. When
    ``enabled=False``, uses ``NullScheduleProvider`` (no-op).

    Usage::

        manager = ScheduleManager(provider="memory", enabled=True)
        manager.set_executor(my_executor)
        manager.create_task(
            name="heartbeat",
            agent_name="coder",
            message="Check build status.",
            schedule_type="interval",
            interval_seconds=60,
        )
    """

    def __init__(
        self,
        *,
        provider: str = "null",
        enabled: bool = False,
        **provider_kwargs: Any,
    ) -> None:
        self._enabled = enabled
        if not enabled:
            self._provider: ScheduleProvider = NullScheduleProvider()
        else:
            self._provider = schedule_registry.create(provider, **provider_kwargs)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def provider(self) -> ScheduleProvider:
        return self._provider

    def set_executor(self, executor: Executor | None) -> None:
        """Set (or clear) the executor invoked when tasks fire."""
        self._provider.set_executor(executor)

    def start(self) -> None:
        """Start the scheduling loop (idempotent)."""
        self._provider.start()

    def stop(self) -> None:
        """Stop the scheduling loop."""
        self._provider.stop()

    def create_task(
        self,
        *,
        name: str,
        agent_name: str,
        message: str = "",
        schedule_type: str = "interval",
        interval_seconds: float = 3600.0,
        cron_expr: str = "",
        enabled: bool = True,
        thread_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ScheduledTask:
        """Create a new scheduled task.

        Args:
            name: Unique task name (required).
            agent_name: Agent to invoke (required).
            message: Input message for the agent.
            schedule_type: ``"interval"`` or ``"cron"``.
            interval_seconds: Interval in seconds (interval tasks, > 0).
            cron_expr: 5-field cron expression (cron tasks).
            enabled: Whether the task starts active.
            thread_id: Optional checkpoint thread for continuity.
            metadata: Extensible metadata.

        Returns:
            The stored ``ScheduledTask`` with ID and ``next_run_at`` set.

        Raises:
            RegistryError: If required fields are missing, the name already
                exists, or the schedule spec is invalid.
        """
        if not name:
            raise RegistryError("name is required for scheduled task")
        if not agent_name:
            raise RegistryError("agent_name is required for scheduled task")
        schedule_type = schedule_type.strip().lower()
        if schedule_type == "cron":
            if not cron_expr:
                raise RegistryError("cron_expr is required when schedule_type='cron'")
            # Validate the expression eagerly.
            next_cron_time(cron_expr, _now())
        elif schedule_type == "interval":
            if interval_seconds <= 0:
                raise RegistryError("interval_seconds must be positive")
        else:
            raise RegistryError(
                f"Invalid schedule_type: {schedule_type!r} (use 'interval' or 'cron')"
            )

        task = ScheduledTask(
            name=name,
            agent_name=agent_name,
            message=message,
            schedule_type=schedule_type,
            interval_seconds=interval_seconds,
            cron_expr=cron_expr,
            enabled=enabled,
            thread_id=thread_id,
            metadata=metadata or {},
        )
        return self._provider.create_task(task)

    def get_task(self, task_id: str) -> ScheduledTask | None:
        """Get a task by ID."""
        return self._provider.get_task(task_id)

    def list_tasks(
        self,
        *,
        agent_name: str | None = None,
        enabled: bool | None = None,
        name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScheduledTask]:
        """Query tasks with filters. Returns empty list when disabled."""
        flt = ScheduleFilter(
            agent_name=agent_name,
            enabled=enabled,
            name=name,
            limit=limit,
            offset=offset,
        )
        return self._provider.list_tasks(flt)

    def update_task(
        self,
        task_id: str,
        *,
        name: str | None = None,
        agent_name: str | None = None,
        message: str | None = None,
        schedule_type: str | None = None,
        interval_seconds: float | None = None,
        cron_expr: str | None = None,
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ScheduledTask | None:
        """Update an existing task. Changing schedule fields recomputes
        ``next_run_at``. Returns the updated task or None if not found.

        Raises:
            RegistryError: If the resulting schedule spec is invalid.
        """
        changes: dict[str, Any] = {}
        if name is not None:
            changes["name"] = name
        if agent_name is not None:
            changes["agent_name"] = agent_name
        if message is not None:
            changes["message"] = message
        if schedule_type is not None:
            changes["schedule_type"] = schedule_type.strip().lower()
        if interval_seconds is not None:
            changes["interval_seconds"] = interval_seconds
        if cron_expr is not None:
            changes["cron_expr"] = cron_expr
        if thread_id is not None:
            changes["thread_id"] = thread_id
        if metadata is not None:
            changes["metadata"] = metadata
        if not changes:
            return self._provider.get_task(task_id)
        # Eagerly validate the resulting schedule spec.
        current = self._provider.get_task(task_id)
        if current is not None:
            merged_type = changes.get("schedule_type", current.schedule_type)
            merged_interval = changes.get("interval_seconds", current.interval_seconds)
            merged_cron = changes.get("cron_expr", current.cron_expr)
            if merged_type == "cron":
                if not merged_cron:
                    raise RegistryError("cron_expr is required when schedule_type='cron'")
                next_cron_time(merged_cron, _now())
            elif merged_type == "interval":
                if merged_interval <= 0:
                    raise RegistryError("interval_seconds must be positive")
            else:
                raise RegistryError(
                    f"Invalid schedule_type: {merged_type!r} (use 'interval' or 'cron')"
                )
        return self._provider.update_task(task_id, changes)

    def delete_task(self, task_id: str) -> bool:
        """Delete a task and its run history. Returns True if deleted."""
        return self._provider.delete_task(task_id)

    def pause_task(self, task_id: str) -> ScheduledTask | None:
        """Pause a task. Returns the updated task or None if not found."""
        return self._provider.pause_task(task_id)

    def resume_task(self, task_id: str) -> ScheduledTask | None:
        """Resume a paused task (recomputes ``next_run_at``)."""
        return self._provider.resume_task(task_id)

    def trigger_task(self, task_id: str) -> TaskRun | None:
        """Manually trigger a task immediately. Works on paused tasks."""
        return self._provider.trigger_task(task_id)

    def list_runs(
        self,
        *,
        task_id: str | None = None,
        status: str | None = None,
        trigger: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TaskRun]:
        """Query execution history with filters."""
        flt = RunFilter(
            task_id=task_id,
            status=status,
            trigger=trigger,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )
        return self._provider.list_runs(flt)

    def get_stats(self) -> ScheduleStats:
        """Get aggregate scheduler statistics. Zero-values when disabled."""
        return self._provider.get_stats()

    def close(self) -> None:
        """Stop the loop and release resources."""
        self._provider.stop()


# ---------------------------------------------------------------------------
# Singleton management
# ---------------------------------------------------------------------------

_schedule_manager: ScheduleManager | None = None
_schedule_manager_lock = threading.Lock()


def get_schedule_manager() -> ScheduleManager:
    """Get the global ScheduleManager singleton.

    Raises ``RuntimeError`` if not initialised — call ``set_schedule_manager``
    first (typically during application bootstrap).
    """
    if _schedule_manager is None:
        with _schedule_manager_lock:
            if _schedule_manager is None:
                raise RuntimeError(
                    "ScheduleManager not initialised. "
                    "Call set_schedule_manager() first."
                )
    return _schedule_manager  # type: ignore[return-value]


def set_schedule_manager(manager: ScheduleManager) -> None:
    """Set the global ScheduleManager singleton."""
    global _schedule_manager
    with _schedule_manager_lock:
        _schedule_manager = manager


def reset_schedule_manager() -> None:
    """Reset the global ScheduleManager singleton (for testing)."""
    global _schedule_manager
    with _schedule_manager_lock:
        _schedule_manager = None
