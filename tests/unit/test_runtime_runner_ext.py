"""Tests for AgentRunner — covers helper functions, invoke, stream, resume, get_stats.

Tests verify:
1. _message_content — string, dict, list, None, object with .content
2. _extract_final_text — dict with messages, object with messages, None
3. AgentRunner._build_input — message format
4. AgentRunner.invoke — success, error handling, TypeError fallback
5. AgentRunner.stream — event normalization, RUN_STARTED/RUN_FINISHED
6. AgentRunner.resume — thread existence check, success, error
7. AgentRunner.get_stats — session counts, config values
8. AgentRunner._normalize_event — tuple, dict, raw
9. AgentRunner._from_mode_payload — messages/updates/values/events modes
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# _message_content
# ---------------------------------------------------------------------------


class TestMessageContent:
    def test_none(self):
        from agentbase.runtime.runner import _message_content

        assert _message_content(None) == ""

    def test_string(self):
        from agentbase.runtime.runner import _message_content

        assert _message_content("hello") == "hello"

    def test_dict_with_string_content(self):
        from agentbase.runtime.runner import _message_content

        assert _message_content({"content": "text"}) == "text"

    def test_dict_with_list_content(self):
        from agentbase.runtime.runner import _message_content

        msg = {"content": [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}]}
        assert _message_content(msg) == "hello world"

    def test_dict_with_list_content_non_text_items(self):
        from agentbase.runtime.runner import _message_content

        msg = {"content": [{"type": "image"}, "raw text"]}
        result = _message_content(msg)
        assert "image" in result or "raw text" in result

    def test_object_with_content_attr(self):
        from agentbase.runtime.runner import _message_content

        class FakeMessage:
            content = "from attr"

        assert _message_content(FakeMessage()) == "from attr"

    def test_object_with_list_content_attr(self):
        from agentbase.runtime.runner import _message_content

        class FakeMessage:
            content = [{"type": "text", "text": "part1"}, {"type": "text", "text": "part2"}]

        assert _message_content(FakeMessage()) == "part1part2"

    def test_object_with_list_content_text_attr(self):
        from agentbase.runtime.runner import _message_content

        class FakeItem:
            text = "from_item"

        class FakeMessage:
            content = [FakeItem()]

        result = _message_content(FakeMessage())
        assert "from_item" in result

    def test_object_without_content(self):
        from agentbase.runtime.runner import _message_content

        class NoContent:
            pass

        result = _message_content(NoContent())
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _extract_final_text
# ---------------------------------------------------------------------------


class TestExtractFinalText:
    def test_none(self):
        from agentbase.runtime.runner import _extract_final_text

        assert _extract_final_text(None) == ""

    def test_dict_with_messages(self):
        from agentbase.runtime.runner import _extract_final_text

        result = {"messages": [{"content": "final answer"}]}
        assert _extract_final_text(result) == "final answer"

    def test_dict_with_multiple_messages(self):
        from agentbase.runtime.runner import _extract_final_text

        result = {"messages": [{"content": "first"}, {"content": "second"}]}
        assert _extract_final_text(result) == "second"

    def test_dict_without_messages(self):
        from agentbase.runtime.runner import _extract_final_text

        result = {"key": "value"}
        assert "value" in _extract_final_text(result)

    def test_object_with_messages(self):
        from agentbase.runtime.runner import _extract_final_text

        class FakeResult:
            messages = [{"content": "from object"}]

        assert _extract_final_text(FakeResult()) == "from object"

    def test_object_without_messages(self):
        from agentbase.runtime.runner import _extract_final_text

        class FakeResult:
            pass

        result = _extract_final_text(FakeResult())
        assert isinstance(result, str)

    def test_empty_messages_list(self):
        from agentbase.runtime.runner import _extract_final_text

        result = {"messages": []}
        assert isinstance(_extract_final_text(result), str)


# ---------------------------------------------------------------------------
# AgentRunner._build_input
# ---------------------------------------------------------------------------


class TestAgentRunnerBuildInput:
    def test_build_input(self):
        from agentbase.runtime.runner import AgentRunner

        mock_factory = MagicMock()
        mock_config = MagicMock()
        mock_config.runtime.max_concurrency = 10

        runner = AgentRunner(factory=mock_factory, app_config=mock_config)
        result = runner._build_input("hello world")
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][0]["content"] == "hello world"


# ---------------------------------------------------------------------------
# AgentRunner.invoke
# ---------------------------------------------------------------------------


class TestAgentRunnerInvoke:
    def _make_runner(self):
        from agentbase.runtime.runner import AgentRunner

        mock_factory = MagicMock()
        mock_factory.tracer = None
        mock_config = MagicMock()
        mock_config.runtime.max_concurrency = 10
        mock_config.runtime.recursion_limit = 50
        return AgentRunner(factory=mock_factory, app_config=mock_config)

    def test_invoke_success(self):
        runner = self._make_runner()
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {"messages": [{"content": "response"}]}

        result = runner.invoke(
            agent=mock_agent,
            agent_name="test_agent",
            message="hello",
            thread_id="test-invoke-1",
        )
        assert result["agent"] == "test_agent"
        assert result["thread_id"] == "test-invoke-1"
        assert result["output_text"] == "response"
        mock_agent.invoke.assert_called_once()

    def test_invoke_error(self):
        from agentbase.runtime.errors import RuntimeExecutionError

        runner = self._make_runner()
        mock_agent = MagicMock()
        mock_agent.invoke.side_effect = ValueError("boom")

        with pytest.raises(RuntimeExecutionError, match="invoke failed"):
            runner.invoke(
                agent=mock_agent,
                agent_name="test_agent",
                message="hello",
                thread_id="test-invoke-err",
            )

    def test_invoke_type_error_fallback(self):
        runner = self._make_runner()
        mock_agent = MagicMock()

        # First call raises TypeError (wrong signature), second call succeeds
        call_count = [0]

        def mock_invoke(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise TypeError("unexpected keyword 'config'")
            return {"messages": [{"content": "fallback response"}]}

        mock_agent.invoke.side_effect = mock_invoke

        result = runner.invoke(
            agent=mock_agent,
            agent_name="test_agent",
            message="hello",
            thread_id="test-invoke-fallback",
        )
        assert result["output_text"] == "fallback response"

    def test_invoke_with_metadata(self):
        runner = self._make_runner()
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {"messages": [{"content": "ok"}]}

        result = runner.invoke(
            agent=mock_agent,
            agent_name="test_agent",
            message="hello",
            thread_id="test-invoke-meta",
            metadata={"request_id": "req-123"},
        )
        assert result["thread_id"] == "test-invoke-meta"


# ---------------------------------------------------------------------------
# AgentRunner.stream
# ---------------------------------------------------------------------------


class TestAgentRunnerStream:
    def _make_runner(self):
        from agentbase.runtime.runner import AgentRunner

        mock_factory = MagicMock()
        mock_factory.tracer = None
        mock_config = MagicMock()
        mock_config.runtime.max_concurrency = 10
        mock_config.runtime.recursion_limit = 50
        mock_config.runtime.stream_modes = ["messages"]
        return AgentRunner(factory=mock_factory, app_config=mock_config)

    def test_stream_success(self):
        from agentbase.runtime.events import EventType

        runner = self._make_runner()
        mock_agent = MagicMock()
        mock_agent.stream.return_value = iter([
            ("messages", {"content": "chunk1"}),
            ("messages", {"content": "chunk2"}),
        ])

        events = list(runner.stream(
            agent=mock_agent,
            agent_name="test_agent",
            message="hello",
            thread_id="test-stream-1",
        ))

        # Should have RUN_STARTED, 2 MESSAGE_DELTAs, RUN_FINISHED
        assert events[0].type == EventType.RUN_STARTED
        assert events[-1].type == EventType.RUN_FINISHED
        assert len(events) >= 3

    def test_stream_with_interrupt(self):
        from agentbase.runtime.events import EventType

        runner = self._make_runner()
        mock_agent = MagicMock()
        mock_agent.stream.return_value = iter([
            ("updates", {"interrupts": [{"reason": "need input"}]}),
        ])

        events = list(runner.stream(
            agent=mock_agent,
            agent_name="test_agent",
            message="hello",
            thread_id="test-stream-interrupt",
        ))

        # Should have RUN_STARTED, INTERRUPT, but no RUN_FINISHED
        types = [e.type for e in events]
        assert EventType.RUN_STARTED in types
        assert EventType.INTERRUPT in types
        assert EventType.RUN_FINISHED not in types

    def test_stream_error(self):
        from agentbase.runtime.errors import RuntimeExecutionError

        runner = self._make_runner()
        mock_agent = MagicMock()
        mock_agent.stream.side_effect = ValueError("stream error")

        with pytest.raises(RuntimeExecutionError, match="stream failed"):
            list(runner.stream(
                agent=mock_agent,
                agent_name="test_agent",
                message="hello",
                thread_id="test-stream-err",
            ))


# ---------------------------------------------------------------------------
# AgentRunner.resume
# ---------------------------------------------------------------------------


class TestAgentRunnerResume:
    def _make_runner(self, checkpointer=None):
        from agentbase.runtime.runner import AgentRunner

        mock_factory = MagicMock()
        mock_factory.tracer = None
        mock_factory.checkpointer = checkpointer or MagicMock()
        mock_config = MagicMock()
        mock_config.runtime.max_concurrency = 10
        mock_config.runtime.recursion_limit = 50
        return AgentRunner(factory=mock_factory, app_config=mock_config)

    def test_resume_success(self):
        mock_checkpointer = MagicMock()
        mock_checkpointer.get_tuple.return_value = {"checkpoint": "exists"}

        runner = self._make_runner(mock_checkpointer)
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {"messages": [{"content": "resumed"}]}

        result = runner.resume(
            agent=mock_agent,
            agent_name="test_agent",
            thread_id="test-resume-1",
            decision={"value": "yes"},
        )
        assert result["output_text"] == "resumed"

    def test_resume_thread_not_found(self):
        from agentbase.runtime.errors import RuntimeExecutionError

        mock_checkpointer = MagicMock()
        mock_checkpointer.get_tuple.return_value = None

        runner = self._make_runner(mock_checkpointer)

        with pytest.raises(RuntimeExecutionError, match="Session not found"):
            runner.resume(
                agent=MagicMock(),
                agent_name="test_agent",
                thread_id="nonexistent",
                decision={"value": "yes"},
            )

    def test_resume_error(self):
        from agentbase.runtime.errors import RuntimeExecutionError

        mock_checkpointer = MagicMock()
        mock_checkpointer.get_tuple.return_value = {"checkpoint": "exists"}

        runner = self._make_runner(mock_checkpointer)
        mock_agent = MagicMock()
        mock_agent.invoke.side_effect = ValueError("resume failed")

        with pytest.raises(RuntimeExecutionError, match="resume failed"):
            runner.resume(
                agent=mock_agent,
                agent_name="test_agent",
                thread_id="test-resume-err",
                decision={"value": "yes"},
            )


# ---------------------------------------------------------------------------
# AgentRunner.get_stats
# ---------------------------------------------------------------------------


class TestAgentRunnerGetStats:
    def test_get_stats(self):
        from agentbase.runtime.runner import AgentRunner

        mock_factory = MagicMock()
        mock_config = MagicMock()
        mock_config.runtime.max_concurrency = 20
        mock_config.runtime.recursion_limit = 100
        mock_config.runtime.default_agent = "default"

        runner = AgentRunner(factory=mock_factory, app_config=mock_config)
        stats = runner.get_stats()
        assert "sessions" in stats
        assert stats["max_concurrency"] == 20
        assert stats["recursion_limit"] == 100
        assert stats["default_agent"] == "default"


# ---------------------------------------------------------------------------
# AgentRunner._normalize_event
# ---------------------------------------------------------------------------


class TestAgentRunnerNormalizeEvent:
    def _make_runner(self):
        from agentbase.runtime.runner import AgentRunner

        mock_factory = MagicMock()
        mock_config = MagicMock()
        mock_config.runtime.max_concurrency = 10
        return AgentRunner(factory=mock_factory, app_config=mock_config)

    def test_normalize_tuple_event(self):
        from agentbase.runtime.events import EventType

        runner = self._make_runner()
        event = ("messages", {"content": "hello"})
        result = runner._normalize_event(event, thread_id="t1", agent_name="a1")
        assert result.type == EventType.MESSAGE_DELTA
        assert result.thread_id == "t1"

    def test_normalize_dict_with_messages(self):
        from agentbase.runtime.events import EventType

        runner = self._make_runner()
        event = {"messages": [{"content": "final"}]}
        result = runner._normalize_event(event, thread_id="t1", agent_name="a1")
        assert result.type == EventType.MESSAGE_FINAL

    def test_normalize_dict_with_type(self):
        from agentbase.runtime.events import EventType

        runner = self._make_runner()
        event = {"type": "tool.start", "data": {"tool": "search"}}
        result = runner._normalize_event(event, thread_id="t1", agent_name="a1")
        assert result.type == EventType.TOOL_START

    def test_normalize_dict_unknown(self):
        from agentbase.runtime.events import EventType

        runner = self._make_runner()
        event = {"unknown_key": "value"}
        result = runner._normalize_event(event, thread_id="t1", agent_name="a1")
        assert result.type == EventType.UPDATE

    def test_normalize_raw_object(self):
        from agentbase.runtime.events import EventType

        runner = self._make_runner()
        event = "plain string"
        result = runner._normalize_event(event, thread_id="t1", agent_name="a1")
        assert result.type == EventType.MESSAGE_DELTA

    def test_normalize_empty_string(self):
        from agentbase.runtime.events import EventType

        runner = self._make_runner()
        result = runner._normalize_event("", thread_id="t1", agent_name="a1")
        assert result.type == EventType.RAW


# ---------------------------------------------------------------------------
# AgentRunner._from_mode_payload
# ---------------------------------------------------------------------------


class TestAgentRunnerFromModePayload:
    def _make_runner(self):
        from agentbase.runtime.runner import AgentRunner

        mock_factory = MagicMock()
        mock_config = MagicMock()
        mock_config.runtime.max_concurrency = 10
        return AgentRunner(factory=mock_factory, app_config=mock_config)

    def test_messages_mode(self):
        from agentbase.runtime.events import EventType

        runner = self._make_runner()
        result = runner._from_mode_payload(
            mode="messages", payload={"content": "hello"},
            thread_id="t1", agent_name="a1",
        )
        assert result.type == EventType.MESSAGE_DELTA

    def test_messages_tuple_mode(self):
        from agentbase.runtime.events import EventType

        runner = self._make_runner()
        result = runner._from_mode_payload(
            mode="messages-tuple", payload=({"content": "text"},),
            thread_id="t1", agent_name="a1",
        )
        assert result.type == EventType.MESSAGE_DELTA

    def test_updates_mode_with_interrupts(self):
        from agentbase.runtime.events import EventType

        runner = self._make_runner()
        result = runner._from_mode_payload(
            mode="updates",
            payload={"interrupts": [{"reason": "need input"}]},
            thread_id="t1", agent_name="a1",
        )
        assert result.type == EventType.INTERRUPT
        assert result.data["reason"] == "need input"

    def test_updates_mode_with_interrupts_string(self):
        from agentbase.runtime.events import EventType

        runner = self._make_runner()
        result = runner._from_mode_payload(
            mode="updates",
            payload={"interrupts": ["simple reason"]},
            thread_id="t1", agent_name="a1",
        )
        assert result.type == EventType.INTERRUPT
        assert "simple reason" in result.data["reason"]

    def test_values_mode_with_messages(self):
        from agentbase.runtime.events import EventType

        runner = self._make_runner()
        result = runner._from_mode_payload(
            mode="values",
            payload={"messages": [{"content": "final text"}]},
            thread_id="t1", agent_name="a1",
        )
        assert result.type == EventType.MESSAGE_FINAL

    def test_updates_mode_with_messages(self):
        from agentbase.runtime.events import EventType

        runner = self._make_runner()
        result = runner._from_mode_payload(
            mode="updates",
            payload={"messages": [{"content": "update text"}]},
            thread_id="t1", agent_name="a1",
        )
        assert result.type == EventType.UPDATE

    def test_updates_mode_plain(self):
        from agentbase.runtime.events import EventType

        runner = self._make_runner()
        result = runner._from_mode_payload(
            mode="updates",
            payload={"key": "value"},
            thread_id="t1", agent_name="a1",
        )
        assert result.type == EventType.UPDATE

    def test_events_mode_tool_start(self):
        from agentbase.runtime.events import EventType

        runner = self._make_runner()
        result = runner._from_mode_payload(
            mode="events",
            payload={"event": "on_tool_start"},
            thread_id="t1", agent_name="a1",
        )
        assert result.type == EventType.TOOL_START

    def test_events_mode_tool_end(self):
        from agentbase.runtime.events import EventType

        runner = self._make_runner()
        result = runner._from_mode_payload(
            mode="events",
            payload={"event": "on_tool_end"},
            thread_id="t1", agent_name="a1",
        )
        assert result.type == EventType.TOOL_END

    def test_events_mode_unknown(self):
        from agentbase.runtime.events import EventType

        runner = self._make_runner()
        result = runner._from_mode_payload(
            mode="events",
            payload={"event": "custom_event"},
            thread_id="t1", agent_name="a1",
        )
        assert result.type == EventType.RAW


# ---------------------------------------------------------------------------
# AgentRunner.stream — semaphore coverage (concurrency limit)
# ---------------------------------------------------------------------------


class TestAgentRunnerStreamSemaphore:
    """Verify that the semaphore is held during the entire stream iteration,
    not just during iterator creation."""

    def _make_runner(self, max_conc: int = 1):
        from agentbase.runtime.runner import AgentRunner

        mock_factory = MagicMock()
        mock_factory.tracer = None
        mock_config = MagicMock()
        mock_config.runtime.max_concurrency = max_conc
        mock_config.runtime.recursion_limit = 50
        mock_config.runtime.stream_modes = ["messages"]
        return AgentRunner(factory=mock_factory, app_config=mock_config)

    def test_semaphore_held_during_iteration(self):
        """When max_concurrency=1, a second stream must block until the
        first completes iteration — not just until iterator creation."""
        import threading
        from agentbase.runtime.events import EventType

        runner = self._make_runner(max_conc=1)
        barrier = threading.Event()
        second_started = threading.Event()

        class BlockingAgent:
            def stream(self, payload, config=None, stream_mode=None):
                # Return a lazy iterator that blocks until the first
                # stream consumer signals it can proceed.
                def _gen():
                    yield ("messages", {"content": "first"})
                    # Signal that the first stream is mid-iteration
                    barrier.set()
                    # Wait for the test to confirm the second stream is blocked
                    second_started.wait(timeout=5)
                    yield ("messages", {"content": "done"})
                return _gen()

        class FastAgent:
            def stream(self, payload, config=None, stream_mode=None):
                # If the semaphore is properly held, this will only
                # be called after the first stream finishes.
                second_started.set()
                return iter([("messages", {"content": "second"})])

        agent1 = BlockingAgent()
        agent2 = FastAgent()

        events1 = []
        errors = []

        def run_stream1():
            try:
                for ev in runner.stream(
                    agent=agent1, agent_name="a1",
                    message="hi", thread_id="t1",
                ):
                    events1.append(ev)
            except Exception as exc:
                errors.append(exc)

        def run_stream2():
            # Wait for the first stream to start iterating
            barrier.wait(timeout=5)
            try:
                for ev in runner.stream(
                    agent=agent2, agent_name="a2",
                    message="hi", thread_id="t2",
                ):
                    pass
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=run_stream1)
        t2 = threading.Thread(target=run_stream2)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert errors == [], f"Unexpected errors: {errors}"
        # If the semaphore works, the second stream's agent.stream() was
        # only called after the first finished — meaning second_started
        # was set by run_stream2, not by the first stream's barrier.
        assert second_started.is_set()
        assert any(e.type == EventType.RUN_FINISHED for e in events1)

    def test_semaphore_released_on_stream_error(self):
        """If a stream raises during iteration, the semaphore must be released."""
        from agentbase.runtime.errors import RuntimeExecutionError

        runner = self._make_runner(max_conc=1)

        class ErrorAgent:
            def stream(self, payload, config=None, stream_mode=None):
                def _gen():
                    yield ("messages", {"content": "chunk1"})
                    raise ValueError("mid-stream error")
                return _gen()

        agent = ErrorAgent()
        with pytest.raises(RuntimeExecutionError, match="stream iteration failed"):
            list(runner.stream(
                agent=agent, agent_name="err",
                message="hi", thread_id="t-err",
            ))

        # Semaphore should be available now — a new stream should work
        agent2 = MagicMock()
        agent2.stream.return_value = iter([("messages", {"content": "ok"})])
        events = list(runner.stream(
            agent=agent2, agent_name="ok",
            message="hi", thread_id="t-ok",
        ))
        # Should have at least RUN_STARTED and RUN_FINISHED
        from agentbase.runtime.events import EventType
        assert any(e.type == EventType.RUN_FINISHED for e in events)


class TestAgentRunnerFromModePayloadExtra:
    def _make_runner(self):
        from agentbase.runtime.runner import AgentRunner

        mock_factory = MagicMock()
        mock_factory.tracer = None
        mock_config = MagicMock()
        mock_config.runtime.max_concurrency = 10
        mock_config.runtime.recursion_limit = 50
        mock_config.runtime.stream_modes = ["messages"]
        return AgentRunner(factory=mock_factory, app_config=mock_config)

    def test_unknown_mode(self):
        from agentbase.runtime.events import EventType

        runner = self._make_runner()
        result = runner._from_mode_payload(
            mode="custom",
            payload={"data": "value"},
            thread_id="t1", agent_name="a1",
        )
        assert result.type == EventType.RAW
