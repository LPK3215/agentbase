"""Tests for the alert core service.

Covers:
- Comparison operators (_compare)
- ISO timestamp parsing (_parse_iso)
- Data models (AlertRule / AlertEvent / stats)
- Null provider (disabled no-op semantics)
- InMemory provider (rule CRUD, filters, event FIFO eviction, stats)
- Registry (register / create / duplicate / unknown / unregister)
- Manager validation (metric/operator/severity/duration/cooldown limits)
- Manager rule CRUD (duplicate names, update semantics)
- Evaluation engine (fire / duration / cooldown / resolve / recovery notify)
- Reader & notifier error isolation
- Background loop (start / stop)
- Disabled manager no-op
- Singleton (get / set / reset)
- Concurrency (parallel tick)
- Protocol compliance
"""
from __future__ import annotations

import threading
import time

import pytest

from agentbase.core.alert import (
    OPERATORS,
    SUPPORTED_METRICS,
    AlertEvent,
    AlertFilter,
    AlertManager,
    AlertProvider,
    AlertRegistry,
    AlertRule,
    EventFilter,
    InMemoryAlertProvider,
    NullAlertProvider,
    _compare,
    _parse_iso,
    alert_registry,
    get_alert_manager,
    register_alert_provider,
    reset_alert_manager,
    set_alert_manager,
)
from agentbase.runtime.errors import RegistryError


def _mgr(**kwargs) -> AlertManager:
    return AlertManager(provider="memory", enabled=True, **kwargs)


def _rule(name="r1", metric="errors_total", operator="gt", threshold=100) -> AlertRule:
    return AlertRule(name=name, metric=metric, operator=operator, threshold=threshold)


# ---------------------------------------------------------------------------
# Comparison & parsing helpers
# ---------------------------------------------------------------------------


class TestCompare:
    def test_all_operators(self):
        assert _compare(101, "gt", 100) is True
        assert _compare(100, "gt", 100) is False
        assert _compare(100, "gte", 100) is True
        assert _compare(99, "gte", 100) is False
        assert _compare(99, "lt", 100) is True
        assert _compare(100, "lt", 100) is False
        assert _compare(100, "lte", 100) is True
        assert _compare(101, "lte", 100) is False
        assert _compare(100, "eq", 100) is True
        assert _compare(101, "eq", 100) is False
        assert _compare(101, "ne", 100) is True
        assert _compare(100, "ne", 100) is False

    def test_operators_constant(self):
        assert OPERATORS == frozenset({"gt", "gte", "lt", "lte", "eq", "ne"})


class TestParseIso:
    def test_valid(self):
        assert _parse_iso("2026-01-01T00:00:00+00:00") > 0

    def test_naive_treated_as_utc(self):
        assert _parse_iso("2026-01-01T00:00:00") > 0

    def test_invalid_returns_zero(self):
        assert _parse_iso("garbage") == 0.0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class TestModels:
    def test_rule_defaults(self):
        r = AlertRule(name="r", metric="errors_total")
        assert r.operator == "gt"
        assert r.severity == "warning"
        assert r.duration_ticks == 1
        assert r.cooldown_seconds == 300
        assert r.notify_user_id == "*"
        assert r.enabled is True
        assert r.state == "ok"
        assert r.breach_count == 0
        assert r.last_fired_at is None

    def test_rule_to_dict(self):
        d = AlertRule(name="r", metric="errors_total", threshold=5).to_dict()
        assert d["name"] == "r"
        assert d["metric"] == "errors_total"
        assert d["threshold"] == 5
        assert "state" in d and "breach_count" in d

    def test_event_to_dict(self):
        d = AlertEvent(rule_name="r", metric="m", value=1, state="firing").to_dict()
        assert d["state"] == "firing"
        assert d["severity"] == "warning"


# ---------------------------------------------------------------------------
# Null provider
# ---------------------------------------------------------------------------


