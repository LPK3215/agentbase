"""Tests for the conversation history API endpoints.

Covers:
- GET /conversations — list (filterable, paginated)
- GET /conversations/stats — aggregate statistics
- GET /conversations/{thread_id} — get history with messages
- PATCH /conversations/{thread_id} — update metadata
- DELETE /conversations/{thread_id} — delete conversation
- Disabled manager returns empty results
- 404 for non-existent conversations
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agentbase.api import (
    _reset_conversation_manager,
    create_app,
    reset_runtime,
)
from agentbase.bootstrap import RuntimeContext
from agentbase.config.schema import AgentConfig, AppConfig


@pytest.fixture
def mock_runtime(tmp_path):
    """Create a mock RuntimeContext with conversations enabled."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    app_config.conversation.enabled = True
    app_config.conversation.provider = "memory"

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
    """Client with conversations enabled, no auth."""
    reset_runtime()
    _reset_conversation_manager()
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
        _reset_conversation_manager()


@pytest.fixture
def client_disabled(tmp_path):
    """Client with conversations disabled."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    app_config.conversation.enabled = False

    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)
    runtime.get_agent = lambda name=None: MagicMock()
    runtime.list_agents = lambda: ["default"]
    runtime.get_agent_config = lambda name=None: AgentConfig(name=name or "default")

    reset_runtime()
    _reset_conversation_manager()
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
        _reset_conversation_manager()


def _record_sample(client, thread_id="t1", agent="default", user_id="u1"):
    """Record a sample conversation via the manager directly."""
    from agentbase.api import _get_conversation_manager

    mgr = _get_conversation_manager()
    mgr.record_conversation(
        thread_id=thread_id,
        agent_name=agent,
        user_id=user_id,
        messages=[
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ],
        title="Test Conversation",
    )


class TestListConversations:
    """GET /conversations"""

    def test_empty_list(self, client):
        resp = client.get("/conversations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_with_data(self, client):
        _record_sample(client, "t1", "default", "u1")
        _record_sample(client, "t2", "default", "u2")
        resp = client.get("/conversations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    def test_list_filter_by_user(self, client):
        _record_sample(client, "t1", "default", "u1")
        _record_sample(client, "t2", "default", "u2")
        resp = client.get("/conversations?user_id=u1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["thread_id"] == "t1"

    def test_list_filter_by_agent(self, client):
        _record_sample(client, "t1", "agent1", "u1")
        _record_sample(client, "t2", "agent2", "u2")
        resp = client.get("/conversations?agent_name=agent1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["thread_id"] == "t1"

    def test_list_pagination(self, client):
        for i in range(5):
            _record_sample(client, f"t{i}", "a", "u")
        resp = client.get("/conversations?limit=2&offset=0")
        data = resp.json()
        assert len(data["items"]) == 2
        resp2 = client.get("/conversations?limit=2&offset=2")
        data2 = resp2.json()
        assert len(data2["items"]) == 2

    def test_list_excludes_messages(self, client):
        _record_sample(client, "t1", "default", "u1")
        resp = client.get("/conversations")
        data = resp.json()
        assert "messages" not in data["items"][0]
        assert "message_count" in data["items"][0]

    def test_list_sort_by_message_count(self, client):
        _record_sample(client, "t1", "a", "u")
        from agentbase.api import _get_conversation_manager
        mgr = _get_conversation_manager()
        mgr.record_conversation(
            thread_id="t2", agent_name="a", user_id="u",
            messages=[{"role": "user", "content": "A"}, {"role": "assistant", "content": "B"}, {"role": "user", "content": "C"}],
        )
        resp = client.get("/conversations?sort_by=message_count&sort_order=desc")
        data = resp.json()
        assert data["items"][0]["thread_id"] == "t2"
        assert data["items"][0]["message_count"] == 3


class TestGetConversationStats:
    """GET /conversations/stats"""

    def test_empty_stats(self, client):
        resp = client.get("/conversations/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_conversations"] == 0
        assert data["total_messages"] == 0

    def test_stats_with_data(self, client):
        _record_sample(client, "t1", "a1", "u1")
        _record_sample(client, "t2", "a2", "u2")
        resp = client.get("/conversations/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_conversations"] == 2
        assert data["total_messages"] == 4  # 2 per conversation

    def test_stats_filter_by_user(self, client):
        _record_sample(client, "t1", "a", "u1")
        _record_sample(client, "t2", "a", "u2")
        resp = client.get("/conversations/stats?user_id=u1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_conversations"] == 1


class TestGetConversationHistory:
    """GET /conversations/{thread_id}"""

    def test_get_existing(self, client):
        _record_sample(client, "t1", "default", "u1")
        resp = client.get("/conversations/t1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["thread_id"] == "t1"
        assert data["agent_name"] == "default"
        assert data["user_id"] == "u1"
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["content"] == "Hello"

    def test_get_without_messages(self, client):
        _record_sample(client, "t1", "default", "u1")
        resp = client.get("/conversations/t1?include_messages=false")
        assert resp.status_code == 200
        data = resp.json()
        assert "messages" not in data
        assert data["message_count"] == 2

    def test_get_not_found(self, client):
        resp = client.get("/conversations/nonexistent")
        assert resp.status_code == 404


class TestUpdateConversation:
    """PATCH /conversations/{thread_id}"""

    def test_update_title(self, client):
        _record_sample(client, "t1", "default", "u1")
        resp = client.patch("/conversations/t1", json={"title": "New Title"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "New Title"

    def test_update_tags(self, client):
        _record_sample(client, "t1", "default", "u1")
        resp = client.patch("/conversations/t1", json={"tags": ["tag1", "tag2"]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["tags"] == ["tag1", "tag2"]

    def test_update_archived(self, client):
        _record_sample(client, "t1", "default", "u1")
        resp = client.patch("/conversations/t1", json={"archived": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["archived"] is True

    def test_update_metadata(self, client):
        _record_sample(client, "t1", "default", "u1")
        resp = client.patch("/conversations/t1", json={"metadata": {"key": "value"}})
        assert resp.status_code == 200
        data = resp.json()
        assert data["metadata"]["key"] == "value"

    def test_update_not_found(self, client):
        resp = client.patch("/conversations/nonexistent", json={"title": "Test"})
        assert resp.status_code == 404

    def test_update_multiple_fields(self, client):
        _record_sample(client, "t1", "default", "u1")
        resp = client.patch("/conversations/t1", json={
            "title": "Updated",
            "tags": ["new"],
            "archived": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Updated"
        assert data["tags"] == ["new"]
        assert data["archived"] is True


class TestDeleteConversation:
    """DELETE /conversations/{thread_id}"""

    def test_delete_existing(self, client):
        _record_sample(client, "t1", "default", "u1")
        resp = client.delete("/conversations/t1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is True
        # Verify it's gone
        resp2 = client.get("/conversations/t1")
        assert resp2.status_code == 404

    def test_delete_not_found(self, client):
        resp = client.delete("/conversations/nonexistent")
        assert resp.status_code == 404


class TestDisabledManager:
    """When conversations are disabled."""

    def test_list_empty(self, client_disabled):
        resp = client_disabled.get("/conversations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_stats_empty(self, client_disabled):
        resp = client_disabled.get("/conversations/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_conversations"] == 0

    def test_get_not_found(self, client_disabled):
        resp = client_disabled.get("/conversations/t1")
        assert resp.status_code == 404

    def test_delete_not_found(self, client_disabled):
        resp = client_disabled.delete("/conversations/t1")
        assert resp.status_code == 404

    def test_update_not_found(self, client_disabled):
        resp = client_disabled.patch("/conversations/t1", json={"title": "Test"})
        assert resp.status_code == 404
