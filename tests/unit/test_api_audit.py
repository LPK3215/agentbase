"""Tests for D2: Audit query API endpoints.

Covers:
- Authentication (no key → 401, valid key → 200)
- Pagination (page/page_size/has_next)
- Filtering (actor/action/resource/result/since/until)
- Count endpoint
- Audit disabled (returns empty/zero)
- Audit enabled (returns recorded events)
"""
from __future__ import annotations

import time
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

    # Enable audit logging
    app_config.audit.enabled = True
    app_config.audit.provider = "sqlite"
    app_config.audit.db_dir = str(tmp_path / "data")

    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)

    fake_agent = MagicMock()
    runtime.get_agent = lambda name=None: fake_agent
    runtime.list_agents = lambda: ["default"]

    def fake_get_agent_config(name=None):
        return AgentConfig(
            name=name or "default",
            description="Test agent",
            system_prompt="You are a test agent.",
            tools=["echo"],
        )

    runtime.get_agent_config = fake_get_agent_config

    return runtime


@pytest.fixture
def client_no_auth(mock_runtime):
    """Client with auth disabled (dev mode)."""
    reset_runtime()
    import os

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
def client_with_auth(mock_runtime):
    """Client with API key auth enabled."""
    reset_runtime()
    import os

    old_key = os.environ.get("AGENTBASE_API_KEY", "")
    os.environ["AGENTBASE_API_KEY"] = "test-audit-key"
    try:
        app = create_app(runtime=mock_runtime)
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        if old_key:
            os.environ["AGENTBASE_API_KEY"] = old_key
        else:
            os.environ.pop("AGENTBASE_API_KEY", None)
        reset_runtime()


@pytest.fixture
def audit_manager(mock_runtime):
    """Get the audit manager from the factory (forces initialization)."""
    return mock_runtime.factory.audit_manager


def _record_sample_events(manager, n=5):
    """Record n sample audit events for testing."""
    from agentbase.core.audit import AuditEvent

    events = []
    for i in range(n):
        event = AuditEvent(
            actor=f"user{i}@example.com" if i < 3 else "admin@example.com",
            action="agent.invoke" if i % 2 == 0 else "document.delete",
            resource=f"agent:default" if i % 2 == 0 else f"doc:{i}",
            result="success" if i < 4 else "failure",
            detail={"index": i},
        )
        events.append(manager.record_event(
            actor=event.actor,
            action=event.action,
            resource=event.resource,
            result=event.result,
            detail=event.detail,
        ))
    return events


# ---------------------------------------------------------------------------
# Authentication tests
# ---------------------------------------------------------------------------


