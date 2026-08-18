"""Tests for the scheduled task API endpoints.

Covers:
- GET /schedules — list (filterable, paginated)
- POST /schedules — create (interval / cron, validation 400)
- GET /schedules/stats — aggregate statistics
- GET /schedules/{task_id} — get detail (404)
- PATCH /schedules/{task_id} — update fields (404 / 400)
- DELETE /schedules/{task_id} — delete (404)
- POST /schedules/{task_id}/pause — pause
- POST /schedules/{task_id}/resume — resume
- POST /schedules/{task_id}/trigger — manual trigger
- GET /schedules/{task_id}/runs — execution history (filters, 404)
- Disabled manager returns empty/zero values
- Route ordering: /schedules/stats not captured by /{task_id}
"""
from __future__ import annotations

import os
import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agentbase.api import (
    _reset_schedule_manager,
    create_app,
    reset_runtime,
)
from agentbase.bootstrap import RuntimeContext
from agentbase.config.schema import AgentConfig, AppConfig


@pytest.fixture
def mock_runtime(tmp_path):
    """Create a mock RuntimeContext with scheduling enabled."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    app_config.scheduler.enabled = True
    app_config.scheduler.provider = "memory"
    app_config.scheduler.tick_seconds = 60.0  # keep the loop quiet in tests

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
    """Client with scheduling enabled, no auth."""
    reset_runtime()
    _reset_schedule_manager()
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
        _reset_schedule_manager()


@pytest.fixture
def client_disabled(tmp_path):
    """Client with scheduling disabled."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    # scheduler.enabled defaults to False

    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)
    fake_agent = MagicMock()
    runtime.get_agent = lambda name=None: fake_agent
    runtime.list_agents = lambda: ["default"]
    runtime.get_agent_config = lambda name=None: AgentConfig(
        name=name or "default",
        description="Test",
    )

    reset_runtime()
    _reset_schedule_manager()
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
        _reset_schedule_manager()