class TestNullProvider:
    def test_all_noop(self):
        p = NullAlertProvider()
        r = p.create_rule(_rule())
        assert r.name == "r1"
        assert p.get_rule("x") is None
        assert p.list_rules() == []
        assert p.update_rule("x", {}) is None
        assert p.delete_rule("x") is False
        e = p.record_event(AlertEvent(rule_name="r"))
        assert e.rule_name == "r"
        assert p.list_events() == []
        assert p.get_stats().total_rules == 0
        p.close()


# ---------------------------------------------------------------------------
# InMemory provider
# ---------------------------------------------------------------------------


class TestInMemoryProvider:
    def test_rule_crud(self):
        p = InMemoryAlertProvider()
        stored = p.create_rule(_rule())
        assert stored.rule_id
        assert p.get_rule(stored.rule_id) is not None
        assert p.get_rule("nope") is None
        updated = p.update_rule(stored.rule_id, {"threshold": 200})
        assert updated.threshold == 200
        assert p.delete_rule(stored.rule_id) is True
        assert p.delete_rule(stored.rule_id) is False

    def test_duplicate_rule_id(self):
        p = InMemoryAlertProvider()
        r = _rule()
        r.rule_id = "fixed"
        p.create_rule(r)
        with pytest.raises(RegistryError, match="already exists"):
            p.create_rule(r)

    def test_rule_filters(self):
        p = InMemoryAlertProvider()
        p.create_rule(_rule("a", "errors_total"))
        p.create_rule(_rule("b", "requests_total"))
        p.create_rule(AlertRule(name="c", metric="errors_total", enabled=False))
        assert [r.name for r in p.list_rules(AlertFilter(metric="errors_total"))] == ["a", "c"]
        assert [r.name for r in p.list_rules(AlertFilter(enabled=True))] == ["a", "b"]
        assert [r.name for r in p.list_rules(AlertFilter(enabled=False))] == ["c"]

    def test_rule_pagination(self):
        p = InMemoryAlertProvider()
        for i in range(5):
            p.create_rule(_rule(f"r{i}"))
        page = p.list_rules(AlertFilter(limit=2, offset=3))
        assert [r.name for r in page] == ["r3", "r4"]

    def test_rule_fifo_eviction(self):
        p = InMemoryAlertProvider(max_rules=3)
        for i in range(5):
            p.create_rule(_rule(f"r{i}"))
        assert len(p.list_rules()) == 3
        assert [r.name for r in p.list_rules()] == ["r2", "r3", "r4"]

    def test_event_recording_and_filter(self):
        p = InMemoryAlertProvider()
        rule = p.create_rule(_rule())
        p.record_event(AlertEvent(rule_id=rule.rule_id, rule_name=rule.name, state="firing"))
        time.sleep(0.01)
        e2 = p.record_event(AlertEvent(rule_id=rule.rule_id, rule_name=rule.name, state="resolved"))
        # newest first
        events = p.list_events()
        assert [e.state for e in events] == ["resolved", "firing"]
        assert [e.state for e in p.list_events(EventFilter(state="firing"))] == ["firing"]
        assert [e.state for e in p.list_events(EventFilter(rule_id=rule.rule_id))] == ["resolved", "firing"]
        assert len(p.list_events(EventFilter(since=e2.created_at))) >= 1

    def test_event_fifo_eviction(self):
        p = InMemoryAlertProvider(max_events=3)
        for i in range(5):
            p.record_event(AlertEvent(rule_name=f"r{i}"))
        assert len(p.list_events()) == 3

    def test_stats(self):
        p = InMemoryAlertProvider()
        rule = p.create_rule(_rule())
        p.create_rule(AlertRule(name="off", metric="errors_total", enabled=False))
        p.record_event(AlertEvent(rule_id=rule.rule_id, state="firing", severity="error"))
        p.record_event(AlertEvent(rule_id=rule.rule_id, state="resolved", severity="error"))
        stats = p.get_stats()
        assert stats.total_rules == 2
        assert stats.enabled_rules == 1
        assert stats.total_events == 2
        assert stats.firing_events == 1
        assert stats.resolved_events == 1
        assert stats.by_severity == {"error": 2}

    def test_close_clears(self):
        p = InMemoryAlertProvider()
        p.create_rule(_rule())
        p.close()
        assert p.list_rules() == []


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_defaults_registered(self):
        assert alert_registry.has("memory")
        assert alert_registry.has("null")

    def test_create_memory(self):
        p = alert_registry.create("memory", max_rules=10)
        assert isinstance(p, InMemoryAlertProvider)

    def test_unknown_provider(self):
        with pytest.raises(RegistryError, match="Unknown alert provider"):
            AlertRegistry().create("nope")

    def test_duplicate_registration(self):
        reg = AlertRegistry()
        reg.register("a", NullAlertProvider)
        with pytest.raises(RegistryError, match="already registered"):
            reg.register("a", NullAlertProvider)

    def test_empty_name(self):
        with pytest.raises(RegistryError, match="empty"):
            AlertRegistry().register("", NullAlertProvider)

    def test_override(self):
        reg = AlertRegistry()
        reg.register("a", NullAlertProvider)
        reg.register("a", InMemoryAlertProvider, override=True)
        assert isinstance(reg.create("a"), InMemoryAlertProvider)

    def test_unregister(self):
        reg = AlertRegistry()
        reg.register("a", NullAlertProvider)
        assert reg.unregister("a") is True
        assert reg.unregister("a") is False

    def test_decorator(self):
        @register_alert_provider("test_alert_prov")
        class Custom:
            def create_rule(self, rule): return rule
            def get_rule(self, rid): return None
            def list_rules(self, filter=None): return []
            def update_rule(self, rid, changes): return None
            def delete_rule(self, rid): return False
            def record_event(self, event): return event
            def list_events(self, filter=None): return []
            def get_stats(self):
                from agentbase.core.alert import AlertStats
                return AlertStats()
            def close(self): pass

        assert alert_registry.has("test_alert_prov")
        alert_registry.unregister("test_alert_prov")


