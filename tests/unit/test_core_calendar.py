"""Tests for core.calendar — models, providers, registry, manager, singleton.

Covers: event data model round-trips, time parsing, Null provider no-ops,
InMemory CRUD + filters + stats + eviction, registry semantics, manager
validation (normal/boundary/error), singleton, concurrency, and Protocol
compliance.
"""
from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from agentbase.core.calendar import (
    CalendarEvent,
    CalendarFilter,
    CalendarManager,
    CalendarProvider,
    CalendarRegistry,
    InMemoryCalendarProvider,
    NullCalendarProvider,
    _apply_calendar_filter,
    _parse_iso,
    calendar_registry,
    get_calendar_manager,
    register_calendar_provider,
    reset_calendar_manager,
    set_calendar_manager,
)
from agentbase.runtime.errors import RegistryError


def _iso(**kwargs) -> str:
    base = {"year": 2026, "month": 9, "day": 1, "hour": 0, "minute": 0}
    base.update(kwargs)
    return datetime(**base, tzinfo=UTC).isoformat()  # type: ignore[arg-type]


def _past_iso(**kwargs) -> str:
    """A timestamp safely in the past (2020-01-01 base)."""
    base = {"year": 2020, "month": 1, "day": 1, "hour": 0, "minute": 0}
    base.update(kwargs)
    return datetime(**base, tzinfo=UTC).isoformat()  # type: ignore[arg-type]


def _future_iso(hours: float = 24.0) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


# ---------------------------------------------------------------------------
# Time helper
# ---------------------------------------------------------------------------

class TestParseIso:
    def test_valid_iso(self):
        dt = _parse_iso("2026-09-01T09:00:00+00:00")
        assert dt is not None and dt.year == 2026

    def test_z_suffix(self):
        dt = _parse_iso("2026-09-01T09:00:00Z")
        assert dt is not None and dt.tzinfo is not None

    def test_naive_treated_as_utc(self):
        dt = _parse_iso("2026-09-01T09:00:00")
        assert dt is not None and dt.tzinfo is not None

    def test_datetime_passthrough(self):
        now = datetime.now(UTC)
        assert _parse_iso(now) == now

    def test_invalid_returns_none(self):
        assert _parse_iso("not-a-date") is None
        assert _parse_iso("") is None


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class TestCalendarEvent:
    def test_defaults_and_id(self):
        ev = CalendarEvent(title="t", start_time=_iso(), end_time=_iso(hour=10))
        assert ev.event_id
        assert ev.status == "confirmed"
        assert ev.created_at and ev.updated_at

    def test_to_dict_round_trip(self):
        ev = CalendarEvent(
            title="standup",
            start_time=_iso(),
            end_time=_iso(minute=30),
            location="room A",
            attendees=["a@x.com"],
            tags=["daily"],
            reminder_minutes=15,
            metadata={"source": "test"},
        )
        data = ev.to_dict()
        restored = CalendarEvent.from_dict(data)
        assert restored.title == ev.title
        assert restored.start_time == ev.start_time
        assert restored.attendees == ["a@x.com"]
        assert restored.reminder_minutes == 15
        assert restored.metadata == {"source": "test"}

    def test_from_dict_ignores_unknown_keys(self):
        ev = CalendarEvent.from_dict({
            "title": "t", "start_time": _iso(), "end_time": _iso(hour=10),
            "bogus": 1,
        })
        assert ev.title == "t"


# ---------------------------------------------------------------------------
# Null provider
# ---------------------------------------------------------------------------

class TestNullProvider:
    def test_all_noops(self):
        p = NullCalendarProvider()
        ev = CalendarEvent(title="t", start_time=_iso(), end_time=_iso(hour=10))
        assert p.create_event(ev) is ev
        assert p.get_event("x") is None
        assert p.list_events() == []
        assert p.update_event("x", {}) is None
        assert p.delete_event("x") is False
        assert p.get_stats().total == 0
        p.close()  # no-op, no error


# ---------------------------------------------------------------------------
# InMemory provider
# ---------------------------------------------------------------------------