def _create_interval_task(client, name="t1", agent="default", interval=3600):
    resp = client.post(
        "/schedules",
        json={
            "name": name,
            "agent_name": agent,
            "message": "hello",
            "schedule_type": "interval",
            "interval_seconds": interval,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _wait_terminal_status(client, task_id, timeout=5.0):
    """Poll /runs until the latest run reaches a terminal status."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/schedules/{task_id}/runs")
        assert resp.status_code == 200
        items = resp.json()["items"]
        if items and items[0]["status"] in ("success", "failed", "skipped"):
            return items[0]
        time.sleep(0.05)
    pytest.fail("run did not reach terminal status in time")


# ---------------------------------------------------------------------------
# GET /schedules
# ---------------------------------------------------------------------------

class TestListSchedules:
    def test_empty(self, client):
        resp = client.get("/schedules")
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_lists_created_tasks(self, client):
        _create_interval_task(client, name="a")
        _create_interval_task(client, name="b")
        resp = client.get("/schedules")
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    def test_filter_by_agent(self, client):
        _create_interval_task(client, name="a", agent="default")
        resp = client.get("/schedules", params={"agent_name": "default"})
        assert resp.json()["total"] == 1
        resp = client.get("/schedules", params={"agent_name": "other"})
        assert resp.json()["total"] == 0

    def test_filter_by_name(self, client):
        _create_interval_task(client, name="daily-report")
        resp = client.get("/schedules", params={"name": "daily"})
        assert resp.json()["total"] == 1

    def test_filter_by_enabled(self, client):
        _create_interval_task(client, name="a")
        resp = client.get("/schedules", params={"enabled": "true"})
        assert resp.json()["total"] == 1
        resp = client.get("/schedules", params={"enabled": "false"})
        assert resp.json()["total"] == 0

    def test_pagination(self, client):
        _create_interval_task(client, name="a")
        _create_interval_task(client, name="b")
        resp = client.get("/schedules", params={"page": 1, "page_size": 1})
        body = resp.json()
        assert body["total"] == 1 and len(body["items"]) == 1
        assert body["page"] == 1 and body["page_size"] == 1

    def test_disabled_returns_empty(self, client_disabled):
        resp = client_disabled.get("/schedules")
        assert resp.status_code == 200
        assert resp.json()["items"] == []


# ---------------------------------------------------------------------------
# POST /schedules
# ---------------------------------------------------------------------------

class TestCreateSchedule:
    def test_create_interval(self, client):
        body = _create_interval_task(client)
        assert body["name"] == "t1"
        assert body["schedule_type"] == "interval"
        assert body["interval_seconds"] == 3600
        assert body["enabled"] is True
        assert body["next_run_at"]

    def test_create_cron(self, client):
        resp = client.post(
            "/schedules",
            json={
                "name": "daily-8am",
                "agent_name": "default",
                "message": "morning report",
                "schedule_type": "cron",
                "cron_expr": "0 8 * * *",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["schedule_type"] == "cron"
        assert body["cron_expr"] == "0 8 * * *"
        assert body["next_run_at"]

    def test_create_cron_invalid_expr_400(self, client):
        resp = client.post(
            "/schedules",
            json={
                "name": "bad",
                "agent_name": "default",
                "schedule_type": "cron",
                "cron_expr": "not a cron",
            },
        )
        assert resp.status_code == 400

    def test_create_cron_missing_expr_400(self, client):
        resp = client.post(
            "/schedules",
            json={
                "name": "bad",
                "agent_name": "default",
                "schedule_type": "cron",
            },
        )
        assert resp.status_code == 400

    def test_create_zero_interval_400(self, client):
        resp = client.post(
            "/schedules",
            json={
                "name": "bad",
                "agent_name": "default",
                "schedule_type": "interval",
                "interval_seconds": 0,
            },
        )
        assert resp.status_code == 400

    def test_create_bad_schedule_type_400(self, client):
        resp = client.post(
            "/schedules",
            json={
                "name": "bad",
                "agent_name": "default",
                "schedule_type": "weekly",
            },
        )
        assert resp.status_code == 400

    def test_create_duplicate_name_400(self, client):
        _create_interval_task(client, name="dup")
        resp = client.post(
            "/schedules",
            json={"name": "dup", "agent_name": "default"},
        )
        assert resp.status_code == 400

    def test_create_missing_required_422(self, client):
        resp = client.post("/schedules", json={"name": "x"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /schedules/stats + route ordering
# ---------------------------------------------------------------------------

class TestScheduleStats:
    def test_empty_stats(self, client):
        resp = client.get("/schedules/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["enabled"] == 0
        assert body["paused"] == 0

    def test_stats_with_tasks(self, client):
        _create_interval_task(client, name="a")
        _create_interval_task(client, name="b")
        resp = client.get("/schedules/stats")
        body = resp.json()
        assert body["total"] == 2
        assert body["enabled"] == 2
        assert body["by_agent"] == {"default": 2}

    def test_stats_route_not_captured_by_task_id(self, client):
        # /schedules/stats must not be treated as /schedules/{task_id}
        resp = client.get("/schedules/stats")
        assert resp.status_code == 200
        assert "total" in resp.json()

    def test_disabled_stats_zero(self, client_disabled):
        resp = client_disabled.get("/schedules/stats")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# GET / PATCH / DELETE /schedules/{task_id}
# ---------------------------------------------------------------------------

class TestTaskDetail:
    def test_get_found(self, client):
        task = _create_interval_task(client)
        resp = client.get(f"/schedules/{task['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == task["id"]

    def test_get_missing_404(self, client):
        resp = client.get("/schedules/nope")
        assert resp.status_code == 404

    def test_patch_message(self, client):
        task = _create_interval_task(client)
        resp = client.patch(f"/schedules/{task['id']}", json={"message": "updated"})
        assert resp.status_code == 200
        assert resp.json()["message"] == "updated"

    def test_patch_schedule_recomputes_next_run(self, client):
        task = _create_interval_task(client)
        resp = client.patch(
            f"/schedules/{task['id']}",
            json={"schedule_type": "cron", "cron_expr": "*/5 * * * *"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["schedule_type"] == "cron"
        assert body["next_run_at"] != task["next_run_at"]

    def test_patch_invalid_cron_400(self, client):
        task = _create_interval_task(client)
        resp = client.patch(
            f"/schedules/{task['id']}",
            json={"schedule_type": "cron", "cron_expr": "bad"},
        )
        assert resp.status_code == 400

    def test_patch_missing_404(self, client):
        resp = client.patch("/schedules/nope", json={"message": "x"})
        assert resp.status_code == 404

    def test_delete(self, client):
        task = _create_interval_task(client)
        resp = client.delete(f"/schedules/{task['id']}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        assert client.get(f"/schedules/{task['id']}").status_code == 404

    def test_delete_missing_404(self, client):
        resp = client.delete("/schedules/nope")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# pause / resume / trigger / runs
# ---------------------------------------------------------------------------

class TestPauseResume:
    def test_pause_resume_cycle(self, client):
        task = _create_interval_task(client)
        resp = client.post(f"/schedules/{task['id']}/pause")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        resp = client.get("/schedules", params={"enabled": "false"})
        assert resp.json()["total"] == 1

        resp = client.post(f"/schedules/{task['id']}/resume")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True
        assert resp.json()["next_run_at"]

    def test_pause_missing_404(self, client):
        assert client.post("/schedules/nope/pause").status_code == 404
        assert client.post("/schedules/nope/resume").status_code == 404


class TestTriggerAndRuns:
    def test_trigger_returns_running_run(self, client):
        task = _create_interval_task(client)
        resp = client.post(f"/schedules/{task['id']}/trigger")
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == task["id"]
        assert body["trigger"] == "manual"
        assert body["status"] in ("running", "success", "failed", "skipped")

    def test_trigger_missing_404(self, client):
        assert client.post("/schedules/nope/trigger").status_code == 404

    def test_runs_history_records_terminal_status(self, client):
        task = _create_interval_task(client)
        client.post(f"/schedules/{task['id']}/trigger")
        run = _wait_terminal_status(client, task["id"])
        assert run["status"] in ("success", "failed", "skipped")
        assert run["finished_at"]
        assert run["duration_ms"] >= 0

    def test_runs_filter_by_trigger(self, client):
        task = _create_interval_task(client)
        client.post(f"/schedules/{task['id']}/trigger")
        _wait_terminal_status(client, task["id"])
        resp = client.get(
            f"/schedules/{task['id']}/runs", params={"trigger": "manual"}
        )
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1
        resp = client.get(
            f"/schedules/{task['id']}/runs", params={"trigger": "schedule"}
        )
        assert resp.json()["total"] == 0

    def test_runs_missing_task_404(self, client):
        assert client.get("/schedules/nope/runs").status_code == 404

    def test_stats_counts_runs(self, client):
        task = _create_interval_task(client)
        client.post(f"/schedules/{task['id']}/trigger")
        _wait_terminal_status(client, task["id"])
        resp = client.get("/schedules/stats")
        body = resp.json()
        assert body["total_runs"] >= 1
        # success + failed + skipped partition the runs
        assert body["successful_runs"] + body["failed_runs"] <= body["total_runs"]

    def test_disabled_trigger_404(self, client_disabled):
        # Null provider returns None → 404 for any task id
        assert client_disabled.post("/schedules/nope/trigger").status_code == 404