# ---------------------------------------------------------------------------
# Manager validation
# ---------------------------------------------------------------------------


class TestManagerValidation:
    def test_disabled_defaults_to_null(self):
        mgr = AlertManager()
        assert mgr.enabled is False
        assert isinstance(mgr.provider, NullAlertProvider)

    def test_empty_name(self):
        mgr = _mgr()
        with pytest.raises(RegistryError, match="name is required"):
            mgr.create_rule("", metric="errors_total")

    def test_name_too_long(self):
        mgr = _mgr()
        with pytest.raises(RegistryError, match="too long"):
            mgr.create_rule("r" * 65, metric="errors_total")

    def test_unsupported_metric(self):
        mgr = _mgr()
        with pytest.raises(RegistryError, match="Unsupported metric"):
            mgr.create_rule("r", metric="nonexistent_metric")

    def test_all_supported_metrics_accepted(self):
        mgr = _mgr()
        for i, m in enumerate(sorted(SUPPORTED_METRICS)):
            mgr.create_rule(f"r{i}", metric=m)

    def test_invalid_operator(self):
        mgr = _mgr()
        with pytest.raises(RegistryError, match="Invalid operator"):
            mgr.create_rule("r", metric="errors_total", operator="between")

    def test_invalid_severity(self):
        mgr = _mgr()
        with pytest.raises(RegistryError, match="Invalid severity"):
            mgr.create_rule("r", metric="errors_total", severity="fatal")

    def test_duration_out_of_range(self):
        mgr = _mgr()
        with pytest.raises(RegistryError, match="duration_ticks"):
            mgr.create_rule("r", metric="errors_total", duration_ticks=0)
        with pytest.raises(RegistryError, match="duration_ticks"):
            mgr.create_rule("r", metric="errors_total", duration_ticks=101)

    def test_cooldown_out_of_range(self):
        mgr = _mgr()
        with pytest.raises(RegistryError, match="cooldown_seconds"):
            mgr.create_rule("r", metric="errors_total", cooldown_seconds=-1)
        with pytest.raises(RegistryError, match="cooldown_seconds"):
            mgr.create_rule("r", metric="errors_total", cooldown_seconds=86_401)

    def test_duplicate_name(self):
        mgr = _mgr()
        mgr.create_rule("dup", metric="errors_total")
        with pytest.raises(RegistryError, match="already used"):
            mgr.create_rule("dup", metric="requests_total")

    def test_tick_seconds_clamped(self):
        assert AlertManager(enabled=True, tick_seconds=0).tick_seconds == 1
        assert AlertManager(enabled=True, tick_seconds=99_999).tick_seconds == 3_600


