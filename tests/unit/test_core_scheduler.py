"""Tests for the scheduled task service (core/scheduler.py).

Covers:
- Cron field/expression parsing (``*``, ``*/n``, ranges, lists, errors)
- ``next_cron_time`` — daily/hourly/step/weekly/day-of-month/Vixie dom-dow
- ``ScheduledTask`` data model + ``compute_next_run``
- ``TaskRun`` data model
- NullScheduleProvider — all no-op
- InMemoryScheduleProvider — CRUD, pause/resume, trigger, runs, stats,
  eviction, tick dispatch, executor success/failure/skip, background loop
- ScheduleRegistry + decorator
- ScheduleManager facade + validation
- Singleton get/set/reset
- Protocol compliance + concurrency
"""
from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from agentbase.core.scheduler import (
    InMemoryScheduleProvider,
    NullScheduleProvider,
    RunFilter,
    ScheduledTask,
    ScheduleFilter,
    ScheduleManager,
    ScheduleProvider,
    ScheduleRegistry,
    TaskRun,
    get_schedule_manager,
    next_cron_time,
    parse_cron,
    parse_cron_field,
    register_schedule_provider,
    reset_schedule_manager,
    schedule_registry,
    set_schedule_manager,
)
from agentbase.runtime.errors import RegistryError


def _utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# ---------------------------------------------------------------------------
# parse_cron_field
# ---------------------------------------------------------------------------

class TestParseCronField:
    def test_star(self):
        assert parse_cron_field("*", 0, 59) == set(range(60))

    def test_step(self):
        assert parse_cron_field("*/15", 0, 59) == {0, 15, 30, 45}

    def test_range(self):
        assert parse_cron_field("1-5", 0, 59) == {1, 2, 3, 4, 5}

    def test_range_with_step(self):
        assert parse_cron_field("0-30/10", 0, 59) == {0, 10, 20, 30}

    def test_list(self):
        assert parse_cron_field("1,15", 0, 59) == {1, 15}

    def test_list_mixed(self):
        assert parse_cron_field("1,5-7,*/30", 0, 59) == {0, 1, 5, 6, 7, 30}

    def test_single_value(self):
        assert parse_cron_field("7", 0, 59) == {7}

    def test_empty_raises(self):
        with pytest.raises(RegistryError):
            parse_cron_field("", 0, 59)

    def test_empty_part_raises(self):
        with pytest.raises(RegistryError):
            parse_cron_field("1,,2", 0, 59)

    def test_bad_step_raises(self):
        with pytest.raises(RegistryError):
            parse_cron_field("*/x", 0, 59)

    def test_zero_step_raises(self):
        with pytest.raises(RegistryError):
            parse_cron_field("*/0", 0, 59)

    def test_out_of_range_raises(self):
        with pytest.raises(RegistryError):
            parse_cron_field("60", 0, 59)
        with pytest.raises(RegistryError):
            parse_cron_field("-5", 0, 59)

    def test_inverted_range_raises(self):
        with pytest.raises(RegistryError):
            parse_cron_field("50-10", 0, 59)

    def test_non_numeric_raises(self):
        with pytest.raises(RegistryError):
            parse_cron_field("abc", 0, 59)


# ---------------------------------------------------------------------------
# parse_cron
# ---------------------------------------------------------------------------

class TestParseCron:
    def test_valid(self):
        minutes, hours, doms, months, dows = parse_cron("0 8 * * *")
        assert minutes == {0} and hours == {8}
        assert doms == set(range(1, 32))
        assert months == set(range(1, 13))
        assert dows == set(range(7))

    def test_dow_7_normalized_to_0(self):
        *_, dows = parse_cron("* * * * 7")
        assert 0 in dows and 7 not in dows

    def test_wrong_field_count_raises(self):
        with pytest.raises(RegistryError):
            parse_cron("* * * *")
        with pytest.raises(RegistryError):
            parse_cron("* * * * * *")

    def test_invalid_field_propagates(self):
        with pytest.raises(RegistryError):
            parse_cron("* * * 13 *")  # month 13 out of range


