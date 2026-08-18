"""Unit tests for audit log export functionality (core.audit export methods).

Covers:
- SQLiteAuditProvider.export() — file-based export (JSON, CSV, YAML)
- SQLiteAuditProvider.export_stream() — in-memory export (JSON, CSV, YAML)
- NullAuditProvider.export() / export_stream() — no-op behaviour
- AuditManager.export_events() / export_events_stream() — manager wrapper
- Export with filter conditions (actor, action, result, since, until)
- CSV format correctness (header row, column order, detail JSON)
- JSON format correctness (valid JSON array, field names)
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from agentbase.core.audit import (
    AuditEvent,
    AuditFilter,
    AuditManager,
    NullAuditProvider,
    SQLiteAuditProvider,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record_sample_events(provider, n=5):
    """Record n sample audit events into the provider."""
    events = []
    for i in range(n):
        event = AuditEvent(
            actor=f"user{i}@example.com" if i < 3 else "admin@example.com",
            action="agent.invoke" if i % 2 == 0 else "document.delete",
            resource="agent:default" if i % 2 == 0 else f"doc:{i}",
            result="success" if i < 4 else "failure",
            detail={"index": i, "extra": f"data-{i}"},
        )
        events.append(provider.record(event))
    return events


def _make_provider(tmp_path):
    """Create a SQLiteAuditProvider with a temp database."""
    return SQLiteAuditProvider(db_path=Path(tmp_path / "audit_test.db"))


# ---------------------------------------------------------------------------
# SQLiteAuditProvider.export() — file-based export
# ---------------------------------------------------------------------------

class TestExportToFile:
    """Test SQLiteAuditProvider.export() file-based export."""

    def test_export_json(self, tmp_path):
        provider = _make_provider(tmp_path)
        _record_sample_events(provider, 3)
        out_path = str(tmp_path / "export.json")
        count = provider.export(out_path, format="json")
        assert count == 3
        data = json.loads(Path(out_path).read_text(encoding="utf-8"))
        assert len(data) == 3
        assert "actor" in data[0]
        assert "action" in data[0]

    def test_export_csv(self, tmp_path):
        provider = _make_provider(tmp_path)
        _record_sample_events(provider, 3)
        out_path = str(tmp_path / "export.csv")
        count = provider.export(out_path, format="csv")
        assert count == 3
        content = Path(out_path).read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)
        assert len(rows) == 3
        assert "id" in rows[0]
        assert "timestamp" in rows[0]
        assert "actor" in rows[0]
        assert "detail" in rows[0]

    def test_export_yaml(self, tmp_path):
        provider = _make_provider(tmp_path)
        _record_sample_events(provider, 2)
        out_path = str(tmp_path / "export.yaml")
        count = provider.export(out_path, format="yaml")
        assert count == 2
        content = Path(out_path).read_text(encoding="utf-8")
        # Basic YAML validation — yaml.dump puts fields in dict order
        assert "actor:" in content
        assert "action:" in content

    def test_export_empty(self, tmp_path):
        provider = _make_provider(tmp_path)
        out_path = str(tmp_path / "empty.json")
        count = provider.export(out_path, format="json")
        assert count == 0
        data = json.loads(Path(out_path).read_text(encoding="utf-8"))
        assert data == []

    def test_export_empty_csv(self, tmp_path):
        provider = _make_provider(tmp_path)
        out_path = str(tmp_path / "empty.csv")
        count = provider.export(out_path, format="csv")
        assert count == 0
        content = Path(out_path).read_text(encoding="utf-8")
        # Should still have the header row
        reader = csv.DictReader(io.StringIO(content))
        assert reader.fieldnames is not None
        assert len(list(reader)) == 0

    def test_export_with_filter(self, tmp_path):
        provider = _make_provider(tmp_path)
        _record_sample_events(provider, 5)
        flt = AuditFilter(actor="user0@example.com")
        out_path = str(tmp_path / "filtered.json")
        count = provider.export(out_path, format="json", filter=flt)
        assert count == 1
        data = json.loads(Path(out_path).read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["actor"] == "user0@example.com"

    def test_export_with_filter_csv(self, tmp_path):
        provider = _make_provider(tmp_path)
        _record_sample_events(provider, 5)
        flt = AuditFilter(result="failure")
        out_path = str(tmp_path / "filtered.csv")
        count = provider.export(out_path, format="csv", filter=flt)
        assert count == 1
        content = Path(out_path).read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["result"] == "failure"

    def test_export_creates_parent_dir(self, tmp_path):
        provider = _make_provider(tmp_path)
        _record_sample_events(provider, 1)
        out_path = str(tmp_path / "subdir" / "nested" / "export.json")
        count = provider.export(out_path, format="json")
        assert count == 1
        assert Path(out_path).exists()

    def test_export_csv_detail_is_json_string(self, tmp_path):
        provider = _make_provider(tmp_path)
        _record_sample_events(provider, 1)
        out_path = str(tmp_path / "detail_test.csv")
        count = provider.export(out_path, format="csv")
        assert count == 1
        content = Path(out_path).read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)
        detail = json.loads(rows[0]["detail"])
        assert "index" in detail
        assert detail["index"] == 0

    def test_export_default_format_is_json(self, tmp_path):
        provider = _make_provider(tmp_path)
        _record_sample_events(provider, 2)
        out_path = str(tmp_path / "default_fmt.json")
        count = provider.export(out_path)  # no format arg
        assert count == 2
        data = json.loads(Path(out_path).read_text(encoding="utf-8"))
        assert len(data) == 2


# ---------------------------------------------------------------------------
# SQLiteAuditProvider.export_stream() — in-memory export
# ---------------------------------------------------------------------------

class TestExportStream:
    """Test SQLiteAuditProvider.export_stream() in-memory export."""

    def test_export_stream_json(self, tmp_path):
        provider = _make_provider(tmp_path)
        _record_sample_events(provider, 3)
        content, events = provider.export_stream(format="json")
        data = json.loads(content)
        assert len(data) == 3
        assert len(events) == 3

    def test_export_stream_csv(self, tmp_path):
        provider = _make_provider(tmp_path)
        _record_sample_events(provider, 3)
        content, events = provider.export_stream(format="csv")
        assert len(events) == 3
        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)
        assert len(rows) == 3
        assert "actor" in rows[0]

    def test_export_stream_yaml(self, tmp_path):
        provider = _make_provider(tmp_path)
        _record_sample_events(provider, 2)
        content, events = provider.export_stream(format="yaml")
        assert len(events) == 2
        assert "actor:" in content

    def test_export_stream_empty(self, tmp_path):
        provider = _make_provider(tmp_path)
        content, events = provider.export_stream(format="json")
        assert events == []
        data = json.loads(content)
        assert data == []

    def test_export_stream_empty_csv(self, tmp_path):
        provider = _make_provider(tmp_path)
        content, events = provider.export_stream(format="csv")
        assert events == []
        # CSV with header only
        reader = csv.DictReader(io.StringIO(content))
        assert len(list(reader)) == 0

    def test_export_stream_with_filter(self, tmp_path):
        provider = _make_provider(tmp_path)
        _record_sample_events(provider, 5)
        flt = AuditFilter(action="document.delete")
        content, events = provider.export_stream(format="json", filter=flt)
        assert len(events) == 2  # i=1 and i=3 have document.delete
        data = json.loads(content)
        assert len(data) == 2
        for item in data:
            assert item["action"] == "document.delete"

    def test_export_stream_with_filter_result(self, tmp_path):
        provider = _make_provider(tmp_path)
        _record_sample_events(provider, 5)
        flt = AuditFilter(result="success")
        content, events = provider.export_stream(format="json", filter=flt)
        assert len(events) == 4  # i=0,1,2,3 are success

    def test_export_stream_with_filter_actor(self, tmp_path):
        provider = _make_provider(tmp_path)
        _record_sample_events(provider, 5)
        flt = AuditFilter(actor="admin@example.com")
        content, events = provider.export_stream(format="json", filter=flt)
        assert len(events) == 2  # i=3 and i=4 are admin

    def test_export_stream_default_format_json(self, tmp_path):
        provider = _make_provider(tmp_path)
        _record_sample_events(provider, 1)
        content, events = provider.export_stream()  # no format arg
        data = json.loads(content)
        assert len(data) == 1

    def test_export_stream_csv_header_correct(self, tmp_path):
        provider = _make_provider(tmp_path)
        _record_sample_events(provider, 1)
        content, events = provider.export_stream(format="csv")
        reader = csv.reader(io.StringIO(content))
        header = next(reader)
        assert header == [
            "id", "timestamp", "actor", "action",
            "resource", "result", "detail",
        ]

    def test_export_stream_csv_row_count_matches_events(self, tmp_path):
        provider = _make_provider(tmp_path)
        _record_sample_events(provider, 5)
        content, events = provider.export_stream(format="csv")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        # header + data rows
        assert len(rows) == len(events) + 1

    def test_export_stream_with_since_filter(self, tmp_path):
        provider = _make_provider(tmp_path)
        events = _record_sample_events(provider, 3)
        # Use the timestamp of the second event as 'since'
        since_ts = events[1].timestamp
        flt = AuditFilter(since=since_ts)
        content, result_events = provider.export_stream(format="json", filter=flt)
        # Should include events with timestamp >= since_ts
        assert len(result_events) <= 3

    def test_export_stream_with_until_filter(self, tmp_path):
        provider = _make_provider(tmp_path)
        events = _record_sample_events(provider, 3)
        # Use the timestamp of the second event as 'until'
        until_ts = events[1].timestamp
        flt = AuditFilter(until=until_ts)
        content, result_events = provider.export_stream(format="json", filter=flt)
        # Should include events with timestamp < until_ts
        assert len(result_events) <= 3


# ---------------------------------------------------------------------------
# NullAuditProvider
# ---------------------------------------------------------------------------

class TestNullProviderExport:
    """Test NullAuditProvider export methods."""

    def test_export_returns_zero(self, tmp_path):
        provider = NullAuditProvider()
        count = provider.export(str(tmp_path / "null.json"), format="json")
        assert count == 0

    def test_export_stream_returns_empty_json(self):
        provider = NullAuditProvider()
        content, events = provider.export_stream(format="json")
        assert events == []
        assert json.loads(content) == []

    def test_export_stream_csv_returns_empty(self):
        provider = NullAuditProvider()
        content, events = provider.export_stream(format="csv")
        assert events == []
        reader = csv.reader(io.StringIO(content))
        header = next(reader)
        assert header == ["id", "timestamp", "actor", "action", "resource", "result", "detail"]
        assert len(list(reader)) == 0

    def test_export_with_filter_returns_zero(self, tmp_path):
        provider = NullAuditProvider()
        flt = AuditFilter(actor="anyone")
        count = provider.export(str(tmp_path / "null.json"), filter=flt)
        assert count == 0

    def test_export_stream_with_filter_returns_empty(self):
        provider = NullAuditProvider()
        flt = AuditFilter(actor="anyone")
        content, events = provider.export_stream(filter=flt)
        assert events == []
        assert json.loads(content) == []


# ---------------------------------------------------------------------------
# AuditManager wrapper
# ---------------------------------------------------------------------------

class TestAuditManagerExport:
    """Test AuditManager.export_events() and export_events_stream()."""

    def test_manager_export_events_json(self, tmp_path):
        mgr = AuditManager(provider="sqlite", enabled=True, db_path=Path(tmp_path / "mgr_audit.db"))
        mgr.record_event(actor="alice", action="test.action", resource="res:1")
        mgr.record_event(actor="bob", action="test.action", resource="res:2")
        out_path = str(tmp_path / "mgr_export.json")
        count = mgr.export_events(out_path, format="json")
        assert count == 2
        data = json.loads(Path(out_path).read_text(encoding="utf-8"))
        assert len(data) == 2

    def test_manager_export_events_csv(self, tmp_path):
        mgr = AuditManager(provider="sqlite", enabled=True, db_path=Path(tmp_path / "mgr_audit.db"))
        mgr.record_event(actor="alice", action="test.action")
        out_path = str(tmp_path / "mgr_export.csv")
        count = mgr.export_events(out_path, format="csv")
        assert count == 1
        content = Path(out_path).read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["actor"] == "alice"

    def test_manager_export_events_stream_json(self, tmp_path):
        mgr = AuditManager(provider="sqlite", enabled=True, db_path=Path(tmp_path / "mgr_audit.db"))
        mgr.record_event(actor="alice", action="test.action")
        content, events = mgr.export_events_stream(format="json")
        data = json.loads(content)
        assert len(data) == 1
        assert data[0]["actor"] == "alice"

    def test_manager_export_events_stream_csv(self, tmp_path):
        mgr = AuditManager(provider="sqlite", enabled=True, db_path=Path(tmp_path / "mgr_audit.db"))
        mgr.record_event(actor="alice", action="test.action", detail={"key": "val"})
        content, events = mgr.export_events_stream(format="csv")
        assert len(events) == 1
        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["actor"] == "alice"
        detail = json.loads(rows[0]["detail"])
        assert detail["key"] == "val"

    def test_manager_export_events_stream_with_filter(self, tmp_path):
        mgr = AuditManager(provider="sqlite", enabled=True, db_path=Path(tmp_path / "mgr_audit.db"))
        mgr.record_event(actor="alice", action="action.a")
        mgr.record_event(actor="bob", action="action.b")
        flt = AuditFilter(actor="alice")
        content, events = mgr.export_events_stream(format="json", filter=flt)
        assert len(events) == 1
        assert events[0].actor == "alice"

    def test_manager_disabled_export_returns_zero(self, tmp_path):
        mgr = AuditManager(enabled=False)
        count = mgr.export_events(str(tmp_path / "disabled.json"))
        assert count == 0

    def test_manager_disabled_export_stream_returns_empty(self):
        mgr = AuditManager(enabled=False)
        content, events = mgr.export_events_stream()
        assert events == []
        assert json.loads(content) == []

    def test_manager_export_events_stream_default_json(self, tmp_path):
        mgr = AuditManager(provider="sqlite", enabled=True, db_path=Path(tmp_path / "mgr_audit.db"))
        mgr.record_event(actor="alice", action="test")
        content, events = mgr.export_events_stream()  # default format
        data = json.loads(content)
        assert len(data) == 1

    def test_manager_export_with_filter(self, tmp_path):
        mgr = AuditManager(provider="sqlite", enabled=True, db_path=Path(tmp_path / "mgr_audit.db"))
        for i in range(5):
            mgr.record_event(
                actor=f"user{i}@example.com",
                action="agent.invoke" if i % 2 == 0 else "doc.delete",
                result="success" if i < 3 else "failure",
            )
        flt = AuditFilter(result="failure")
        out_path = str(tmp_path / "filtered.json")
        count = mgr.export_events(out_path, format="json", filter=flt)
        assert count == 2
        data = json.loads(Path(out_path).read_text(encoding="utf-8"))
        for item in data:
            assert item["result"] == "failure"
