"""Tests for the alert API endpoints.

Covers:
- GET /alerts/rules — list (filters, pagination)
- POST /alerts/rules — create (400 on invalid input)
- GET /alerts/rules/stats — aggregate statistics
- GET /alerts/metrics — supported metrics with live values
- GET/PATCH/DELETE /alerts/rules/{rule_id} — detail / update / delete
- POST /alerts/rules/{rule_id}/evaluate — manual evaluation (fires)
- GET /alerts/events — history (filters)
- POST /alerts/tick — manual full pass
- Disabled manager returns empty values
- Route ordering: /stats and /metrics not captured by /{rule_id}
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agentbase.api import (
    _reset_alert_manager,
    _reset_notification_manager,
    create_app,
    reset_runtime,
)
from agentbase.bootstrap import RuntimeContext
from agentbase.config.schema import AgentConfig, AppConfig


@pytest.fixture
def mock_runtime(tmp_path):
    """Runtime with the alert service enabled."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    app_config.alert.enabled = True
    app_config.alert.provider = "memory"

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
    _reset_alert_manager()
    _reset_notification_manager()
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
        _reset_alert_manager()
        _reset_notification_manager()


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
    # alert.enabled defaults to False
    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)
    fake_agent = MagicMock()
    runtime.get_agent = lambda name=None: fake_agent
    runtime.list_agents = lambda: ["default"]
    runtime.get_agent_config = lambda name=None: AgentConfig(
        name=name or "default", description="Test agent",
    )
    yield from _make_client(runtime)