# ---------------------------------------------------------------------------
# next_cron_time
# ---------------------------------------------------------------------------

class TestNextCronTime:
    def test_daily(self):
        after = _utc(2026, 8, 18, 10, 0)
        nxt = next_cron_time("0 8 * * *", after)
        assert (nxt.year, nxt.month, nxt.day, nxt.hour, nxt.minute) == (2026, 8, 19, 8, 0)

    def test_daily_same_day_later(self):
        after = _utc(2026, 8, 18, 6, 0)
        nxt = next_cron_time("0 8 * * *", after)
        assert (nxt.day, nxt.hour, nxt.minute) == (18, 8, 0)

    def test_minute_step(self):
        after = _utc(2026, 8, 18, 10, 4)
        nxt = next_cron_time("*/15 * * * *", after)
        assert (nxt.hour, nxt.minute) == (10, 15)

    def test_hourly(self):
        after = _utc(2026, 8, 18, 10, 30)
        nxt = next_cron_time("0 * * * *", after)
        assert (nxt.hour, nxt.minute) == (11, 0)

    def test_weekly_monday(self):
        # 2026-08-18 is a Tuesday
        after = _utc(2026, 8, 18, 10, 0)
        nxt = next_cron_time("30 9 * * 1", after)
        assert nxt.weekday() == 0  # Monday
        assert (nxt.hour, nxt.minute) == (9, 30)
        assert nxt.day == 24  # next Monday

    def test_day_of_month(self):
        after = _utc(2026, 8, 18, 0, 0)
        nxt = next_cron_time("0 0 1 * *", after)
        assert (nxt.month, nxt.day) == (9, 1)

    def test_month_and_day(self):
        after = _utc(2026, 8, 18, 0, 0)
        nxt = next_cron_time("0 0 1 1 *", after)
        assert (nxt.year, nxt.month, nxt.day) == (2027, 1, 1)

    def test_vixie_dom_dow_either(self):
        # Both restricted: match EITHER dom=15 OR Friday
        after = _utc(2026, 8, 18, 0, 0)  # Tuesday
        nxt = next_cron_time("0 0 15 * 5", after)
        # 2026-08-21 is the first Friday after the 18th; the 15th already passed
        assert (nxt.month, nxt.day) == (8, 21)

    def test_naive_input_treated_as_utc(self):
        after = datetime(2026, 8, 18, 10, 0)  # naive  # noqa: DTZ001
        nxt = next_cron_time("0 8 * * *", after)
        assert nxt.tzinfo is not None
        assert (nxt.day, nxt.hour) == (19, 8)

    def test_strictly_after(self):
        after = _utc(2026, 8, 18, 10, 0)
        nxt = next_cron_time("0 10 * * *", after)
        assert nxt > after

    def test_never_matching_raises(self):
        with pytest.raises(RegistryError):
            next_cron_time("0 0 30 2 *", _utc(2026, 1, 1))  # Feb 30

    def test_leap_year_feb_29(self):
        after = _utc(2026, 1, 1)
        nxt = next_cron_time("0 0 29 2 *", after)
        assert (nxt.year, nxt.month, nxt.day) == (2028, 2, 29)

    def test_invalid_expr_raises(self):
        with pytest.raises(RegistryError):
            next_cron_time("not a cron", _utc(2026, 1, 1))


# ---------------------------------------------------------------------------
# ScheduledTask data model
# ---------------------------------------------------------------------------

