"""Tests for CeleryRequestQueue — Protocol compliance, factory routing, mock integration.

Since no real Celery broker is available in CI, these tests use mocks
to verify:
1. CeleryRequestQueue Protocol compliance (isinstance check)
2. submit / get_task / list_tasks / cancel / update_task behavior
3. Celery state mapping (PENDING→pending, SUCCESS→completed, etc.)
4. Factory routing via queue_registry
5. Import error handling when celery is not installed
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helper to create a CeleryRequestQueue with mock celery
# ---------------------------------------------------------------------------


def _mock_celery_module():
    """Create a mock celery module for testing."""
    mock_celery = MagicMock()

    # Mock Celery app
    mock_app = MagicMock()
    mock_app.conf = {}
    mock_app.task = MagicMock(return_value=lambda f: f)
    mock_app.AsyncResult = MagicMock()
    mock_app.control = MagicMock()
    mock_app.connection_for_write = MagicMock()
    mock_app.worker_main = MagicMock()
    mock_app.close = MagicMock()

    mock_celery.Celery = MagicMock(return_value=mock_app)
    mock_celery.current_task = MagicMock()

    return mock_celery, mock_app


def _create_celery_queue():
    """Create a CeleryRequestQueue with a mock celery module."""
    from agentbase.core.queue_celery import CeleryRequestQueue

    mock_celery, mock_app = _mock_celery_module()
    old_celery = sys.modules.get("celery")
    sys.modules["celery"] = mock_celery
    try:
        queue = CeleryRequestQueue(broker_url="redis://localhost:6379/0")
    finally:
        if old_celery is not None:
            sys.modules["celery"] = old_celery
        else:
            del sys.modules["celery"]

    return queue, mock_app


# ---------------------------------------------------------------------------
# Protocol compliance tests
# ---------------------------------------------------------------------------


class TestCeleryQueueProtocol:
    def test_is_request_queue(self):
        """CeleryRequestQueue should satisfy the RequestQueue Protocol."""
        from agentbase.core.queue import RequestQueue

        queue, _ = _create_celery_queue()
        assert isinstance(queue, RequestQueue)
        queue.close()

    def test_has_submit(self):
        queue, _ = _create_celery_queue()
        assert hasattr(queue, "submit")
        queue.close()

    def test_has_get_task(self):
        queue, _ = _create_celery_queue()
        assert hasattr(queue, "get_task")
        queue.close()

    def test_has_list_tasks(self):
        queue, _ = _create_celery_queue()
        assert hasattr(queue, "list_tasks")
        queue.close()

    def test_has_cancel(self):
        queue, _ = _create_celery_queue()
        assert hasattr(queue, "cancel")
        queue.close()

    def test_has_update_task(self):
        queue, _ = _create_celery_queue()
        assert hasattr(queue, "update_task")
        queue.close()


# ---------------------------------------------------------------------------
# Import error tests
# ---------------------------------------------------------------------------


class TestCeleryImportError:
    def test_import_error_without_celery(self):
        """When celery is not installed, should raise ImportError."""
        from agentbase.core.queue_celery import CeleryRequestQueue

        old_celery = sys.modules.get("celery")
        if old_celery is not None:
            # celery IS installed — just verify the class exists
            assert CeleryRequestQueue is not None
        else:
            with pytest.raises(ImportError, match="celery"):
                CeleryRequestQueue(broker_url="redis://localhost:6379/0")


# ---------------------------------------------------------------------------
# Celery state mapping tests
# ---------------------------------------------------------------------------


class TestCeleryStateMapping:
    def test_pending(self):
        from agentbase.core.queue_celery import CeleryRequestQueue

        assert CeleryRequestQueue._map_celery_state("PENDING") == "pending"

    def test_started(self):
        from agentbase.core.queue_celery import CeleryRequestQueue

        assert CeleryRequestQueue._map_celery_state("STARTED") == "running"

    def test_success(self):
        from agentbase.core.queue_celery import CeleryRequestQueue

        assert CeleryRequestQueue._map_celery_state("SUCCESS") == "completed"

    def test_failure(self):
        from agentbase.core.queue_celery import CeleryRequestQueue

        assert CeleryRequestQueue._map_celery_state("FAILURE") == "failed"

    def test_revoked(self):
        from agentbase.core.queue_celery import CeleryRequestQueue

        assert CeleryRequestQueue._map_celery_state("REVOKED") == "cancelled"

    def test_retry(self):
        from agentbase.core.queue_celery import CeleryRequestQueue

        assert CeleryRequestQueue._map_celery_state("RETRY") == "pending"

    def test_unknown_state_defaults_to_pending(self):
        from agentbase.core.queue_celery import CeleryRequestQueue

        assert CeleryRequestQueue._map_celery_state("UNKNOWN") == "pending"


# ---------------------------------------------------------------------------
# Task deserialization tests
# ---------------------------------------------------------------------------


class TestTaskFromDict:
    def test_basic_roundtrip(self):
        from agentbase.core.queue import TaskStatus
        from agentbase.core.queue_celery import _task_from_dict

        d = {
            "id": "test-123",
            "agent_name": "default",
            "message": "hello",
            "status": "pending",
        }
        task = _task_from_dict(d)
        assert task.id == "test-123"
        assert task.agent_name == "default"
        assert task.message == "hello"
        assert task.status == TaskStatus.PENDING

    def test_with_status_string(self):
        from agentbase.core.queue import TaskStatus
        from agentbase.core.queue_celery import _task_from_dict

        d = {
            "id": "test-456",
            "status": "completed",
        }
        task = _task_from_dict(d)
        assert task.status == TaskStatus.COMPLETED

    def test_missing_status_defaults_to_pending(self):
        from agentbase.core.queue import TaskStatus
        from agentbase.core.queue_celery import _task_from_dict

        d = {"id": "test-789"}
        task = _task_from_dict(d)
        assert task.status == TaskStatus.PENDING


# ---------------------------------------------------------------------------
# Submit tests (with mocks)
# ---------------------------------------------------------------------------


class TestCelerySubmit:
    def test_submit_returns_task(self):
        queue, mock_app = _create_celery_queue()

        # Mock apply_async to return a mock AsyncResult
        mock_async_result = MagicMock()
        mock_async_result.id = "celery-task-123"
        queue._celery_task.apply_async = MagicMock(return_value=mock_async_result)

        task = queue.submit(
            agent_name="default",
            message="hello",
        )

        assert task.agent_name == "default"
        assert task.message == "hello"
        assert task.status.value == "pending"
        assert task.id in queue._tasks
        queue.close()

    def test_submit_with_metadata(self):
        queue, mock_app = _create_celery_queue()

        mock_async_result = MagicMock()
        mock_async_result.id = "celery-task-456"
        queue._celery_task.apply_async = MagicMock(return_value=mock_async_result)

        task = queue.submit(
            agent_name="test",
            message="run",
            metadata={"key": "value"},
            thread_id="thread-1",
        )

        assert task.thread_id == "thread-1"
        assert task.metadata["key"] == "value"
        queue.close()

    def test_submit_with_priority(self):
        queue, mock_app = _create_celery_queue()

        mock_async_result = MagicMock()
        mock_async_result.id = "celery-task-789"
        queue._celery_task.apply_async = MagicMock(return_value=mock_async_result)

        task = queue.submit(
            agent_name="default",
            message="high priority",
            priority=5,
        )

        assert task.priority == 5
        # Verify apply_async was called with priority
        call_kwargs = queue._celery_task.apply_async.call_args[1]
        assert call_kwargs["priority"] == 5
        queue.close()


# ---------------------------------------------------------------------------
# Get task tests
# ---------------------------------------------------------------------------


class TestCeleryGetTask:
    def test_get_task_existing(self):
        queue, mock_app = _create_celery_queue()

        # Submit a task first
        mock_async_result = MagicMock()
        mock_async_result.id = "celery-1"
        queue._celery_task.apply_async = MagicMock(return_value=mock_async_result)

        task = queue.submit(agent_name="default", message="test")

        # Mock the AsyncResult for get_task
        mock_result = MagicMock()
        mock_result.state = "PENDING"
        mock_result.successful.return_value = False
        mock_result.failed.return_value = False
        mock_app.AsyncResult.return_value = mock_result

        retrieved = queue.get_task(task.id)
        assert retrieved is not None
        assert retrieved.id == task.id
        queue.close()

    def test_get_task_nonexistent(self):
        queue, mock_app = _create_celery_queue()

        result = queue.get_task("nonexistent-id")
        assert result is None
        queue.close()

    def test_get_task_completed(self):
        queue, mock_app = _create_celery_queue()

        # Submit a task
        mock_async_result = MagicMock()
        mock_async_result.id = "celery-2"
        queue._celery_task.apply_async = MagicMock(return_value=mock_async_result)

        task = queue.submit(agent_name="default", message="test")

        # Mock completed state
        mock_result = MagicMock()
        mock_result.state = "SUCCESS"
        mock_result.successful.return_value = True
        mock_result.failed.return_value = False
        mock_result.result = {"output": "done"}
        mock_app.AsyncResult.return_value = mock_result

        retrieved = queue.get_task(task.id)
        assert retrieved is not None
        assert retrieved.status.value == "completed"
        assert retrieved.result == {"output": "done"}
        queue.close()

    def test_get_task_failed(self):
        queue, mock_app = _create_celery_queue()

        mock_async_result = MagicMock()
        mock_async_result.id = "celery-3"
        queue._celery_task.apply_async = MagicMock(return_value=mock_async_result)

        task = queue.submit(agent_name="default", message="test")

        mock_result = MagicMock()
        mock_result.state = "FAILURE"
        mock_result.successful.return_value = False
        mock_result.failed.return_value = True
        mock_result.result = "Task error"
        mock_app.AsyncResult.return_value = mock_result

        retrieved = queue.get_task(task.id)
        assert retrieved is not None
        assert retrieved.status.value == "failed"
        assert "Task error" in retrieved.error
        queue.close()


# ---------------------------------------------------------------------------
# List tasks tests
# ---------------------------------------------------------------------------


class TestCeleryListTasks:
    def test_list_empty(self):
        queue, _ = _create_celery_queue()
        tasks = queue.list_tasks()
        assert tasks == []
        queue.close()

    def test_list_with_tasks(self):
        queue, mock_app = _create_celery_queue()

        # Submit two tasks
        mock_async_result = MagicMock()
        mock_async_result.id = "celery-1"
        queue._celery_task.apply_async = MagicMock(return_value=mock_async_result)

        queue.submit(agent_name="agent1", message="task1")
        queue.submit(agent_name="agent2", message="task2")

        # Mock AsyncResult for listing
        mock_result = MagicMock()
        mock_result.state = "PENDING"
        mock_result.successful.return_value = False
        mock_result.failed.return_value = False
        mock_app.AsyncResult.return_value = mock_result

        tasks = queue.list_tasks()
        assert len(tasks) == 2
        queue.close()

    def test_list_filtered_by_agent(self):
        queue, mock_app = _create_celery_queue()

        mock_async_result = MagicMock()
        mock_async_result.id = "celery-1"
        queue._celery_task.apply_async = MagicMock(return_value=mock_async_result)

        queue.submit(agent_name="agent1", message="task1")
        queue.submit(agent_name="agent2", message="task2")

        mock_result = MagicMock()
        mock_result.state = "PENDING"
        mock_result.successful.return_value = False
        mock_result.failed.return_value = False
        mock_app.AsyncResult.return_value = mock_result

        tasks = queue.list_tasks(agent_name="agent1")
        assert len(tasks) == 1
        assert tasks[0].agent_name == "agent1"
        queue.close()


# ---------------------------------------------------------------------------
# Cancel tests
# ---------------------------------------------------------------------------


class TestCeleryCancel:
    def test_cancel_pending_task(self):
        queue, mock_app = _create_celery_queue()

        mock_async_result = MagicMock()
        mock_async_result.id = "celery-1"
        queue._celery_task.apply_async = MagicMock(return_value=mock_async_result)

        task = queue.submit(agent_name="default", message="test")

        # Mock pending state
        mock_result = MagicMock()
        mock_result.state = "PENDING"
        mock_app.AsyncResult.return_value = mock_result

        result = queue.cancel(task.id)
        assert result is True
        mock_app.control.revoke.assert_called_once()
        queue.close()

    def test_cancel_nonexistent(self):
        queue, _ = _create_celery_queue()
        result = queue.cancel("nonexistent")
        assert result is False
        queue.close()

    def test_cancel_already_completed(self):
        queue, mock_app = _create_celery_queue()

        mock_async_result = MagicMock()
        mock_async_result.id = "celery-1"
        queue._celery_task.apply_async = MagicMock(return_value=mock_async_result)

        task = queue.submit(agent_name="default", message="test")

        # Mock completed state
        mock_result = MagicMock()
        mock_result.state = "SUCCESS"
        mock_app.AsyncResult.return_value = mock_result

        result = queue.cancel(task.id)
        assert result is False
        queue.close()


# ---------------------------------------------------------------------------
# Update task tests
# ---------------------------------------------------------------------------


class TestCeleryUpdateTask:
    def test_update_existing_task(self):
        queue, mock_app = _create_celery_queue()

        mock_async_result = MagicMock()
        mock_async_result.id = "celery-1"
        queue._celery_task.apply_async = MagicMock(return_value=mock_async_result)

        task = queue.submit(agent_name="default", message="test")

        mock_result = MagicMock()
        mock_result.state = "PENDING"
        mock_result.successful.return_value = False
        mock_result.failed.return_value = False
        mock_app.AsyncResult.return_value = mock_result

        updated = queue.update_task(task.id, message="updated message")
        assert updated is not None
        assert updated.message == "updated message"
        queue.close()

    def test_update_nonexistent(self):
        queue, _ = _create_celery_queue()
        result = queue.update_task("nonexistent", message="test")
        assert result is None
        queue.close()


# ---------------------------------------------------------------------------
# Stats and clear tests
# ---------------------------------------------------------------------------


class TestCeleryStats:
    def test_stats_empty(self):
        queue, _ = _create_celery_queue()
        stats = queue.stats()
        assert stats["total"] == 0
        queue.close()

    def test_stats_with_tasks(self):
        queue, mock_app = _create_celery_queue()

        mock_async_result = MagicMock()
        mock_async_result.id = "celery-1"
        queue._celery_task.apply_async = MagicMock(return_value=mock_async_result)

        queue.submit(agent_name="default", message="t1")
        queue.submit(agent_name="default", message="t2")

        mock_result = MagicMock()
        mock_result.state = "PENDING"
        mock_app.AsyncResult.return_value = mock_result

        stats = queue.stats()
        assert stats["total"] == 2
        assert stats["pending"] == 2
        queue.close()


class TestCeleryClear:
    def test_clear_completed(self):
        queue, mock_app = _create_celery_queue()

        mock_async_result = MagicMock()
        mock_async_result.id = "celery-1"
        queue._celery_task.apply_async = MagicMock(return_value=mock_async_result)

        queue.submit(agent_name="default", message="t1")

        mock_result = MagicMock()
        mock_result.state = "SUCCESS"
        mock_app.AsyncResult.return_value = mock_result

        removed = queue.clear()
        assert removed == 1
        assert len(queue._tasks) == 0
        queue.close()

    def test_clear_no_completed(self):
        queue, mock_app = _create_celery_queue()

        mock_async_result = MagicMock()
        mock_async_result.id = "celery-1"
        queue._celery_task.apply_async = MagicMock(return_value=mock_async_result)

        queue.submit(agent_name="default", message="t1")

        mock_result = MagicMock()
        mock_result.state = "PENDING"
        mock_app.AsyncResult.return_value = mock_result

        removed = queue.clear()
        assert removed == 0
        queue.close()


# ---------------------------------------------------------------------------
# Handler registration tests
# ---------------------------------------------------------------------------


class TestCeleryHandler:
    def test_register_handler(self):
        queue, _ = _create_celery_queue()

        def handler(task):
            return {"result": "ok"}

        queue.register_handler(handler)
        assert queue._handler is handler
        queue.close()

    def test_worker_start_without_handler_raises(self):
        queue, _ = _create_celery_queue()

        with pytest.raises(RuntimeError, match="handler"):
            queue.worker_start()
        queue.close()


# ---------------------------------------------------------------------------
# Health check tests
# ---------------------------------------------------------------------------


class TestCeleryHealthCheck:
    def test_health_check_success(self):
        queue, mock_app = _create_celery_queue()

        mock_conn = MagicMock()
        mock_conn.ensure_connection = MagicMock(return_value=None)
        mock_app.connection_for_write.return_value = mock_conn

        assert queue.health_check() is True
        mock_conn.close.assert_called_once()
        queue.close()

    def test_health_check_failure(self):
        queue, mock_app = _create_celery_queue()

        mock_app.connection_for_write.side_effect = Exception("conn failed")

        assert queue.health_check() is False
        queue.close()


# ---------------------------------------------------------------------------
# Queue registry tests
# ---------------------------------------------------------------------------


class TestQueueRegistryCelery:
    def test_celery_registered_when_available(self):
        """When celery is installed, 'celery' should be in queue_registry."""
        from agentbase.core.queue import queue_registry

        # celery might not be installed in test env — check both paths
        try:
            import celery  # noqa: F401
            assert queue_registry.has("celery")
        except ImportError:
            # celery not installed — should not be registered
            assert not queue_registry.has("celery")