class TestInMemoryProvider:
    def _mk(self, **kw):
        defaults = {
            "title": "event",
            "start_time": _iso(),
            "end_time": _iso(hour=10),
        }
        defaults.update(kw)
        return CalendarEvent(**defaults)

    def test_crud(self):
        p = InMemoryCalendarProvider()
        ev = p.create_event(self._mk())
        assert p.get_event(ev.event_id).title == "event"
        updated = p.update_event(ev.event_id, {"title": "renamed"})
        assert updated is not None and updated.title == "renamed"
        assert updated.updated_at >= ev.updated_at
        assert p.delete_event(ev.event_id) is True
        assert p.delete_event(ev.event_id) is False
        assert p.get_event(ev.event_id) is None

    def test_duplicate_id_rejected(self):
        p = InMemoryCalendarProvider()
        ev = p.create_event(self._mk())
        with pytest.raises(RegistryError):
            p.create_event(ev)

    def test_update_missing_returns_none(self):
        p = InMemoryCalendarProvider()
        assert p.update_event("nope", {"title": "x"}) is None

    def test_fifo_eviction(self):
        p = InMemoryCalendarProvider(max_events=3)
        for i in range(5):
            p.create_event(self._mk(title=f"e{i}"))
        assert len(p.list_events()) == 3
        titles = {e.title for e in p.list_events()}
        assert titles == {"e2", "e3", "e4"}

    def test_sorted_by_start_time(self):
        p = InMemoryCalendarProvider()
        p.create_event(self._mk(title="late", start_time=_iso(day=5), end_time=_iso(day=5, hour=10)))
        p.create_event(self._mk(title="early", start_time=_iso(day=1), end_time=_iso(day=1, hour=10)))
        titles = [e.title for e in p.list_events()]
        assert titles == ["early", "late"]

    def test_stats(self):
        p = InMemoryCalendarProvider()
        p.create_event(self._mk(
            title="upcoming", status="confirmed", tags=["a"],
            start_time=_future_iso(10), end_time=_future_iso(11),
        ))
        p.create_event(self._mk(
            title="past", status="cancelled", tags=["a", "b"],
            start_time=_past_iso(), end_time=_past_iso(hour=10),
        ))
        s = p.get_stats()
        assert s.total == 2
        assert s.upcoming == 1
        assert s.past == 1
        assert s.cancelled == 1
        assert s.by_status == {"confirmed": 1, "cancelled": 1}
        assert s.by_tag["a"] == 2 and s.by_tag["b"] == 1

    def test_close_clears(self):
        p = InMemoryCalendarProvider()
        p.create_event(self._mk())
        p.close()
        assert p.list_events() == []


# ---------------------------------------------------------------------------
# Filter helper
# ---------------------------------------------------------------------------