class TestScheduledTask:
    def test_defaults_and_id(self):
        task = ScheduledTask(name="t", agent_name="a")
        assert task.id
        assert task.schedule_type == "interval"
        assert task.created_at
        assert task.metadata == {}

    def test_schedule_type_normalized(self):
        task = ScheduledTask(name="t", agent_name="a", schedule_type=" CRON ")
        assert task.schedule_type == "cron"

    def test_to_dict_roundtrip(self):
        task = ScheduledTask(name="t", agent_name="a", message="m", cron_expr="0 8 * * *")
        d = task.to_dict()
        assert d["name"] == "t"
        assert d["agent_name"] == "a"
        assert d["cron_expr"] == "0 8 * * *"
        assert "next_run_at" in d and "run_count" in d

    def test_compute_next_run_interval(self):
        task = ScheduledTask(name="t", agent_name="a", interval_seconds=90)
        after = _utc(2026, 8, 18, 10, 0)
        nxt = task.compute_next_run(after)
        assert nxt == after + timedelta(seconds=90)

    def test_compute_next_run_cron(self):
        task = ScheduledTask(
            name="t", agent_name="a", schedule_type="cron", cron_expr="0 8 * * *"
        )
        after = _utc(2026, 8, 18, 10, 0)
        assert task.compute_next_run(after) == _utc(2026, 8, 19, 8, 0)

    def test_compute_next_run_invalid_interval(self):
        task = ScheduledTask(name="t", agent_name="a", interval_seconds=0)
        with pytest.raises(RegistryError):
            task.compute_next_run()

    def test_compute_next_run_invalid_cron(self):
        task = ScheduledTask(
            name="t", agent_name="a", schedule_type="cron", cron_expr="bad"
        )
        with pytest.raises(RegistryError):
            task.compute_next_run()

    def test_compute_next_run_unknown_type(self):
        task = ScheduledTask(name="t", agent_name="a", schedule_type="weekly")
        with pytest.raises(RegistryError):
            task.compute_next_run()


class TestTaskRun:
    def test_defaults(self):
        run = TaskRun(task_id="x")
        assert run.id and run.status == "running"
        assert not run.is_finished

    def test_is_finished(self):
        for status in ("success", "failed", "skipped"):
            assert TaskRun(task_id="x", status=status).is_finished

    def test_to_dict(self):
        run = TaskRun(task_id="x", status="failed", error="boom")
        d = run.to_dict()
        assert d["task_id"] == "x" and d["status"] == "failed" and d["error"] == "boom"


# ---------------------------------------------------------------------------
# Null provider
# ---------------------------------------------------------------------------

class TestNullScheduleProvider:
    def test_all_noop(self):
        p = NullScheduleProvider()
        task = ScheduledTask(name="t", agent_name="a")
        assert p.create_task(task) is task
        assert p.get_task("x") is None
        assert p.list_tasks() == []
        assert p.update_task("x", {}) is None
        assert p.delete_task("x") is False
        assert p.pause_task("x") is None
        assert p.resume_task("x") is None
        assert p.trigger_task("x") is None
        assert p.list_runs() == []
        assert p.get_stats().total == 0
        p.set_executor(None)
        p.start()
        p.stop()  # no error


# ---------------------------------------------------------------------------
# In-memory provider
# ---------------------------------------------------------------------------

