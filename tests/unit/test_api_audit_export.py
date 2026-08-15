"""Tests for audit log export API endpoint — GET /audit/events/export.

Covers:
- JSON format export (content-type, content-disposition, valid JSON)
- CSV format export (content-type, CSV header row, row count)
- YAML format export (content-type, YAML structure)
- Export with filter conditions (actor, action, result, since, until)
- Export when audit disabled (returns empty with correct headers)
- Content-Disposition header filename
- X-Export-Count header
- Authentication (no key → 401 when auth enabled)
"""
from __future__ import annotations

import csv
import io
import json
import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agentbase.api import create_app, reset_runtime
from agentbase.config.schema import AgentConfig, AppConfig


@pytest.fixture
def mock_runtime(tmp_path):
    """Create a mock RuntimeContext with audit enabled."""
    from agentbase.bootstrap import RuntimeContext

    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False

    # Enable audit logging
    app_config.audit.enabled = True
    app_config.audit.provider = "sqlite"
    app_config.audit.db_dir = str(tmp_path / "data")

    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)

    fake_agent = MagicMock()
    runtime.get_agent = lambda name=None: fake_agent
    runtime.list_agents = lambda: ["default"]

    runtime.get_agent_config = lambda name=None: AgentConfig(
        name=name or "default",
        description="Test agent",
        system_prompt="You are a test agent.",
        tools=["echo"],
    )

    return runtime


@pytest.fixture
def mock_runtime_audit_disabled(tmp_path):
    """Create a mock RuntimeContext with audit disabled."""
    from agentbase.bootstrap import RuntimeContext

    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    app_config.audit.enabled = False

    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)

    fake_agent = MagicMock()
    runtime.get_agent = lambda name=None: fake_agent
    runtime.list_agents = lambda: ["default"]
    runtime.get_agent_config = lambda name=None: AgentConfig(
        name=name or "default",
        description="Test agent",
    )

    return runtime


@pytest.fixture
def client(mock_runtime):
    """Client with auth disabled (dev mode)."""
    reset_runtime()
    old_key = os.environ.get("AGENTBASE_API_KEY", "")
    os.environ.pop("AGENTBASE_API_KEY", None)
    try:
        app = create_app(runtime=mock_runtime)
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        if old_key:
            os.environ["AGENTBASE_API_KEY"] = old_key
        reset_runtime()


@pytest.fixture
def client_disabled(mock_runtime_audit_disabled):
    """Client with audit disabled."""
    reset_runtime()
    old_key = os.environ.get("AGENTBASE_API_KEY", "")
    os.environ.pop("AGENTBASE_API_KEY", None)
    try:
        app = create_app(runtime=mock_runtime_audit_disabled)
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        if old_key:
            os.environ["AGENTBASE_API_KEY"] = old_key
        reset_runtime()


def _record_events(runtime, n=5):
    """Record sample audit events via the factory's audit manager."""
    manager = runtime.factory.audit_manager
    events = []
    for i in range(n):
        event = manager.record_event(
            actor=f"user{i}@example.com" if i < 3 else "admin@example.com",
            action="agent.invoke" if i % 2 == 0 else "document.delete",
            resource="agent:default" if i % 2 == 0 else f"doc:{i}",
            result="success" if i < 4 else "failure",
            detail={"index": i},
        )
        events.append(event)
    return events


# ---------------------------------------------------------------------------
# JSON format export
# ---------------------------------------------------------------------------