def _create_rule(client, name="high-errors", **extra) -> dict:
    payload = {"name": name, "metric": "errors_total", "threshold": 100}
    payload.update(extra)
    resp = client.post("/alerts/rules", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _set_metrics(app, value: float) -> None:
    from agentbase.api import _metrics

    _metrics.reset()
    for _ in range(int(value)):
        _metrics.record_error("TEST")


# ---------------------------------------------------------------------------
# Rule list / create / stats / metrics
# ---------------------------------------------------------------------------


def test_list_rules_empty(client):
    body = client.get("/alerts/rules").json()
    assert body["items"] == []
    assert body["total"] == 0


def test_create_rule_defaults(client):
    body = _create_rule(client)
    assert body["name"] == "high-errors"
    assert body["metric"] == "errors_total"
    assert body["operator"] == "gt"
    assert body["threshold"] == 100
    assert body["severity"] == "warning"
    assert body["duration_ticks"] == 1
    assert body["enabled"] is True


def test_create_rule_full(client):
    body = _create_rule(
        client, "sustained", operator="gte", threshold=50, severity="critical",
        duration_ticks=3, cooldown_seconds=600, notify_user_id="alice",
        description="sustained errors",
    )
    assert body["operator"] == "gte"
    assert body["severity"] == "critical"
    assert body["duration_ticks"] == 3
    assert body["cooldown_seconds"] == 600
    assert body["notify_user_id"] == "alice"


def test_create_rule_missing_fields_400(client):
    assert client.post("/alerts/rules", json={"metric": "errors_total"}).status_code == 400
    assert client.post("/alerts/rules", json={"name": "x"}).status_code == 400
    assert client.post("/alerts/rules", json={}).status_code == 400


def test_create_rule_bad_metric_400(client):
    resp = client.post("/alerts/rules", json={"name": "x", "metric": "nope", "threshold": 1})
    assert resp.status_code == 400
    assert "Unsupported metric" in resp.json()["detail"]


def test_create_rule_bad_operator_400(client):
    resp = client.post(
        "/alerts/rules",
        json={"name": "x", "metric": "errors_total", "threshold": 1, "operator": "bad"},
    )
    assert resp.status_code == 400


def test_create_rule_duplicate_name_400(client):
    _create_rule(client, "dup")
    resp = client.post("/alerts/rules", json={"name": "dup", "metric": "errors_total", "threshold": 1})
    assert resp.status_code == 400


def test_list_rules_filters(client):
    _create_rule(client, "a", metric="errors_total")
    _create_rule(client, "b", metric="requests_total")
    _create_rule(client, "c", metric="errors_total", enabled=False)
    body = client.get("/alerts/rules", params={"metric": "errors_total"}).json()
    assert [r["name"] for r in body["items"]] == ["a", "c"]
    body = client.get("/alerts/rules", params={"enabled": "false"}).json()
    assert [r["name"] for r in body["items"]] == ["c"]


def test_stats(client):
    _create_rule(client, "a")
    body = client.get("/alerts/rules/stats").json()
    assert body["total_rules"] == 1
    assert body["enabled_rules"] == 1
    assert body["firing_rules"] == 0


def test_metrics_endpoint(client):
    body = client.get("/alerts/metrics").json()
    names = [i["metric"] for i in body["items"]]
    assert "errors_total" in names
    assert "latency_avg_ms" in names
    assert all("current_value" in i for i in body["items"])


# ---------------------------------------------------------------------------
# Rule detail / update / delete
# ---------------------------------------------------------------------------


def test_get_rule(client):
    created = _create_rule(client)
    body = client.get(f"/alerts/rules/{created['rule_id']}").json()
    assert body["name"] == "high-errors"


def test_get_rule_missing_404(client):
    assert client.get("/alerts/rules/nope").status_code == 404


def test_update_rule(client):
    created = _create_rule(client)
    resp = client.patch(f"/alerts/rules/{created['rule_id']}", json={"threshold": 200, "enabled": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["threshold"] == 200
    assert body["enabled"] is False


def test_update_rule_invalid_operator_400(client):
    created = _create_rule(client)
    resp = client.patch(f"/alerts/rules/{created['rule_id']}", json={"operator": "bad"})
    assert resp.status_code == 400


def test_update_rule_missing_404(client):
    assert client.patch("/alerts/rules/nope", json={"threshold": 1}).status_code == 404


def test_delete_rule(client):
    created = _create_rule(client)
    assert client.delete(f"/alerts/rules/{created['rule_id']}").status_code == 200
    assert client.get(f"/alerts/rules/{created['rule_id']}").status_code == 404
    assert client.delete(f"/alerts/rules/{created['rule_id']}").status_code == 404


# ---------------------------------------------------------------------------
# Evaluation & events
# ---------------------------------------------------------------------------


def test_manual_evaluate_fires(client):
    from agentbase.api import _metrics

    created = _create_rule(client, threshold=5)
    # push errors_total above threshold via the real metrics collector
    _metrics.reset()
    for _ in range(10):
        _metrics.record_error("TEST")
    resp = client.post(f"/alerts/rules/{created['rule_id']}/evaluate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["event"] is not None
    assert body["event"]["state"] == "firing"
    assert body["event"]["value"] == 10.0


def test_manual_evaluate_no_event_when_ok(client):
    from agentbase.api import _metrics

    created = _create_rule(client, threshold=10_000)
    _metrics.reset()
    resp = client.post(f"/alerts/rules/{created['rule_id']}/evaluate")
    assert resp.status_code == 200
    assert resp.json()["event"] is None


def test_manual_evaluate_missing_404(client):
    assert client.post("/alerts/rules/nope/evaluate").status_code == 404


def test_manual_tick(client):
    from agentbase.api import _metrics

    _create_rule(client, "tick-rule", threshold=5)
    _metrics.reset()
    for _ in range(10):
        _metrics.record_error("TEST")
    resp = client.post("/alerts/tick")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["events"][0]["rule_name"] == "tick-rule"


def test_events_history_and_filters(client):
    from agentbase.api import _metrics

    created = _create_rule(client, threshold=5)
    _metrics.reset()
    for _ in range(10):
        _metrics.record_error("TEST")
    client.post(f"/alerts/rules/{created['rule_id']}/evaluate")
    body = client.get("/alerts/events").json()
    assert body["total"] == 1
    assert body["items"][0]["state"] == "firing"
    # filter by state
    body = client.get("/alerts/events", params={"state": "resolved"}).json()
    assert body["total"] == 0
    # filter by rule_id
    body = client.get("/alerts/events", params={"rule_id": created["rule_id"]}).json()
    assert body["total"] == 1


# ---------------------------------------------------------------------------
# Disabled service
# ---------------------------------------------------------------------------


def test_disabled_returns_empty(client_disabled):
    assert client_disabled.get("/alerts/rules").json()["items"] == []
    assert client_disabled.get("/alerts/rules/stats").json()["total_rules"] == 0
    assert client_disabled.get("/alerts/events").json()["items"] == []
    # manual tick on disabled → no events, no crash
    assert client_disabled.post("/alerts/tick").json()["count"] == 0


# ---------------------------------------------------------------------------
# Route ordering
# ---------------------------------------------------------------------------


def test_static_routes_not_captured(client):
    # these must hit their own handlers, not /alerts/rules/{rule_id}
    assert client.get("/alerts/rules/stats").status_code == 200
    assert client.get("/alerts/metrics").status_code == 200


# ---------------------------------------------------------------------------
# Notification integration + queue metrics wiring (regression)
# ---------------------------------------------------------------------------


@pytest.fixture
def client_notify(tmp_path):
    """Runtime with alert + notification + memory queue enabled."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    app_config.alert.enabled = True
    app_config.alert.provider = "memory"
    app_config.notification.enabled = True
    app_config.notification.provider = "memory"
    app_config.queue.provider = "memory"

    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)
    fake_agent = MagicMock()
    runtime.get_agent = lambda name=None: fake_agent
    runtime.list_agents = lambda: ["default"]
    runtime.get_agent_config = lambda name=None: AgentConfig(
        name=name or "default", description="Test agent",
    )
    yield from _make_client(runtime)


def test_queue_submit_increments_metric(client_notify):
    """Regression: /queue/submit must record into the metrics collector."""
    from agentbase.api import _metrics

    _metrics.reset()
    resp = client_notify.post(
        "/queue/submit", json={"agent_name": "default", "message": "hi"}
    )
    assert resp.status_code == 200, resp.text
    snapshot = _metrics.get_snapshot()
    assert snapshot["queue_submitted_total"] == 1.0


def test_alert_fires_and_delivers_notification(client_notify):
    """Regression: firing alert must deliver a notification via the real sink.

    Guards against calling a non-existent manager method (the original bug
    called ``NotificationManager.create`` instead of ``create_notification``
    and the TypeError was silently swallowed by the best-effort wrapper).
    """
    from agentbase.api import _metrics

    _metrics.reset()
    client_notify.post(
        "/queue/submit", json={"agent_name": "default", "message": "hi"}
    )
    client_notify.post(
        "/alerts/rules",
        json={
            "name": "queue-activity",
            "metric": "queue_submitted_total",
            "operator": "gt",
            "threshold": 0,
            "severity": "warning",
            "notify_user_id": "*",
        },
    )
    body = client_notify.post("/alerts/tick").json()
    assert body["count"] == 1
    assert body["events"][0]["state"] == "firing"

    notifications = client_notify.get(
        "/notifications", params={"user_id": "*", "category": "alert"}
    ).json()
    assert notifications["total"] >= 1
    item = notifications["items"][0]
    assert item["category"] == "alert"
    assert item["severity"] == "warning"
    assert "queue-activity" in item["title"]
    assert item["metadata"]["state"] == "firing"