class TestInMemoryProviderCRUD:
    def test_create_sets_next_run(self):
        p = InMemoryScheduleProvider(autostart=False)
        task = p.create_task(ScheduledTask(name="t", agent_name="a", interval_seconds=60))
        assert task.next_run_at
        assert task.id in {t.id for t in p.list_tasks()}
        p.stop()

    def test_create_duplicate_name_raises(self):
        p = InMemoryScheduleProvider(autostart=False)
        p.create_task(ScheduledTask(name="t", agent_name="a"))
        with pytest.raises(RegistryError):
            p.create_task(ScheduledTask(name="t", agent_name="b"))
        p.stop()

    def test_fifo_task_eviction(self):
        p = InMemoryScheduleProvider(max_tasks=2, autostart=False)
        t1 = p.create_task(ScheduledTask(name="t1", agent_name="a"))
        t2 = p.create_task(ScheduledTask(name="t2", agent_name="a"))
        t3 = p.create_task(ScheduledTask(name="t3", agent_name="a"))
        ids = {t.id for t in p.list_tasks()}
        assert t3.id in ids and t1.id not in ids and t2.id in ids
        p.stop()

    def test_get_missing(self):
        p = InMemoryScheduleProvider(autostart=False)
        assert p.get_task("nope") is None
        p.stop()

    def test_update_fields(self):
        p = InMemoryScheduleProvider(autostart=False)
        task = p.create_task(ScheduledTask(name="t", agent_name="a", message="old"))
        updated = p.update_task(task.id, {"message": "new"})
        assert updated.message == "new"
        assert updated.updated_at
        p.stop()

    def test_update_schedule_recomputes_next_run(self):
        p = InMemoryScheduleProvider(autostart=False)
        task = p.create_task(ScheduledTask(name="t", agent_name="a", interval_seconds=60))
        before = task.next_run_at
        time.sleep(0.01)
        updated = p.update_task(task.id, {"interval_seconds": 120})
        assert updated.next_run_at != before
        p.stop()

    def test_update_protected_fields_ignored(self):
        p = InMemoryScheduleProvider(autostart=False)
        task = p.create_task(ScheduledTask(name="t", agent_name="a"))
        updated = p.update_task(task.id, {"id": "hack", "run_count": 99})
        assert updated.id == task.id and updated.run_count == 0
        p.stop()

    def test_update_missing_returns_none(self):
        p = InMemoryScheduleProvider(autostart=False)
        assert p.update_task("nope", {"message": "x"}) is None
        p.stop()

    def test_delete_removes_runs(self):
        p = InMemoryScheduleProvider(autostart=False)
        p.set_executor(lambda t: "ok")
        task = p.create_task(ScheduledTask(name="t", agent_name="a"))
        p.trigger_task(task.id)
        time.sleep(0.2)
        assert p.list_runs(RunFilter(task_id=task.id))
        assert p.delete_task(task.id) is True
        assert p.list_runs(RunFilter(task_id=task.id)) == []
        assert p.delete_task(task.id) is False
        p.stop()

    def test_list_filter(self):
        p = InMemoryScheduleProvider(autostart=False)
        p.create_task(ScheduledTask(name="alpha", agent_name="coder"))
        p.create_task(ScheduledTask(name="beta", agent_name="researcher"))
        assert len(p.list_tasks(ScheduleFilter(agent_name="coder"))) == 1
        assert len(p.list_tasks(ScheduleFilter(enabled=True))) == 2
        assert len(p.list_tasks(ScheduleFilter(name="alph"))) == 1
        assert len(p.list_tasks(ScheduleFilter(limit=1))) == 1
        p.stop()