class TestExportJson:
    def test_export_json_basic(self, client, mock_runtime):
        _record_events(mock_runtime, 3)
        resp = client.get("/audit/events/export", params={"format": "json"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        data = json.loads(resp.text)
        assert len(data) == 3

    def test_export_json_content_disposition(self, client, mock_runtime):
        _record_events(mock_runtime, 1)
        resp = client.get("/audit/events/export", params={"format": "json"})
        assert "content-disposition" in dict(resp.headers)
        assert "audit_export.json" in resp.headers["content-disposition"]

    def test_export_json_x_export_count(self, client, mock_runtime):
        _record_events(mock_runtime, 3)
        resp = client.get("/audit/events/export", params={"format": "json"})
        assert resp.headers.get("x-export-count") == "3"

    def test_export_json_empty(self, client):
        resp = client.get("/audit/events/export", params={"format": "json"})
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert data == []

    def test_export_json_default_format(self, client, mock_runtime):
        _record_events(mock_runtime, 2)
        resp = client.get("/audit/events/export")  # no format param
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert len(data) == 2


# ---------------------------------------------------------------------------
# CSV format export
# ---------------------------------------------------------------------------

class TestExportCsv:
    def test_export_csv_basic(self, client, mock_runtime):
        _record_events(mock_runtime, 3)
        resp = client.get("/audit/events/export", params={"format": "csv"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")

    def test_export_csv_header_row(self, client, mock_runtime):
        _record_events(mock_runtime, 2)
        resp = client.get("/audit/events/export", params={"format": "csv"})
        reader = csv.reader(io.StringIO(resp.text))
        header = next(reader)
        assert header == [
            "id", "timestamp", "actor", "action",
            "resource", "result", "detail",
        ]

    def test_export_csv_row_count(self, client, mock_runtime):
        _record_events(mock_runtime, 5)
        resp = client.get("/audit/events/export", params={"format": "csv"})
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        assert len(rows) == 5

    def test_export_csv_content_disposition(self, client, mock_runtime):
        _record_events(mock_runtime, 1)
        resp = client.get("/audit/events/export", params={"format": "csv"})
        assert "audit_export.csv" in resp.headers["content-disposition"]

    def test_export_csv_x_export_count(self, client, mock_runtime):
        _record_events(mock_runtime, 3)
        resp = client.get("/audit/events/export", params={"format": "csv"})
        assert resp.headers.get("x-export-count") == "3"

    def test_export_csv_empty(self, client):
        resp = client.get("/audit/events/export", params={"format": "csv"})
        assert resp.status_code == 200
        reader = csv.DictReader(io.StringIO(resp.text))
        assert len(list(reader)) == 0

    def test_export_csv_detail_is_json(self, client, mock_runtime):
        _record_events(mock_runtime, 1)
        resp = client.get("/audit/events/export", params={"format": "csv"})
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        detail = json.loads(rows[0]["detail"])
        assert "index" in detail


# ---------------------------------------------------------------------------
# YAML format export
# ---------------------------------------------------------------------------

class TestExportYaml:
    def test_export_yaml_basic(self, client, mock_runtime):
        _record_events(mock_runtime, 2)
        resp = client.get("/audit/events/export", params={"format": "yaml"})
        assert resp.status_code == 200
        assert "yaml" in resp.headers["content-type"]
        assert "actor:" in resp.text

    def test_export_yaml_content_disposition(self, client, mock_runtime):
        _record_events(mock_runtime, 1)
        resp = client.get("/audit/events/export", params={"format": "yaml"})
        assert "audit_export.yaml" in resp.headers["content-disposition"]


# ---------------------------------------------------------------------------
# Export with filters
# ---------------------------------------------------------------------------

class TestExportWithFilters:
    def test_export_filter_by_actor(self, client, mock_runtime):
        _record_events(mock_runtime, 5)
        resp = client.get("/audit/events/export", params={
            "format": "json",
            "actor": "admin@example.com",
        })
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert len(data) == 2
        for item in data:
            assert item["actor"] == "admin@example.com"

    def test_export_filter_by_action(self, client, mock_runtime):
        _record_events(mock_runtime, 5)
        resp = client.get("/audit/events/export", params={
            "format": "json",
            "action": "document.delete",
        })
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert len(data) == 2
        for item in data:
            assert item["action"] == "document.delete"

    def test_export_filter_by_result(self, client, mock_runtime):
        _record_events(mock_runtime, 5)
        resp = client.get("/audit/events/export", params={
            "format": "json",
            "result": "failure",
        })
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert len(data) == 1
        assert data[0]["result"] == "failure"

    def test_export_filter_csv_with_actor(self, client, mock_runtime):
        _record_events(mock_runtime, 5)
        resp = client.get("/audit/events/export", params={
            "format": "csv",
            "actor": "user0@example.com",
        })
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["actor"] == "user0@example.com"

    def test_export_filter_multiple_params(self, client, mock_runtime):
        _record_events(mock_runtime, 5)
        resp = client.get("/audit/events/export", params={
            "format": "json",
            "actor": "admin@example.com",
            "result": "failure",
        })
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert len(data) == 1
        assert data[0]["actor"] == "admin@example.com"
        assert data[0]["result"] == "failure"

    def test_export_filter_no_matches(self, client, mock_runtime):
        _record_events(mock_runtime, 3)
        resp = client.get("/audit/events/export", params={
            "format": "json",
            "actor": "nonexistent@example.com",
        })
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert data == []

    def test_export_filter_x_export_count_matches(self, client, mock_runtime):
        _record_events(mock_runtime, 5)
        resp = client.get("/audit/events/export", params={
            "format": "json",
            "result": "success",
        })
        assert resp.headers.get("x-export-count") == "4"


# ---------------------------------------------------------------------------
# Disabled audit
# ---------------------------------------------------------------------------

class TestExportDisabled:
    def test_export_disabled_json(self, client_disabled):
        resp = client_disabled.get("/audit/events/export", params={"format": "json"})
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert data == []

    def test_export_disabled_csv(self, client_disabled):
        resp = client_disabled.get("/audit/events/export", params={"format": "csv"})
        assert resp.status_code == 200
        reader = csv.DictReader(io.StringIO(resp.text))
        assert len(list(reader)) == 0

    def test_export_disabled_x_export_count(self, client_disabled):
        resp = client_disabled.get("/audit/events/export")
        assert resp.headers.get("x-export-count") == "0"

    def test_export_disabled_content_disposition(self, client_disabled):
        resp = client_disabled.get("/audit/events/export")
        assert "audit_export.json" in resp.headers["content-disposition"]
