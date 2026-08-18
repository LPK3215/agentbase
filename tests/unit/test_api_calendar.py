"""Tests for the calendar API endpoints.

Covers:
- GET /calendar — list (filters, pagination)
- POST /calendar — create (validation 400)
- GET /calendar/stats — aggregate statistics
- GET /calendar/upcoming — upcoming events
- GET /calendar/{event_id} — get detail (404)
- PATCH /calendar/{event_id} — update (404 / 400)
- DELETE /calendar/{event_id} — delete (404)
- Disabled manager returns empty/zero values
- Route ordering: /calendar/stats and /calendar/upcoming not captured by /{event_id}
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agentbase.api import (
    _reset_calendar_manager,
    create_app,
    reset_runtime,
)
from agentbase.bootstrap import RuntimeContext
from agentbase.config.schema import AgentConfig, AppConfig


def _iso(**kwargs) -> str:
    base = {"year": 2020, "month": 1, "day": 1, "hour": 0, "minute": 0}
    base.update(kwargs)
    return datetime(**base, tzinfo=UTC).isoformat()  # type: ignore[arg-type]


def _future(hours: float) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


@pytest.fixture
def mock_runtime(tmp_path):
    """Runtime with the calendar service enabled."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    app_config.calendar.enabled = True
    app_config.calendar.provider = "memory"

    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)
    fake_agent = MagicMock()
    runtime.get_agent = lambda name=None: fake_agent
    runtime.list_agents = lambda: ["default"]
    runtime.get_agent_config = lambda name=None: AgentConfig(
        name=name or "default", description="Test agent",
    )
    return runtime


def _make_client(runtime):
    reset_runtime()
    _reset_calendar_manager()
    old_key = os.environ.get("AGENTBASE_API_KEY", "")
    os.environ.pop("AGENTBASE_API_KEY", None)
    try:
        app = create_app(runtime=runtime)
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        if old_key:
            os.environ["AGENTBASE_API_KEY"] = old_key
        reset_runtime()
        _reset_calendar_manager()


@pytest.fixture
def client(mock_runtime):
    yield from _make_client(mock_runtime)


@pytest.fixture
def client_disabled(tmp_path):
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    # calendar.enabled defaults to False
    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)
    fake_agent = MagicMock()
    runtime.get_agent = lambda name=None: fake_agent
    runtime.list_agents = lambda: ["default"]
    runtime.get_agent_config = lambda name=None: AgentConfig(
        name=name or "default", description="Test agent",
    )
    yield from _make_client(runtime)