class TestInMemoryProviderLifecycle:
    def test_pause_resume(self):
        p = InMemoryScheduleProvider(autostart=False)
        task = p.create_task(ScheduledTask(name="t", agent_name="a", interval_seconds=60))
        paused = p.pause_task(task.id)
        assert paused.enabled is False
        resumed = p.resume_task(task.id)
        assert resumed.enabled is True
        assert resumed.next_run_at  # recomputed
        p.stop()

    def test_pause_missing(self):
        p = InMemoryScheduleProvider(autostart=False)
        assert p.pause_task("nope") is None
        assert p.resume_task("nope") is None
        p.stop()

    def test_paused_task_not_dispatched(self):
        p = InMemoryScheduleProvider(autostart=False)
        task = p.create_task(ScheduledTask(name="t", agent_name="a", interval_seconds=60))
        # Force the task to be due
        task.next_run_at = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        p.pause_task(task.id)
        assert p.tick() == 0
        p.stop()

    def test_tick_dispatches_due_task(self):
        p = InMemoryScheduleProvider(autostart=False)
        p.set_executor(lambda t: "done")
        task = p.create_task(ScheduledTask(name="t", agent_name="a", interval_seconds=60))
        task.next_run_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        dispatched = p.tick()
        assert dispatched == 1
        # next_run_at advanced into the future
        assert datetime.fromisoformat(task.next_run_at) > datetime.now(UTC)
        deadline = time.time() + 5
        while time.time() < deadline:
            runs = p.list_runs(RunFilter(task_id=task.id, status="success"))
            if runs:
                break
            time.sleep(0.05)
        assert runs, "due task run should complete"
        assert runs[0].output_summary == "done"
        assert task.run_count == 1
        p.stop()

    def test_tick_invalid_schedule_pauses_task(self):
        p = InMemoryScheduleProvider(autostart=False)
        task = p.create_task(ScheduledTask(name="t", agent_name="a", interval_seconds=60))
        # Directly corrupt the schedule so compute_next_run fails
        task.interval_seconds = -1
        task.next_run_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        assert p.tick() == 0
        assert task.enabled is False
        p.stop()

    def test_executor_failure_recorded(self):
        def boom(task):
            raise ValueError("kaboom")

        p = InMemoryScheduleProvider(autostart=False)
        p.set_executor(boom)
        task = p.create_task(ScheduledTask(name="t", agent_name="a"))
        p.trigger_task(task.id)
        deadline = time.time() + 5
        failed = []
        while time.time() < deadline:
            failed = p.list_runs(RunFilter(task_id=task.id, status="failed"))
            if failed:
                break
            time.sleep(0.05)
        assert failed and failed[0].error == "kaboom"
        assert task.error_count == 1
        assert task.last_status == "failed"
        p.stop()

    def test_trigger_without_executor_skipped(self):
        p = InMemoryScheduleProvider(autostart=False)  # no executor
        task = p.create_task(ScheduledTask(name="t", agent_name="a"))
        run = p.trigger_task(task.id)
        assert run is not None and run.trigger == "manual"
        deadline = time.time() + 5
        skipped = []
        while time.time() < deadline:
            skipped = p.list_runs(RunFilter(task_id=task.id, status="skipped"))
            if skipped:
                break
            time.sleep(0.05)
        assert skipped and skipped[0].error == "no executor configured"
        p.stop()

    def test_trigger_works_when_paused(self):
        p = InMemoryScheduleProvider(autostart=False)
        p.set_executor(lambda t: "ok")
        task = p.create_task(ScheduledTask(name="t", agent_name="a"))
        p.pause_task(task.id)
        run = p.trigger_task(task.id)
        assert run is not None
        p.stop()

    def test_trigger_missing_returns_none(self):
        p = InMemoryScheduleProvider(autostart=False)
        assert p.trigger_task("nope") is None
        p.stop()

    def test_stats(self):
        p = InMemoryScheduleProvider(autostart=False)
        p.set_executor(lambda t: "ok")
        t1 = p.create_task(ScheduledTask(name="t1", agent_name="coder"))
        t2 = p.create_task(ScheduledTask(name="t2", agent_name="coder"))
        p.pause_task(t2.id)
        p.trigger_task(t1.id)
        deadline = time.time() + 5
        while time.time() < deadline:
            if p.get_stats().successful_runs == 1:
                break
            time.sleep(0.05)
        stats = p.get_stats()
        assert stats.total == 2
        assert stats.enabled == 1
        assert stats.paused == 1
        assert stats.by_agent == {"coder": 2}
        assert stats.successful_runs == 1
        assert stats.failed_runs == 0
        p.stop()

    def test_run_fifo_eviction(self):
        p = InMemoryScheduleProvider(max_runs=2, autostart=False)
        p.set_executor(lambda t: "ok")
        task = p.create_task(ScheduledTask(name="t", agent_name="a"))
        for _ in range(3):
            p.trigger_task(task.id)
            deadline = time.time() + 5
            while time.time() < deadline:
                if p.list_runs(RunFilter(task_id=task.id)):
                    # ensure at least recorded
                    break
            time.sleep(0.05)
        time.sleep(0.3)  # let workers finish
        assert len(p.list_runs()) <= 2
        p.stop()

    def test_background_loop_fires_due_task(self):
        p = InMemoryScheduleProvider(tick_seconds=0.05, autostart=False)
        fired = threading.Event()
        p.set_executor(lambda t: fired.set())
        p.create_task(ScheduledTask(name="t", agent_name="a", interval_seconds=0.1))
        p.start()
        assert fired.wait(timeout=5), "background loop should fire the due task"
        p.stop()

    def test_start_stop_idempotent(self):
        p = InMemoryScheduleProvider(autostart=False)
        p.start()
        p.start()  # no-op
        p.stop()
        p.stop()  # no-op
        p.start()  # restart works
        p.stop()

    def test_close_clears(self):
        p = InMemoryScheduleProvider(autostart=False)
        p.create_task(ScheduledTask(name="t", agent_name="a"))
        p.close()
        assert p.list_tasks() == []