class TestAuditAuth:
    def test_no_auth_returns_200_when_auth_disabled(self, client_no_auth):
        """When AGENTBASE_API_KEY is not set, audit endpoints are accessible."""
        resp = client_no_auth.get("/audit/events")
        assert resp.status_code == 200

    def test_auth_required_returns_401_without_key(self, client_with_auth):
        """When auth is enabled, missing API key returns 401."""
        resp = client_with_auth.get("/audit/events")
        assert resp.status_code == 401
        data = resp.json()
        assert "error" in data

    def test_auth_passes_with_valid_key(self, client_with_auth):
        """Valid API key allows access to audit endpoints."""
        resp = client_with_auth.get(
            "/audit/events",
            headers={"X-API-Key": "test-audit-key"},
        )
        assert resp.status_code == 200

    def test_auth_rejects_wrong_key(self, client_with_auth):
        """Wrong API key returns 401."""
        resp = client_with_auth.get(
            "/audit/events",
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    def test_bearer_token_works(self, client_with_auth):
        """Bearer token auth also works for audit endpoints."""
        resp = client_with_auth.get(
            "/audit/events",
            headers={"Authorization": "Bearer test-audit-key"},
        )
        assert resp.status_code == 200

    def test_count_endpoint_requires_auth(self, client_with_auth):
        """Count endpoint also requires authentication."""
        resp = client_with_auth.get("/audit/events/count")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Empty results (audit disabled or no events)
# ---------------------------------------------------------------------------


class TestAuditEmpty:
    def test_empty_events_list(self, client_no_auth):
        """With no events recorded, returns empty list with total=0."""
        resp = client_no_auth.get("/audit/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["page"] == 1
        assert data["has_next"] is False

    def test_empty_count(self, client_no_auth):
        """Count endpoint returns 0 when no events."""
        resp = client_no_auth.get("/audit/events/count")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


# ---------------------------------------------------------------------------
# Data retrieval tests
# ---------------------------------------------------------------------------


class TestAuditQuery:
    def test_list_returns_events(self, client_no_auth, audit_manager):
        """Recorded events are returned by the API."""
        events = _record_sample_events(audit_manager, n=5)
        resp = client_no_auth.get("/audit/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert len(data["items"]) == 5
        # Verify event structure
        item = data["items"][0]
        assert "id" in item
        assert "actor" in item
        assert "action" in item
        assert "resource" in item
        assert "result" in item
        assert "detail" in item
        assert "timestamp" in item

    def test_count_returns_total(self, client_no_auth, audit_manager):
        """Count endpoint returns total event count."""
        _record_sample_events(audit_manager, n=5)
        resp = client_no_auth.get("/audit/events/count")
        assert resp.status_code == 200
        assert resp.json()["count"] == 5

    def test_events_ordered_desc_by_timestamp(self, client_no_auth, audit_manager):
        """Events should be in descending timestamp order (newest first)."""
        _record_sample_events(audit_manager, n=3)
        # Add a small delay to ensure different timestamps
        time.sleep(0.05)
        from agentbase.core.audit import AuditEvent

        audit_manager.record_event(
            actor="late_user",
            action="config.update",
            resource="config",
            result="success",
        )
        resp = client_no_auth.get("/audit/events")
        data = resp.json()
        assert data["total"] == 4
        # The last recorded event should be first (newest)
        assert data["items"][0]["actor"] == "late_user"


# ---------------------------------------------------------------------------
# Pagination tests
# ---------------------------------------------------------------------------


class TestAuditPagination:
    def test_page_size_limits_results(self, client_no_auth, audit_manager):
        """page_size limits the number of items returned."""
        _record_sample_events(audit_manager, n=10)
        resp = client_no_auth.get("/audit/events?page=1&page_size=3")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 3
        assert data["total"] == 10
        assert data["page"] == 1
        assert data["page_size"] == 3
        assert data["has_next"] is True

    def test_page_2_returns_next_items(self, client_no_auth, audit_manager):
        """Page 2 returns the next batch of items."""
        _record_sample_events(audit_manager, n=10)
        resp = client_no_auth.get("/audit/events?page=2&page_size=3")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 3
        assert data["page"] == 2
        assert data["has_next"] is True

    def test_last_page_has_no_next(self, client_no_auth, audit_manager):
        """Last page has has_next=False."""
        _record_sample_events(audit_manager, n=5)
        resp = client_no_auth.get("/audit/events?page=2&page_size=3")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["has_next"] is False

    def test_page_size_max_100(self, client_no_auth, audit_manager):
        """page_size is capped at 100."""
        _record_sample_events(audit_manager, n=3)
        resp = client_no_auth.get("/audit/events?page=1&page_size=500")
        # FastAPI Query(le=100) should reject > 100
        assert resp.status_code == 422

    def test_page_must_be_ge_1(self, client_no_auth):
        """page must be >= 1."""
        resp = client_no_auth.get("/audit/events?page=0")
        assert resp.status_code == 422

    def test_page_size_must_be_ge_1(self, client_no_auth):
        """page_size must be >= 1."""
        resp = client_no_auth.get("/audit/events?page_size=0")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Filter tests
# ---------------------------------------------------------------------------


class TestAuditFilter:
    def test_filter_by_actor(self, client_no_auth, audit_manager):
        """Filter events by actor."""
        _record_sample_events(audit_manager, n=5)
        resp = client_no_auth.get("/audit/events?actor=user0@example.com")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["actor"] == "user0@example.com"

    def test_filter_by_action(self, client_no_auth, audit_manager):
        """Filter events by action type."""
        _record_sample_events(audit_manager, n=5)
        resp = client_no_auth.get("/audit/events?action=agent.invoke")
        assert resp.status_code == 200
        data = resp.json()
        # Events at index 0, 2, 4 have action=agent.invoke
        assert data["total"] == 3
        for item in data["items"]:
            assert item["action"] == "agent.invoke"

    def test_filter_by_resource(self, client_no_auth, audit_manager):
        """Filter events by resource."""
        _record_sample_events(audit_manager, n=5)
        resp = client_no_auth.get("/audit/events?resource=agent:default")
        assert resp.status_code == 200
        data = resp.json()
        # Events at index 0, 2, 4 have resource=agent:default
        assert data["total"] == 3

    def test_filter_by_result(self, client_no_auth, audit_manager):
        """Filter events by result."""
        _record_sample_events(audit_manager, n=5)
        resp = client_no_auth.get("/audit/events?result=failure")
        assert resp.status_code == 200
        data = resp.json()
        # Only event at index 4 has result=failure
        assert data["total"] == 1
        assert data["items"][0]["result"] == "failure"

    def test_filter_by_since(self, client_no_auth, audit_manager):
        """Filter events by since (inclusive lower bound)."""
        events = _record_sample_events(audit_manager, n=3)
        since_ts = events[1].timestamp
        resp = client_no_auth.get(f"/audit/events?since={since_ts}")
        assert resp.status_code == 200
        data = resp.json()
        # Should include events with timestamp >= since_ts
        assert data["total"] >= 2

    def test_filter_by_until(self, client_no_auth, audit_manager):
        """Filter events by until (exclusive upper bound)."""
        events = _record_sample_events(audit_manager, n=3)
        until_ts = events[2].timestamp
        resp = client_no_auth.get(f"/audit/events?until={until_ts}")
        assert resp.status_code == 200
        data = resp.json()
        # Should only include events with timestamp < until_ts
        for item in data["items"]:
            assert item["timestamp"] < until_ts

    def test_combined_filters(self, client_no_auth, audit_manager):
        """Multiple filters work together (AND logic)."""
        _record_sample_events(audit_manager, n=5)
        resp = client_no_auth.get(
            "/audit/events?actor=user0@example.com&action=agent.invoke"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["actor"] == "user0@example.com"
        assert data["items"][0]["action"] == "agent.invoke"

    def test_count_with_filter(self, client_no_auth, audit_manager):
        """Count endpoint also respects filters."""
        _record_sample_events(audit_manager, n=5)
        resp = client_no_auth.get("/audit/events/count?action=agent.invoke")
        assert resp.status_code == 200
        assert resp.json()["count"] == 3


# ---------------------------------------------------------------------------
# Audit disabled tests
# ---------------------------------------------------------------------------


class TestAuditDisabled:
    def test_disabled_returns_empty_list(self, tmp_path):
        """When audit is disabled, /audit/events returns empty list."""
        from agentbase.bootstrap import RuntimeContext

        app_config = AppConfig()
        app_config.runtime.workspace_dir = str(tmp_path / "workspace")
        app_config.runtime.config_dir = "configs"
        app_config.runtime.default_agent = "default"
        app_config.audit.enabled = False

        runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)
        runtime.get_agent = lambda name=None: MagicMock()
        runtime.list_agents = lambda: ["default"]

        reset_runtime()
        import os

        os.environ.pop("AGENTBASE_API_KEY", None)
        try:
            app = create_app(runtime=runtime)
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/audit/events")
                assert resp.status_code == 200
                data = resp.json()
                assert data["items"] == []
                assert data["total"] == 0

                resp = c.get("/audit/events/count")
                assert resp.status_code == 200
                assert resp.json()["count"] == 0
        finally:
            reset_runtime()


# ---------------------------------------------------------------------------
# AgentFactory audit_manager property tests
# ---------------------------------------------------------------------------


class TestFactoryAuditManager:
    def test_audit_manager_lazy_init(self, tmp_path):
        """audit_manager is lazily initialized on first access."""
        from agentbase.bootstrap import RuntimeContext

        app_config = AppConfig()
        app_config.runtime.workspace_dir = str(tmp_path / "workspace")
        app_config.runtime.config_dir = "configs"
        app_config.runtime.default_agent = "default"
        app_config.audit.enabled = True
        app_config.audit.db_dir = str(tmp_path / "data")

        runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)

        # Before first access, _audit_manager is None
        assert runtime.factory._audit_manager is None

        # First access creates it
        mgr = runtime.factory.audit_manager
        assert mgr is not None
        assert mgr.enabled is True

        # Second access returns the same instance
        assert runtime.factory.audit_manager is mgr

    def test_audit_manager_disabled(self, tmp_path):
        """When audit.enabled=False, audit_manager uses NullAuditProvider."""
        from agentbase.bootstrap import RuntimeContext

        app_config = AppConfig()
        app_config.runtime.workspace_dir = str(tmp_path / "workspace")
        app_config.runtime.config_dir = "configs"
        app_config.runtime.default_agent = "default"
        app_config.audit.enabled = False

        runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)
        mgr = runtime.factory.audit_manager
        assert mgr.enabled is False