def _create(client, **overrides) -> dict:
    payload = {
        "title": "Team sync",
        "start_time": _future(24),
        "end_time": _future(25),
    }
    payload.update(overrides)
    resp = client.post("/calendar", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# List / create
# ---------------------------------------------------------------------------

class TestListAndCreate:
    def test_create_returns_event(self, client):
        data = _create(client, location="room 1", tags=["work"], reminder_minutes=15)
        assert data["event_id"]
        assert data["title"] == "Team sync"
        assert data["status"] == "confirmed"

    def test_list_empty(self, client):
        resp = client.get("/calendar")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_list_returns_created(self, client):
        _create(client)
        _create(client, title="Second", start_time=_future(48), end_time=_future(49))
        resp = client.get("/calendar")
        body = resp.json()
        assert body["total"] == 2
        starts = [e["start_time"] for e in body["items"]]
        assert starts == sorted(starts)

    def test_list_filter_status(self, client):
        _create(client)
        _create(client, title="T", status="tentative")
        resp = client.get("/calendar", params={"status": "tentative"})
        items = resp.json()["items"]
        assert len(items) == 1 and items[0]["status"] == "tentative"

    def test_list_filter_tag(self, client):
        _create(client, tags=["work"])
        _create(client, title="P", tags=["private"])
        resp = client.get("/calendar", params={"tag": "private"})
        assert len(resp.json()["items"]) == 1

    def test_list_filter_location_substring(self, client):
        _create(client, location="Beijing HQ")
        _create(client, title="X", location="Shanghai HQ")
        resp = client.get("/calendar", params={"location": "beijing"})
        assert len(resp.json()["items"]) == 1

    def test_list_pagination(self, client):
        for i in range(5):
            _create(client, title=f"e{i}", start_time=_future(24 + i), end_time=_future(25 + i))
        resp = client.get("/calendar", params={"page": 2, "page_size": 2})
        body = resp.json()
        assert body["total"] == 2 and body["page"] == 2
        assert [e["title"] for e in body["items"]] == ["e2", "e3"]

    def test_create_invalid_time_400(self, client):
        resp = client.post("/calendar", json={
            "title": "t", "start_time": "nope", "end_time": _future(1),
        })
        assert resp.status_code == 400

    def test_create_end_before_start_400(self, client):
        resp = client.post("/calendar", json={
            "title": "t", "start_time": _future(2), "end_time": _future(1),
        })
        assert resp.status_code == 400

    def test_create_invalid_status_400(self, client):
        resp = client.post("/calendar", json={
            "title": "t", "start_time": _future(1), "end_time": _future(2),
            "status": "maybe",
        })
        assert resp.status_code == 400

    def test_create_missing_title_422(self, client):
        resp = client.post("/calendar", json={"start_time": _future(1), "end_time": _future(2)})
        assert resp.status_code == 422  # FastAPI required-field validation


# ---------------------------------------------------------------------------
# Stats / upcoming
# ---------------------------------------------------------------------------

class TestStatsAndUpcoming:
    def test_stats(self, client):
        _create(client)
        _create(client, title="T2", status="cancelled", start_time=_iso(), end_time=_iso(hour=10))
        resp = client.get("/calendar/stats")
        body = resp.json()
        assert body["total"] == 2
        assert body["upcoming"] == 1 and body["past"] == 1
        assert body["by_status"]["cancelled"] == 1

    def test_upcoming(self, client):
        _create(client, title="future", start_time=_future(24), end_time=_future(25))
        _create(client, title="past", start_time=_iso(), end_time=_iso(hour=10))
        resp = client.get("/calendar/upcoming")
        items = resp.json()["items"]
        assert [e["title"] for e in items] == ["future"]

    def test_upcoming_limit(self, client):
        for i in range(5):
            _create(client, title=f"u{i}", start_time=_future(24 + i), end_time=_future(25 + i))
        resp = client.get("/calendar/upcoming", params={"limit": 2})
        assert len(resp.json()["items"]) == 2

    def test_route_ordering_stats_not_captured(self, client):
        assert client.get("/calendar/stats").status_code == 200
        assert client.get("/calendar/upcoming").status_code == 200


# ---------------------------------------------------------------------------
# Detail / update / delete
# ---------------------------------------------------------------------------

class TestDetailUpdateDelete:
    def test_get_detail(self, client):
        ev = _create(client)
        resp = client.get(f"/calendar/{ev['event_id']}")
        assert resp.status_code == 200
        assert resp.json()["event_id"] == ev["event_id"]

    def test_get_missing_404(self, client):
        assert client.get("/calendar/nope").status_code == 404

    def test_update_fields(self, client):
        ev = _create(client)
        resp = client.patch(f"/calendar/{ev['event_id']}", json={"title": "Renamed"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "Renamed"

    def test_update_conflict_400(self, client):
        ev = _create(client, start_time=_future(10), end_time=_future(11))
        resp = client.patch(
            f"/calendar/{ev['event_id']}",
            json={"start_time": _future(12)},
        )
        assert resp.status_code == 400

    def test_update_missing_404(self, client):
        resp = client.patch("/calendar/nope", json={"title": "x"})
        assert resp.status_code == 404

    def test_delete(self, client):
        ev = _create(client)
        assert client.delete(f"/calendar/{ev['event_id']}").status_code == 200
        assert client.get(f"/calendar/{ev['event_id']}").status_code == 404

    def test_delete_missing_404(self, client):
        assert client.delete("/calendar/nope").status_code == 404


# ---------------------------------------------------------------------------
# Disabled service
# ---------------------------------------------------------------------------

class TestDisabled:
    def test_list_empty(self, client_disabled):
        resp = client_disabled.get("/calendar")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_stats_zero(self, client_disabled):
        resp = client_disabled.get("/calendar/stats")
        assert resp.json()["total"] == 0

    def test_upcoming_empty(self, client_disabled):
        assert client_disabled.get("/calendar/upcoming").json()["items"] == []

    def test_detail_404(self, client_disabled):
        assert client_disabled.get("/calendar/any").status_code == 404

    def test_delete_404(self, client_disabled):
        assert client_disabled.delete("/calendar/any").status_code == 404