# ---------------------------------------------------------------------------
# Manager rule CRUD
# ---------------------------------------------------------------------------


class TestManagerCrud:
    def test_create_get_list(self):
        mgr = _mgr()
        rule = mgr.create_rule("high-errors", metric="errors_total", threshold=100)
        assert mgr.get_rule(rule.rule_id) is not None
        names = [r.name for r in mgr.list_rules()]
        assert "high-errors" in names

    def test_update_allowed_fields(self):
        mgr = _mgr()
        rule = mgr.create_rule("r", metric="errors_total", threshold=100)
        updated = mgr.update_rule(rule.rule_id, {"threshold": 200, "enabled": False})
        assert updated.threshold == 200
        assert updated.enabled is False
        # threshold change resets counters
        assert updated.breach_count == 0
        assert updated.state == "ok"

    def test_update_invalid_operator(self):
        mgr = _mgr()
        rule = mgr.create_rule("r", metric="errors_total")
        with pytest.raises(RegistryError, match="Invalid operator"):
            mgr.update_rule(rule.rule_id, {"operator": "bad"})

    def test_update_missing(self):
        assert _mgr().update_rule("nope", {"threshold": 1}) is None

    def test_delete(self):
        mgr = _mgr()
        rule = mgr.create_rule("r", metric="errors_total")
        assert mgr.delete_rule(rule.rule_id) is True
        assert mgr.delete_rule(rule.rule_id) is False


# ---------------------------------------------------------------------------
# Evaluation engine
# ---------------------------------------------------------------------------