class TestApplyFilter:
    def _events(self):
        return [
            CalendarEvent(title="a", start_time=_iso(day=1), end_time=_iso(day=1, hour=1),
                          status="confirmed", tags=["work"], location="Office Beijing",
                          attendees=["alice@x.com"]),
            CalendarEvent(title="b", start_time=_iso(day=2), end_time=_iso(day=2, hour=1),
                          status="tentative", tags=["private"], location="Home",
                          attendees=["bob@x.com"]),
            CalendarEvent(title="c", start_time=_iso(day=3), end_time=_iso(day=3, hour=1),
                          status="cancelled", tags=["work"], location="Office Shanghai",
                          attendees=["alice@x.com"]),
        ]

    def test_no_filter_returns_all_sorted(self):
        assert [e.title for e in _apply_calendar_filter(self._events(), None)] == ["a", "b", "c"]

    def test_status_filter(self):
        flt = CalendarFilter(status="tentative")
        assert [e.title for e in _apply_calendar_filter(self._events(), flt)] == ["b"]

    def test_tag_filter(self):
        flt = CalendarFilter(tag="work")
        assert {e.title for e in _apply_calendar_filter(self._events(), flt)} == {"a", "c"}

    def test_location_substring_case_insensitive(self):
        flt = CalendarFilter(location="office shanghai")
        assert [e.title for e in _apply_calendar_filter(self._events(), flt)] == ["c"]

    def test_attendee_filter(self):
        flt = CalendarFilter(attendee="bob@x.com")
        assert [e.title for e in _apply_calendar_filter(self._events(), flt)] == ["b"]

    def test_time_range(self):
        # since: event end >= since; until: event start <= until
        flt = CalendarFilter(since=_iso(day=2, hour=1), until=_iso(day=3))
        assert {e.title for e in _apply_calendar_filter(self._events(), flt)} == {"b", "c"}

    def test_upcoming_only(self):
        events = [CalendarEvent(title="future", start_time=_future_iso(5), end_time=_future_iso(6)),
                  CalendarEvent(title="gone", start_time=_past_iso(), end_time=_past_iso(hour=1))]
        flt = CalendarFilter(upcoming_only=True)
        assert [e.title for e in _apply_calendar_filter(events, flt)] == ["future"]

    def test_pagination(self):
        flt = CalendarFilter(limit=2, offset=1)
        assert [e.title for e in _apply_calendar_filter(self._events(), flt)] == ["b", "c"]
        flt2 = CalendarFilter(limit=0)
        assert _apply_calendar_filter(self._events(), flt2) == []


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_defaults_registered(self):
        assert calendar_registry.has("memory")
        assert calendar_registry.has("null")
        assert "memory" in calendar_registry.names()

    def test_register_and_create(self):
        reg = CalendarRegistry()
        reg.register("null", NullCalendarProvider)
        assert isinstance(reg.create("null"), NullCalendarProvider)

    def test_duplicate_rejected(self):
        reg = CalendarRegistry()
        reg.register("x", NullCalendarProvider)
        with pytest.raises(RegistryError):
            reg.register("x", NullCalendarProvider)
        reg.register("x", NullCalendarProvider, override=True)  # ok

    def test_empty_name_rejected(self):
        reg = CalendarRegistry()
        with pytest.raises(RegistryError):
            reg.register("  ", NullCalendarProvider)

    def test_unknown_provider(self):
        reg = CalendarRegistry()
        with pytest.raises(RegistryError, match="Unknown calendar provider"):
            reg.create("nope")

    def test_unregister(self):
        reg = CalendarRegistry()
        reg.register("tmp", NullCalendarProvider)
        assert reg.unregister("tmp") is True
        assert reg.unregister("tmp") is False
        assert reg.count == 0

    def test_decorator(self):
        @register_calendar_provider("test-cal-custom")
        class Custom:
            def create_event(self, event): return event
            def get_event(self, event_id): return None
            def list_events(self, filter=None): return []
            def update_event(self, event_id, changes): return None
            def delete_event(self, event_id): return False
            def get_stats(self): from agentbase.core.calendar import CalendarStats; return CalendarStats()
            def close(self): pass

        assert calendar_registry.has("test-cal-custom")
        assert isinstance(calendar_registry.create("test-cal-custom"), Custom)
        calendar_registry.unregister("test-cal-custom")


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class TestManager:
    def test_disabled_manager_noop(self):
        m = CalendarManager(enabled=False)
        assert m.enabled is False
        ev = m.create_event(title="t", start_time=_iso(), end_time=_iso(hour=10))
        assert m.get_event(ev.event_id) is None  # Null storage
        assert m.list_events() == []

    def test_create_and_get(self):
        m = CalendarManager(provider="memory", enabled=True)
        ev = m.create_event(
            title="sync", start_time=_iso(), end_time=_iso(hour=1),
            attendees=["a@x.com"], tags=["work"], reminder_minutes=30,
        )
        got = m.get_event(ev.event_id)
        assert got is not None and got.title == "sync"

    def test_missing_title(self):
        m = CalendarManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="title"):
            m.create_event(title="  ", start_time=_iso(), end_time=_iso(hour=1))

    def test_title_too_long(self):
        m = CalendarManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="title too long"):
            m.create_event(title="x" * 201, start_time=_iso(), end_time=_iso(hour=1))

    def test_bad_start_time(self):
        m = CalendarManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="start_time"):
            m.create_event(title="t", start_time="bogus", end_time=_iso(hour=1))

    def test_bad_end_time(self):
        m = CalendarManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="end_time"):
            m.create_event(title="t", start_time=_iso(), end_time="bogus")

    def test_end_before_start(self):
        m = CalendarManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="after start_time"):
            m.create_event(title="t", start_time=_iso(hour=2), end_time=_iso(hour=1))

    def test_equal_times_rejected(self):
        m = CalendarManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError):
            m.create_event(title="t", start_time=_iso(), end_time=_iso())

    def test_invalid_status(self):
        m = CalendarManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="Invalid status"):
            m.create_event(
                title="t", start_time=_iso(), end_time=_iso(hour=1), status="maybe",
            )

    def test_too_many_attendees(self):
        m = CalendarManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="attendees"):
            m.create_event(
                title="t", start_time=_iso(), end_time=_iso(hour=1),
                attendees=[f"p{i}@x.com" for i in range(101)],
            )

    def test_description_too_long(self):
        m = CalendarManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="description"):
            m.create_event(
                title="t", start_time=_iso(), end_time=_iso(hour=1),
                description="d" * 8_001,
            )

    @pytest.mark.parametrize("rm", [-1, 43_201])
    def test_reminder_out_of_range(self, rm):
        m = CalendarManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="reminder_minutes"):
            m.create_event(
                title="t", start_time=_iso(), end_time=_iso(hour=1), reminder_minutes=rm,
            )

    def test_reminder_zero_allowed(self):
        m = CalendarManager(provider="memory", enabled=True)
        ev = m.create_event(
            title="t", start_time=_iso(), end_time=_iso(hour=1), reminder_minutes=0,
        )
        assert ev.reminder_minutes == 0

    def test_update_fields(self):
        m = CalendarManager(provider="memory", enabled=True)
        ev = m.create_event(title="t", start_time=_iso(), end_time=_iso(hour=1))
        updated = m.update_event(ev.event_id, {"title": "t2", "status": "tentative"})
        assert updated is not None
        assert updated.title == "t2" and updated.status == "tentative"

    def test_update_validates_merged_times(self):
        m = CalendarManager(provider="memory", enabled=True)
        ev = m.create_event(
            title="t", start_time=_iso(day=2), end_time=_iso(day=2, hour=1),
        )
        # moving start past end must fail even though each field alone is valid
        with pytest.raises(RegistryError, match="after start_time"):
            m.update_event(ev.event_id, {"start_time": _iso(day=2, hour=2)})

    def test_update_validates_status(self):
        m = CalendarManager(provider="memory", enabled=True)
        ev = m.create_event(title="t", start_time=_iso(), end_time=_iso(hour=1))
        with pytest.raises(RegistryError, match="Invalid status"):
            m.update_event(ev.event_id, {"status": "nope"})

    def test_update_empty_title_rejected(self):
        m = CalendarManager(provider="memory", enabled=True)
        ev = m.create_event(title="t", start_time=_iso(), end_time=_iso(hour=1))
        with pytest.raises(RegistryError, match="title"):
            m.update_event(ev.event_id, {"title": ""})

    def test_update_ignores_protected_fields(self):
        m = CalendarManager(provider="memory", enabled=True)
        ev = m.create_event(title="t", start_time=_iso(), end_time=_iso(hour=1))
        updated = m.update_event(ev.event_id, {"event_id": "hacked", "created_at": "x"})
        assert updated is not None and updated.event_id == ev.event_id

    def test_update_missing_returns_none(self):
        m = CalendarManager(provider="memory", enabled=True)
        assert m.update_event("nope", {"title": "x"}) is None

    def test_delete(self):
        m = CalendarManager(provider="memory", enabled=True)
        ev = m.create_event(title="t", start_time=_iso(), end_time=_iso(hour=1))
        assert m.delete_event(ev.event_id) is True
        assert m.delete_event(ev.event_id) is False

    def test_stats_via_manager(self):
        m = CalendarManager(provider="memory", enabled=True)
        m.create_event(title="t", start_time=_iso(), end_time=_iso(hour=1), tags=["x"])
        assert m.get_stats().total == 1

    def test_close(self):
        m = CalendarManager(provider="memory", enabled=True)
        m.create_event(title="t", start_time=_iso(), end_time=_iso(hour=1))
        m.close()
        assert m.list_events() == []


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_default_disabled(self):
        reset_calendar_manager()
        m = get_calendar_manager()
        assert m.enabled is False

    def test_set_and_reset(self):
        m = CalendarManager(provider="memory", enabled=True)
        set_calendar_manager(m)
        assert get_calendar_manager() is m
        reset_calendar_manager()
        assert get_calendar_manager() is not m


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_parallel_create(self):
        p = InMemoryCalendarProvider()
        errors: list[Exception] = []

        def worker(i: int) -> None:
            try:
                for j in range(20):
                    p.create_event(CalendarEvent(
                        title=f"e{i}-{j}",
                        start_time=_iso(day=1, minute=j % 60),
                        end_time=_iso(day=1, hour=1, minute=j % 60),
                    ))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(p.list_events()) == 80


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------

class TestProtocolCompliance:
    def test_inmemory_satisfies_protocol(self):
        assert isinstance(InMemoryCalendarProvider(), CalendarProvider)

    def test_null_satisfies_protocol(self):
        assert isinstance(NullCalendarProvider(), CalendarProvider)

    def test_manager_provider_satisfies_protocol(self):
        m = CalendarManager(provider="memory", enabled=True)
        assert isinstance(m.provider, CalendarProvider)