# ---------------------------------------------------------------------------
# Registry + decorator
# ---------------------------------------------------------------------------

class TestScheduleRegistry:
    def test_defaults_registered(self):
        assert schedule_registry.has("null")
        assert schedule_registry.has("memory")
        assert "null" in schedule_registry.names()
        assert schedule_registry.count >= 2

    def test_create_unknown_raises(self):
        with pytest.raises(RegistryError):
            schedule_registry.create("nope")

    def test_register_and_create(self):
        registry = ScheduleRegistry()
        registry.register("custom", NullScheduleProvider)
        assert isinstance(registry.create("custom"), NullScheduleProvider)

    def test_duplicate_raises(self):
        registry = ScheduleRegistry()
        registry.register("x", NullScheduleProvider)
        with pytest.raises(RegistryError):
            registry.register("x", NullScheduleProvider)

    def test_duplicate_override_allowed(self):
        registry = ScheduleRegistry()
        registry.register("x", NullScheduleProvider)
        registry.register("x", InMemoryScheduleProvider, override=True)
        assert registry.has("x")

    def test_empty_name_raises(self):
        registry = ScheduleRegistry()
        with pytest.raises(RegistryError):
            registry.register("  ", NullScheduleProvider)

    def test_unregister(self):
        registry = ScheduleRegistry()
        registry.register("x", NullScheduleProvider)
        assert registry.unregister("x") is True
        assert registry.unregister("x") is False