class TestEvaluation:
    def test_fire_on_breach(self):
        mgr = _mgr()
        mgr.set_metrics_reader(lambda m: 150.0)
        notified: list[dict] = []
        mgr.set_notifier(lambda **kw: notified.append(kw))
        rule = mgr.create_rule("high-errors", metric="errors_total", threshold=100)
        events = mgr.tick()
        assert len(events) == 1
        assert events[0].state == "firing"
        assert events[0].value == 150.0
        # rule state persisted
        assert mgr.get_rule(rule.rule_id).state == "firing"
        # notification delivered with metadata
        assert len(notified) == 1
        assert notified[0]["category"] == "alert"
        assert notified[0]["metadata"]["rule_id"] == rule.rule_id

    def test_no_fire_when_within_threshold(self):
        mgr = _mgr()
        mgr.set_metrics_reader(lambda m: 50.0)
        mgr.create_rule("high-errors", metric="errors_total", threshold=100)
        assert mgr.tick() == []
        assert mgr.list_events() == []

    def test_duration_ticks_requires_consecutive_breaches(self):
        mgr = _mgr()
        values = iter([150.0, 150.0, 150.0])
        mgr.set_metrics_reader(lambda m: next(values))
        mgr.create_rule("sustained", metric="errors_total", threshold=100, duration_ticks=3)
        assert mgr.tick() == []  # breach 1
        assert mgr.tick() == []  # breach 2
        events = mgr.tick()  # breach 3 → fire
        assert len(events) == 1

    def test_breach_counter_resets_on_non_breach(self):
        mgr = _mgr()
        values = iter([150.0, 50.0, 150.0])
        mgr.set_metrics_reader(lambda m: next(values))
        rule = mgr.create_rule("flappy", metric="errors_total", threshold=100, duration_ticks=2)
        mgr.tick()  # breach 1
        mgr.tick()  # reset
        assert mgr.get_rule(rule.rule_id).breach_count == 0
        assert mgr.tick() == []  # breach 1 again, not yet 2

    def test_cooldown_prevents_refire(self):
        mgr = _mgr()
        mgr.set_metrics_reader(lambda m: 150.0)
        rule = mgr.create_rule("hot", metric="errors_total", threshold=100, cooldown_seconds=3600)
        assert len(mgr.tick()) == 1  # fires
        assert mgr.tick() == []  # in cooldown → no repeat
        assert mgr.get_rule(rule.rule_id).state == "firing"

    def test_resolve_after_recovery(self):
        mgr = _mgr()
        state = {"value": 150.0}
        mgr.set_metrics_reader(lambda m: state["value"])
        mgr.create_rule("r", metric="errors_total", threshold=100)
        assert len(mgr.tick()) == 1  # firing
        state["value"] = 10.0
        events = mgr.tick()  # recovery
        assert len(events) == 1
        assert events[0].state == "resolved"

    def test_reader_none_skips_rule(self):
        mgr = _mgr()
        def bad_reader(m):
            raise RuntimeError("metrics unavailable")
        mgr.set_metrics_reader(bad_reader)
        rule = mgr.create_rule("r", metric="errors_total", threshold=100)
        assert mgr.tick() == []
        assert mgr.get_rule(rule.rule_id).state == "ok"

    def test_reader_nan_skips_rule(self):
        mgr = _mgr()
        mgr.set_metrics_reader(lambda m: float("nan"))
        mgr.create_rule("r", metric="errors_total", threshold=100)
        assert mgr.tick() == []

    def test_no_reader_means_no_events(self):
        mgr = _mgr()  # reader never set
        mgr.create_rule("r", metric="errors_total", threshold=100)
        assert mgr.tick() == []

    def test_failing_notifier_does_not_block(self):
        mgr = _mgr()
        mgr.set_metrics_reader(lambda m: 150.0)
        def bad_notifier(**kw):
            raise RuntimeError("notification sink down")
        mgr.set_notifier(bad_notifier)
        mgr.create_rule("r", metric="errors_total", threshold=100)
        events = mgr.tick()  # must not raise
        assert len(events) == 1

    def test_disabled_rule_not_evaluated(self):
        mgr = _mgr()
        mgr.set_metrics_reader(lambda m: 150.0)
        mgr.create_rule("off", metric="errors_total", threshold=100, enabled=False)
        assert mgr.tick() == []

    def test_disabled_manager_tick_noop(self):
        mgr = AlertManager()  # disabled
        assert mgr.tick() == []

    def test_evaluate_rule_direct(self):
        mgr = _mgr()
        mgr.set_metrics_reader(lambda m: 150.0)
        rule = mgr.create_rule("r", metric="errors_total", threshold=100)
        event = mgr.evaluate_rule(rule)
        assert event is not None and event.state == "firing"


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------


class TestBackgroundLoop:
    def test_start_stop(self):
        mgr = _mgr(tick_seconds=1)
        fired: list[str] = []

        original_tick = mgr.tick

        def counting_tick():
            events = original_tick()
            fired.extend(e.rule_name for e in events)
            return events

        mgr.tick = counting_tick
        mgr.set_metrics_reader(lambda m: 150.0)
        mgr.create_rule("loop-rule", metric="errors_total", threshold=100)
        mgr.start()
        time.sleep(2.5)
        mgr.stop()
        assert "loop-rule" in fired

    def test_start_twice_safe(self):
        mgr = _mgr(tick_seconds=60)
        mgr.start()
        mgr.start()  # no-op
        mgr.stop()
        mgr.stop()  # safe


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_creates_disabled(self):
        reset_alert_manager()
        assert get_alert_manager().enabled is False

    def test_set_and_reset(self):
        mgr = _mgr()
        set_alert_manager(mgr)
        assert get_alert_manager() is mgr
        reset_alert_manager()
        assert get_alert_manager() is not mgr
        reset_alert_manager()


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_parallel_ticks(self):
        mgr = _mgr()
        mgr.set_metrics_reader(lambda m: 50.0)  # within threshold
        for i in range(5):
            mgr.create_rule(f"r{i}", metric="errors_total", threshold=100)
        errors: list[Exception] = []

        def worker() -> None:
            try:
                for _ in range(10):
                    mgr.tick()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_inmemory_satisfies_protocol(self):
        assert isinstance(InMemoryAlertProvider(), AlertProvider)

    def test_null_satisfies_protocol(self):
        assert isinstance(NullAlertProvider(), AlertProvider)
