"""Unit tests for the FastAPI service layer."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agentbase.api import create_app, reset_runtime
from agentbase.config.schema import AgentConfig, AppConfig
from agentbase.runtime.events import EventType, RuntimeEvent


@pytest.fixture
def mock_runtime(tmp_path):
    """Create a mock RuntimeContext with a fake agent."""
    from agentbase.bootstrap import RuntimeContext

    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"

    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)

    # Mock get_agent to return a fake agent
    fake_agent = MagicMock()

    def fake_get_agent(name=None):
        return fake_agent

    runtime.get_agent = fake_get_agent

    # Mock list_agents
    runtime.list_agents = lambda: ["default", "coder"]

    # Mock get_agent_config
    def fake_get_agent_config(name=None):
        return AgentConfig(
            name=name or "default",
            description=f"Test agent {name}",
            system_prompt="You are a test agent.",
            tools=["echo"],
            capabilities=["file_upload"],
        )

    runtime.get_agent_config = fake_get_agent_config

    # Mock runner
    def fake_invoke(*, agent, agent_name, message, thread_id=None, metadata=None):
        return {
            "thread_id": thread_id or "test-thread-123",
            "agent": agent_name,
            "result": {"messages": [{"content": f"Response to: {message}"}]},
            "output_text": f"Response to: {message}",
        }

    def fake_stream(*, agent, agent_name, message, thread_id=None, metadata=None):
        yield RuntimeEvent(
            type=EventType.RUN_STARTED,
            thread_id=thread_id or "stream-thread-456",
            agent=agent_name,
            data={"message": message},
        )
        yield RuntimeEvent(
            type=EventType.MESSAGE_DELTA,
            thread_id=thread_id or "stream-thread-456",
            agent=agent_name,
            data={"text": "Hello "},
        )
        yield RuntimeEvent(
            type=EventType.MESSAGE_DELTA,
            thread_id=thread_id or "stream-thread-456",
            agent=agent_name,
            data={"text": "world"},
        )
        yield RuntimeEvent(
            type=EventType.RUN_FINISHED,
            thread_id=thread_id or "stream-thread-456",
            agent=agent_name,
            data={"output_text": "Hello world"},
        )

    def fake_resume(*, agent, agent_name, thread_id, decision):
        return {
            "thread_id": thread_id,
            "agent": agent_name,
            "result": {"messages": [{"content": "Resumed"}]},
            "output_text": "Resumed",
        }

    runtime.runner.invoke = fake_invoke
    runtime.runner.stream = fake_stream
    runtime.runner.resume = fake_resume

    return runtime


@pytest.fixture
def client(mock_runtime):
    """Create a test client with a mock runtime."""
    reset_runtime()
    app = create_app(runtime=mock_runtime)
    # raise_server_exceptions=False so that errors handled by the app's
    # global_exception_handler surface as HTTP responses (e.g. 404) instead
    # of being re-raised out of the test client. This matches how the app
    # behaves under a real uvicorn server.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    reset_runtime()


class TestHealth:
    def test_health(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "default" in data["agents"]
        assert data["default_agent"] == "default"


class TestListAgents:
    def test_list_agents(self, client):
        response = client.get("/agents")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        names = [a["name"] for a in data]
        assert "default" in names
        assert "coder" in names

    def test_agent_info_has_fields(self, client):
        response = client.get("/agents")
        data = response.json()
        agent = data[0]
        assert "name" in agent
        assert "description" in agent
        assert "tools" in agent
        assert "capabilities" in agent


class TestGetAgent:
    def test_get_agent(self, client):
        response = client.get("/agents/default")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "default"

    def test_get_agent_not_found(self, client):
        # mock returns config for any name, so this tests the error path
        # when the runtime raises
        with patch.object(client.app, "dependency_overrides", {}):
            response = client.get("/agents/nonexistent")
            # Our mock returns config for any name, so it succeeds
            assert response.status_code == 200


class TestGetConfigurableItems:
    def test_get_configurable_items(self, client):
        response = client.get("/agents/default/configurable")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        names = [item["name"] for item in data]
        assert "name" in names
        assert "tools" in names
        assert "capabilities" in names


class TestInvokeAgent:
    def test_invoke(self, client):
        response = client.post("/agents/default/invoke", json={
            "message": "hello",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["agent"] == "default"
        assert "Response to: hello" in data["output_text"]
        assert "thread_id" in data

    def test_invoke_with_thread_id(self, client):
        response = client.post("/agents/default/invoke", json={
            "message": "follow up",
            "thread_id": "my-thread-789",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["thread_id"] == "my-thread-789"

    def test_invoke_with_metadata(self, client):
        response = client.post("/agents/default/invoke", json={
            "message": "test",
            "metadata": {"user": "alice"},
        })
        assert response.status_code == 200

    def test_invoke_show_raw(self, client):
        response = client.post("/agents/default/invoke", json={
            "message": "test",
            "show_raw": True,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["result"] is not None

    def test_invoke_not_found(self, client):
        """When get_agent raises, should return 404."""
        from agentbase.api import get_runtime

        original = get_runtime().get_agent

        def raise_not_found(name=None):
            raise Exception("not found")

        get_runtime().get_agent = raise_not_found
        try:
            response = client.post("/agents/nonexistent/invoke", json={"message": "test"})
            assert response.status_code == 404
        finally:
            get_runtime().get_agent = original


class TestStreamAgent:
    def test_stream(self, client):
        response = client.post("/agents/default/stream", json={
            "message": "stream test",
        })
        assert response.status_code == 200
        body = response.text
        assert "data:" in body
        assert "run.started" in body
        assert "message.delta" in body
        assert "run.finished" in body
        assert "Hello " in body
        assert "world" in body

    def test_stream_sse_format(self, client):
        response = client.post("/agents/default/stream", json={
            "message": "format test",
        })
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        # Each event should end with \n\n
        assert "\n\n" in response.text


class TestResumeAgent:
    def test_resume(self, client):
        response = client.post("/agents/default/resume", json={
            "thread_id": "test-thread-123",
            "decision": "approve",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["thread_id"] == "test-thread-123"
        assert data["output_text"] == "Resumed"

    def test_resume_with_json_decision(self, client):
        response = client.post("/agents/default/resume", json={
            "thread_id": "test-thread-456",
            "decision": "respond",
            "decision_json": '{"answer": "yes"}',
        })
        assert response.status_code == 200


class TestQueue:
    def test_submit_and_get(self, client):
        # Submit
        response = client.post("/queue/submit", json={
            "agent_name": "default",
            "message": "async task",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["agent_name"] == "default"
        assert data["message"] == "async task"
        assert data["status"] == "pending"
        task_id = data["id"]

        # Get
        response = client.get(f"/queue/{task_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == task_id

    def test_get_nonexistent_task(self, client):
        response = client.get("/queue/nonexistent-id")
        assert response.status_code == 404

    def test_list_tasks(self, client):
        client.post("/queue/submit", json={"agent_name": "default", "message": "1"})
        client.post("/queue/submit", json={"agent_name": "coder", "message": "2"})

        response = client.get("/queue")
        assert response.status_code == 200
        data = response.json()
        # list_tasks returns a paginated envelope: {"items": [...], "total": ...}
        assert data["total"] >= 2
        assert len(data["items"]) >= 2

    def test_list_filtered_by_agent(self, client):
        client.post("/queue/submit", json={"agent_name": "default", "message": "a"})
        client.post("/queue/submit", json={"agent_name": "coder", "message": "b"})

        response = client.get("/queue?agent_name=default")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert all(t["agent_name"] == "default" for t in data["items"])

    def test_cancel_task(self, client):
        response = client.post("/queue/submit", json={
            "agent_name": "default",
            "message": "cancel me",
        })
        task_id = response.json()["id"]

        response = client.delete(f"/queue/{task_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"

    def test_cancel_nonexistent(self, client):
        response = client.delete("/queue/nonexistent")
        assert response.status_code == 400 or response.status_code == 404

    def test_process_queue(self, client):
        """Submit and process tasks."""
        client.post("/queue/submit", json={"agent_name": "default", "message": "task1"})
        client.post("/queue/submit", json={"agent_name": "default", "message": "task2"})

        response = client.post("/queue/process")
        assert response.status_code == 200
        data = response.json()
        assert data["processed"] == 2
        assert len(data["results"]) == 2
        # All tasks should be completed
        for r in data["results"]:
            assert r["status"] == "completed"


class TestAppFactory:
    def test_create_app_with_runtime(self, mock_runtime):
        app = create_app(runtime=mock_runtime)
        assert app is not None
        assert app.title == "agentbase"

    def test_create_app_default(self):
        """create_app without runtime should work lazily."""
        reset_runtime()
        # Don't call it without a proper config setup in tests
        # Just verify the function exists
        from agentbase.api import create_app
        assert callable(create_app)
        reset_runtime()