class TestRegisterDecorator:
    def test_decorator_registers(self):
        @register_schedule_provider("test-sched-prov")
        class MyProvider(NullScheduleProvider):
            pass

        assert schedule_registry.has("test-sched-prov")
        assert isinstance(schedule_registry.create("test-sched-prov"), MyProvider)
        schedule_registry.unregister("test-sched-prov")


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class TestScheduleManager:
    def test_disabled_uses_null(self):
        mgr = ScheduleManager(provider="memory", enabled=False)
        assert mgr.enabled is False
        assert isinstance(mgr.provider, NullScheduleProvider)
        assert mgr.list_tasks() == []
        assert mgr.get_stats().total == 0

    def test_create_interval_task(self):
        mgr = ScheduleManager(provider="memory", enabled=True, autostart=False)
        task = mgr.create_task(name="t", agent_name="a", interval_seconds=30)
        assert task.id and task.next_run_at
        mgr.close()

    def test_create_cron_task(self):
        mgr = ScheduleManager(provider="memory", enabled=True, autostart=False)
        task = mgr.create_task(
            name="t", agent_name="a", schedule_type="cron", cron_expr="0 8 * * *"
        )
        assert task.schedule_type == "cron"
        mgr.close()

    def test_create_missing_name(self):
        mgr = ScheduleManager(provider="memory", enabled=True, autostart=False)
        with pytest.raises(RegistryError):
            mgr.create_task(name="", agent_name="a")
        mgr.close()

    def test_create_missing_agent(self):
        mgr = ScheduleManager(provider="memory", enabled=True, autostart=False)
        with pytest.raises(RegistryError):
            mgr.create_task(name="t", agent_name="")
        mgr.close()

    def test_create_bad_type(self):
        mgr = ScheduleManager(provider="memory", enabled=True, autostart=False)
        with pytest.raises(RegistryError):
            mgr.create_task(name="t", agent_name="a", schedule_type="weekly")
        mgr.close()

    def test_create_cron_without_expr(self):
        mgr = ScheduleManager(provider="memory", enabled=True, autostart=False)
        with pytest.raises(RegistryError):
            mgr.create_task(name="t", agent_name="a", schedule_type="cron")
        mgr.close()

    def test_create_invalid_cron(self):
        mgr = ScheduleManager(provider="memory", enabled=True, autostart=False)
        with pytest.raises(RegistryError):
            mgr.create_task(name="t", agent_name="a", schedule_type="cron", cron_expr="bad")
        mgr.close()

    def test_create_invalid_interval(self):
        mgr = ScheduleManager(provider="memory", enabled=True, autostart=False)
        with pytest.raises(RegistryError):
            mgr.create_task(name="t", agent_name="a", interval_seconds=0)
        mgr.close()

    def test_update_no_changes_returns_task(self):
        mgr = ScheduleManager(provider="memory", enabled=True, autostart=False)
        task = mgr.create_task(name="t", agent_name="a")
        assert mgr.update_task(task.id) is not None
        mgr.close()

    def test_update_invalid_cron_raises(self):
        mgr = ScheduleManager(provider="memory", enabled=True, autostart=False)
        task = mgr.create_task(name="t", agent_name="a", schedule_type="cron",
                               cron_expr="0 8 * * *")
        with pytest.raises(RegistryError):
            mgr.update_task(task.id, cron_expr="bad")
        mgr.close()

    def test_update_switch_to_cron_without_expr_raises(self):
        mgr = ScheduleManager(provider="memory", enabled=True, autostart=False)
        task = mgr.create_task(name="t", agent_name="a")
        with pytest.raises(RegistryError):
            mgr.update_task(task.id, schedule_type="cron")
        mgr.close()

    def test_update_recomputes_next_run(self):
        mgr = ScheduleManager(provider="memory", enabled=True, autostart=False)
        task = mgr.create_task(name="t", agent_name="a", interval_seconds=60)
        before = task.next_run_at
        time.sleep(0.01)
        updated = mgr.update_task(task.id, interval_seconds=300)
        assert updated.next_run_at != before
        mgr.close()

    def test_full_lifecycle(self):
        mgr = ScheduleManager(provider="memory", enabled=True, autostart=False)
        mgr.set_executor(lambda t: f"hi {t.name}")
        task = mgr.create_task(name="t", agent_name="a", interval_seconds=60)
        got = mgr.get_task(task.id)
        assert got is not None
        assert mgr.pause_task(task.id).enabled is False
        assert mgr.resume_task(task.id).enabled is True
        run = mgr.trigger_task(task.id)
        assert run is not None
        deadline = time.time() + 5
        while time.time() < deadline:
            runs = mgr.list_runs(task_id=task.id, status="success")
            if runs:
                break
            time.sleep(0.05)
        assert runs and runs[0].output_summary == "hi t"
        assert mgr.delete_task(task.id) is True
        assert mgr.get_task(task.id) is None
        mgr.close()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_not_initialised_raises(self):
        reset_schedule_manager()
        with pytest.raises(RuntimeError):
            get_schedule_manager()

    def test_set_get_reset(self):
        reset_schedule_manager()
        mgr = ScheduleManager(enabled=False)
        set_schedule_manager(mgr)
        assert get_schedule_manager() is mgr
        reset_schedule_manager()
        with pytest.raises(RuntimeError):
            get_schedule_manager()


# ---------------------------------------------------------------------------
# Protocol + concurrency
# ---------------------------------------------------------------------------

class TestProtocolCompliance:
    def test_null_is_schedule_provider(self):
        assert isinstance(NullScheduleProvider(), ScheduleProvider)

    def test_inmemory_is_schedule_provider(self):
        assert isinstance(InMemoryScheduleProvider(autostart=False), ScheduleProvider)


class TestConcurrency:
    def test_parallel_creates(self):
        p = InMemoryScheduleProvider(autostart=False)
        errors: list[Exception] = []

        def make(i):
            try:
                p.create_task(ScheduledTask(name=f"t{i}", agent_name="a"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=make, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(p.list_tasks()) == 8
        p.stop()

    def test_parallel_trigger(self):
        p = InMemoryScheduleProvider(max_workers=4, autostart=False)
        p.set_executor(lambda t: "ok")
        task = p.create_task(ScheduledTask(name="t", agent_name="a"))
        for _ in range(4):
            p.trigger_task(task.id)
        deadline = time.time() + 5
        while time.time() < deadline:
            if task.run_count >= 4:
                break
            time.sleep(0.05)
        assert task.run_count == 4
        assert task.error_count == 0
        p.stop()
