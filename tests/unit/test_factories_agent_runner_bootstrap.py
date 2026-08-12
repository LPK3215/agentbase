"""Unit tests for agent_factory, runner, and bootstrap."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentbase.config.schema import AgentConfig, AppConfig
from agentbase.runtime.errors import RuntimeExecutionError
from agentbase.runtime.events import EventType

# ---------------------------------------------------------------------------
# agent_factory
# ---------------------------------------------------------------------------

class TestAgentFactory:
    def test_build_creates_agent(self, tmp_path, bootstrapped, monkeypatch):
        """Test that AgentFactory.build produces a runnable agent."""
        from agentbase.factories.agent_factory import AgentFactory

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "skills").mkdir()

        app_config = AppConfig()
        app_config.runtime.workspace_dir = str(workspace)

        factory = AgentFactory(root_dir=tmp_path, app_config=app_config)
        agent_config = AgentConfig(
            name="default",
            description="Test",
            system_prompt="You are a test agent.",
            tools=["echo", "get_time"],
        )

        mock_agent = MagicMock()
        mock_model = MagicMock()
        with patch("deepagents.create_deep_agent", return_value=mock_agent):
            with patch("agentbase.factories.agent_factory.build_model", return_value=mock_model):
                agent = factory.build(agent_config)
                assert agent is mock_agent

    def test_factory_caches_managers(self, tmp_path):
        """Factory properties should cache their instances."""
        from agentbase.factories.agent_factory import AgentFactory

        app_config = AppConfig()
        factory = AgentFactory(root_dir=tmp_path, app_config=app_config)

        # First access creates
        mgr1 = factory.memory_manager
        mgr2 = factory.memory_manager
        assert mgr1 is mgr2

        kb1 = factory.knowledge_base
        kb2 = factory.knowledge_base
        assert kb1 is kb2

        sm1 = factory.skill_manager
        sm2 = factory.skill_manager
        assert sm1 is sm2

    def test_factory_skill_manager_uses_workspace_dir(self, tmp_path):
        from agentbase.factories.agent_factory import AgentFactory

        app_config = AppConfig()
        app_config.runtime.workspace_dir = "workspace"
        factory = AgentFactory(root_dir=tmp_path, app_config=app_config)

        mgr = factory.skill_manager
        assert mgr is not None

    def test_factory_search_provider_none_by_default(self, tmp_path):
        """Search provider should be None when web_search.provider is 'none'."""
        from agentbase.factories.agent_factory import AgentFactory

        app_config = AppConfig()
        factory = AgentFactory(root_dir=tmp_path, app_config=app_config)
        assert factory.search_provider is None

    def test_factory_knowledge_base_with_hash_embedding(self, tmp_path):
        """KB should use hash embedding when configured."""
        from agentbase.core.embeddings import HashEmbedding
        from agentbase.factories.agent_factory import AgentFactory

        app_config = AppConfig()
        app_config.embedding.provider = "hash"
        factory = AgentFactory(root_dir=tmp_path, app_config=app_config)
        kb = factory.knowledge_base
        assert kb.embedding_provider is not None
        assert isinstance(kb.embedding_provider, HashEmbedding)

    def test_build_permissions_normalizes_paths(self, tmp_path):
        """Permission paths should be normalized to start with /."""
        from agentbase.config.schema import PermissionRule
        from agentbase.factories.agent_factory import AgentFactory

        app_config = AppConfig()
        factory = AgentFactory(root_dir=tmp_path, app_config=app_config)

        rules = [
            PermissionRule(operations=["read"], paths=["workspace/**"], mode="allow"),
        ]
        result = factory._build_permissions(rules)
        assert len(result) >= 1
        # Check that paths were normalized
        item = result[0]
        if hasattr(item, "paths"):
            assert any(p.startswith("/") for p in item.paths)
        elif isinstance(item, dict):
            assert any(p.startswith("/") for p in item["paths"])

    def test_build_permissions_empty(self, tmp_path):
        from agentbase.factories.agent_factory import AgentFactory

        app_config = AppConfig()
        factory = AgentFactory(root_dir=tmp_path, app_config=app_config)
        result = factory._build_permissions([])
        assert result == []

    def test_resolve_paths(self, tmp_path):
        from agentbase.factories.agent_factory import AgentFactory

        app_config = AppConfig()
        factory = AgentFactory(root_dir=tmp_path, app_config=app_config)
        result = factory._resolve_paths(["relative/path", str(tmp_path / "abs")])
        assert len(result) == 2
        assert all(Path(p).is_absolute() for p in result)


# ---------------------------------------------------------------------------
# runner.py — _message_content
# ---------------------------------------------------------------------------

class TestMessageContent:
    def test_none(self):
        from agentbase.runtime.runner import _message_content
        assert _message_content(None) == ""

    def test_string(self):
        from agentbase.runtime.runner import _message_content
        assert _message_content("hello") == "hello"

    def test_dict_with_content_string(self):
        from agentbase.runtime.runner import _message_content
        assert _message_content({"content": "text"}) == "text"

    def test_dict_with_content_list(self):
        from agentbase.runtime.runner import _message_content
        msg = {"content": [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}]}
        assert _message_content(msg) == "hello world"

    def test_object_with_content(self):
        from agentbase.runtime.runner import _message_content

        class FakeMsg:
            content = "object content"
        assert _message_content(FakeMsg()) == "object content"

    def test_object_with_content_list(self):
        from agentbase.runtime.runner import _message_content

        class FakeText:
            def __init__(self, text):
                self.text = text

        class FakeMsg:
            content = [FakeText("a"), FakeText("b")]

        assert _message_content(FakeMsg()) == "ab"

    def test_object_no_content(self):
        from agentbase.runtime.runner import _message_content

        class NoContent:
            pass

        assert "NoContent" in _message_content(NoContent())


class TestExtractFinalText:
    def test_none(self):
        from agentbase.runtime.runner import _extract_final_text
        assert _extract_final_text(None) == ""

    def test_dict_with_messages(self):
        from agentbase.runtime.runner import _extract_final_text
        result = {"messages": [{"content": "final answer"}]}
        assert _extract_final_text(result) == "final answer"

    def test_dict_no_messages(self):
        from agentbase.runtime.runner import _extract_final_text
        assert _extract_final_text({"key": "value"}) == "{'key': 'value'}"

    def test_object_with_messages(self):
        from agentbase.runtime.runner import _extract_final_text

        class Result:
            messages = [{"content": "obj answer"}]

        assert _extract_final_text(Result()) == "obj answer"


# ---------------------------------------------------------------------------
# runner.py — _normalize_event
# ---------------------------------------------------------------------------

class TestNormalizeEvent:
    @pytest.fixture
    def runner(self, tmp_path):
        from agentbase.factories.agent_factory import AgentFactory
        from agentbase.runtime.runner import AgentRunner

        app_config = AppConfig()
        factory = AgentFactory(root_dir=tmp_path, app_config=app_config)
        return AgentRunner(factory=factory, app_config=app_config)

    def test_tuple_messages_mode(self, runner):
        """Messages mode tuple should produce MESSAGE_DELTA."""
        event = ("messages", {"content": "hello"})
        result = runner._normalize_event(event, thread_id="T1", agent_name="default")
        assert result.type == EventType.MESSAGE_DELTA
        assert "hello" in result.data.get("text", "")

    def test_tuple_updates_mode_with_messages(self, runner):
        event = ("updates", {"messages": [{"content": "updated"}]})
        result = runner._normalize_event(event, thread_id="T1", agent_name="default")
        assert result.type == EventType.UPDATE

    def test_tuple_values_mode_with_messages(self, runner):
        event = ("values", {"messages": [{"content": "final"}]})
        result = runner._normalize_event(event, thread_id="T1", agent_name="default")
        assert result.type == EventType.MESSAGE_FINAL

    def test_tuple_updates_interrupt(self, runner):
        event = ("updates", {"interrupts": [{"reason": "approval needed"}]})
        result = runner._normalize_event(event, thread_id="T1", agent_name="default")
        assert result.type == EventType.INTERRUPT
        assert "approval" in result.data.get("reason", "")

    def test_tuple_events_mode_tool_start(self, runner):
        event = ("events", {"event": "on_tool_start"})
        result = runner._normalize_event(event, thread_id="T1", agent_name="default")
        assert result.type == EventType.TOOL_START

    def test_tuple_events_mode_tool_end(self, runner):
        event = ("events", {"event": "on_tool_end"})
        result = runner._normalize_event(event, thread_id="T1", agent_name="default")
        assert result.type == EventType.TOOL_END

    def test_dict_with_messages(self, runner):
        event = {"messages": [{"content": "hello"}]}
        result = runner._normalize_event(event, thread_id="T1", agent_name="default")
        assert result.type == EventType.MESSAGE_FINAL

    def test_dict_with_type(self, runner):
        event = {"type": "run.started", "data": {"msg": "hi"}}
        result = runner._normalize_event(event, thread_id="T1", agent_name="default")
        assert result.type == EventType.RUN_STARTED

    def test_dict_unknown(self, runner):
        event = {"unknown_key": "value"}
        result = runner._normalize_event(event, thread_id="T1", agent_name="default")
        assert result.type == EventType.UPDATE

    def test_string_event(self, runner):
        result = runner._normalize_event("text chunk", thread_id="T1", agent_name="default")
        assert result.type in {EventType.MESSAGE_DELTA, EventType.RAW}

    def test_unknown_tuple_mode(self, runner):
        event = ("custom_mode", {"data": "value"})
        result = runner._normalize_event(event, thread_id="T1", agent_name="default")
        assert result.type == EventType.RAW


# ---------------------------------------------------------------------------
# runner.py — stream / invoke / resume
# ---------------------------------------------------------------------------

class TestRunnerStream:
    @pytest.fixture
    def runner(self, tmp_path):
        from agentbase.factories.agent_factory import AgentFactory
        from agentbase.runtime.runner import AgentRunner

        app_config = AppConfig()
        factory = AgentFactory(root_dir=tmp_path, app_config=app_config)
        return AgentRunner(factory=factory, app_config=app_config)

    def test_stream_produces_start_and_finish(self, runner):
        """Stream should yield RUN_STARTED and RUN_FINISHED events."""

        class FakeAgent:
            def stream(self, payload, config=None, stream_mode=None):
                yield {"messages": [{"content": "hello"}]}

        events = list(runner.stream(
            agent=FakeAgent(),
            agent_name="default",
            message="hi",
        ))
        types = [e.type for e in events]
        assert EventType.RUN_STARTED in types
        assert EventType.RUN_FINISHED in types

    def test_stream_collects_final_text(self, runner):
        class FakeAgent:
            def stream(self, payload, config=None, stream_mode=None):
                yield ("messages", {"content": "world"})

        events = list(runner.stream(
            agent=FakeAgent(),
            agent_name="default",
            message="hi",
        ))
        finish = [e for e in events if e.type == EventType.RUN_FINISHED]
        assert len(finish) == 1
        assert "world" in finish[0].data.get("output_text", "")

    def test_stream_handles_interrupt(self, runner):
        class FakeAgent:
            def stream(self, payload, config=None, stream_mode=None):
                yield ("updates", {"interrupts": [{"reason": "need approval"}]})

        events = list(runner.stream(
            agent=FakeAgent(),
            agent_name="default",
            message="do something",
        ))
        types = [e.type for e in events]
        assert EventType.INTERRUPT in types
        # Should NOT have RUN_FINISHED when interrupted
        assert EventType.RUN_FINISHED not in types

    def test_stream_handles_error(self, runner):
        class FakeAgent:
            def stream(self, payload, config=None, stream_mode=None):
                raise ValueError("boom")
                yield  # never reached

        with pytest.raises(RuntimeExecutionError):
            list(runner.stream(
                agent=FakeAgent(),
                agent_name="default",
                message="hi",
            ))

    def test_invoke_returns_dict(self, runner):
        class FakeAgent:
            def invoke(self, payload, config=None):
                return {"messages": [{"content": "answer"}]}

        result = runner.invoke(
            agent=FakeAgent(),
            agent_name="default",
            message="question",
        )
        assert result["agent"] == "default"
        assert result["output_text"] == "answer"
        assert "thread_id" in result

    def test_invoke_handles_error(self, runner):
        class FakeAgent:
            def invoke(self, payload, config=None):
                raise RuntimeError("crash")

        with pytest.raises(RuntimeExecutionError, match="invoke failed"):
            runner.invoke(
                agent=FakeAgent(),
                agent_name="default",
                message="hi",
            )

    def test_resume_nonexistent_thread_raises(self, runner):
        """Resume should raise AGENTBASE_RT_002 for nonexistent thread."""
        class FakeAgent:
            def invoke(self, payload, config=None):
                return {"messages": [{"content": "resumed"}]}

        # The checkpointer is memory-based and has no checkpoint for this thread
        with pytest.raises(RuntimeExecutionError, match="AGENTBASE_RT_002|Session not found"):
            runner.resume(
                agent=FakeAgent(),
                agent_name="default",
                thread_id="nonexistent-thread",
                decision="approve",
            )


# ---------------------------------------------------------------------------
# bootstrap.py
# ---------------------------------------------------------------------------

class TestBootstrap:
    def test_resolve_root_dir_with_path(self):
        from agentbase.bootstrap import resolve_root_dir
        result = resolve_root_dir("/tmp")
        assert result == Path("/tmp").resolve()

    def test_resolve_root_dir_none(self):
        from agentbase.bootstrap import resolve_root_dir
        result = resolve_root_dir(None)
        assert result == Path.cwd().resolve()

    def test_build_runtime(self, tmp_path, isolated_env, monkeypatch):
        """Test full build_runtime creates a RuntimeContext."""
        from agentbase.bootstrap import build_runtime

        configs = tmp_path / "configs"
        configs.mkdir()
        (configs / "default.yaml").write_text(
            "app:\n  name: test\n  env: test\n  log_level: INFO\n",
            encoding="utf-8",
        )
        (configs / "agents").mkdir()
        (configs / "agents" / "default.yaml").write_text(
            'name: default\nsystem_prompt: "Test"\ntools: [echo]\n',
            encoding="utf-8",
        )

        ctx = build_runtime(root_dir=tmp_path)
        assert ctx.app_config.app.name == "test"
        assert ctx.factory is not None
        assert ctx.runner is not None

    def test_runtime_context_list_agents(self, tmp_path, isolated_env):
        """Test that RuntimeContext.list_agents returns agent names."""
        from agentbase.bootstrap import build_runtime

        configs = tmp_path / "configs"
        configs.mkdir()
        (configs / "default.yaml").write_text(
            "app:\n  name: test\n",
            encoding="utf-8",
        )
        (configs / "agents").mkdir()
        (configs / "agents" / "alpha.yaml").write_text("name: alpha\n", encoding="utf-8")
        (configs / "agents" / "beta.yaml").write_text("name: beta\n", encoding="utf-8")

        ctx = build_runtime(root_dir=tmp_path)
        agents = ctx.list_agents()
        assert "alpha" in agents
        assert "beta" in agents

    def test_runtime_context_get_agent_config(self, tmp_path, isolated_env):
        from agentbase.bootstrap import build_runtime

        configs = tmp_path / "configs"
        configs.mkdir()
        (configs / "default.yaml").write_text("app:\n  name: test\n", encoding="utf-8")
        (configs / "agents").mkdir()
        (configs / "agents" / "default.yaml").write_text(
            'name: default\nsystem_prompt: "Hello"\n',
            encoding="utf-8",
        )

        ctx = build_runtime(root_dir=tmp_path)
        agent_config = ctx.get_agent_config("default")
        assert agent_config.name == "default"
        assert agent_config.system_prompt == "Hello"

    def test_runtime_context_get_agent_config_default(self, tmp_path, isolated_env):
        """get_agent_config with None should use default_agent."""
        from agentbase.bootstrap import build_runtime

        configs = tmp_path / "configs"
        configs.mkdir()
        (configs / "default.yaml").write_text(
            "app:\n  name: test\nruntime:\n  default_agent: default\n",
            encoding="utf-8",
        )
        (configs / "agents").mkdir()
        (configs / "agents" / "default.yaml").write_text(
            'name: default\n',
            encoding="utf-8",
        )

        ctx = build_runtime(root_dir=tmp_path)
        agent_config = ctx.get_agent_config(None)
        assert agent_config.name == "default"
