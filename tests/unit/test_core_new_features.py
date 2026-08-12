"""Unit tests for core/mcp, core/queue, core/evaluation, core/tracer, core/workspace, extensions/middleware/summary, config schema."""
from __future__ import annotations

import json

import pytest

# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------

class TestMCPRegistry:
    def test_registry_has_memory(self):
        from agentbase.core.mcp import mcp_registry

        assert mcp_registry.has("memory")
        assert "memory" in mcp_registry.names()

    def test_create_memory_client(self):
        from agentbase.core.mcp import MemoryMCPClient, mcp_registry

        client = mcp_registry.create("memory", name="test_server")
        assert isinstance(client, MemoryMCPClient)
        assert client.name == "test_server"

    def test_create_unknown_raises(self):
        from agentbase.core.mcp import mcp_registry

        with pytest.raises(KeyError, match="Unknown MCP client"):
            mcp_registry.create("nonexistent")

    def test_register_custom(self):
        from agentbase.core.mcp import mcp_registry, register_mcp_client

        @register_mcp_client("custom_mcp", description="Test", override=True)
        class CustomClient:
            @property
            def name(self):
                return "custom"

            def connect(self): pass
            def disconnect(self): pass
            def list_tools(self): return []
            def call_tool(self, name, args=None): return None

        assert mcp_registry.has("custom_mcp")
        client = mcp_registry.create("custom_mcp")
        assert client.name == "custom"


class TestMemoryMCPClient:
    def test_register_and_call_tool(self):
        from agentbase.core.mcp import MemoryMCPClient

        client = MemoryMCPClient(name="test")
        client.register_tool("echo", lambda args: args.get("text", ""), description="Echo tool")
        client.connect()

        tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "echo"

        result = client.call_tool("echo", {"text": "hello"})
        assert result.content == "hello"
        assert not result.is_error

    def test_call_unknown_tool(self):
        from agentbase.core.mcp import MemoryMCPClient

        client = MemoryMCPClient(name="test")
        result = client.call_tool("nonexistent")
        assert result.is_error

    def test_tool_error_handling(self):
        from agentbase.core.mcp import MemoryMCPClient

        client = MemoryMCPClient(name="test")
        client.register_tool("boom", lambda args: (_ for _ in ()).throw(ValueError("crash")))
        result = client.call_tool("boom")
        assert result.is_error
        assert "crash" in result.content


class TestMCPManager:
    def test_multiple_servers(self):
        from agentbase.core.mcp import MCPManager, MemoryMCPClient

        mgr = MCPManager()
        s1 = MemoryMCPClient(name="server1")
        s1.register_tool("tool_a", lambda args: "a")
        s2 = MemoryMCPClient(name="server2")
        s2.register_tool("tool_b", lambda args: "b")

        mgr.add_server("s1", s1)
        mgr.add_server("s2", s2)
        mgr.connect_all()

        tools = mgr.list_all_tools()
        assert len(tools) == 2

        result = mgr.call_tool("tool_a", {})
        assert result.content == "a"

        result = mgr.call_tool("tool_b", {})
        assert result.content == "b"

    def test_tool_not_found(self):
        from agentbase.core.mcp import MCPManager

        mgr = MCPManager()
        result = mgr.call_tool("nonexistent")
        assert result.is_error

    def test_server_names(self):
        from agentbase.core.mcp import MCPManager, MemoryMCPClient

        mgr = MCPManager()
        mgr.add_server("alpha", MemoryMCPClient(name="alpha"))
        mgr.add_server("beta", MemoryMCPClient(name="beta"))
        assert "alpha" in mgr.server_names
        assert "beta" in mgr.server_names


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

class TestMemoryRequestQueue:
    def test_submit_and_get(self):
        from agentbase.core.queue import MemoryRequestQueue, TaskStatus

        q = MemoryRequestQueue()
        task = q.submit(agent_name="default", message="hello")
        assert task.status == TaskStatus.PENDING

        retrieved = q.get_task(task.id)
        assert retrieved is not None
        assert retrieved.message == "hello"

    def test_get_nonexistent(self):
        from agentbase.core.queue import MemoryRequestQueue

        q = MemoryRequestQueue()
        assert q.get_task("nonexistent") is None

    def test_list_filtered(self):
        from agentbase.core.queue import MemoryRequestQueue

        q = MemoryRequestQueue()
        q.submit(agent_name="a", message="1")
        q.submit(agent_name="b", message="2")
        q.submit(agent_name="a", message="3")

        all_a = q.list_tasks(agent_name="a")
        assert len(all_a) == 2

    def test_cancel(self):
        from agentbase.core.queue import MemoryRequestQueue, TaskStatus

        q = MemoryRequestQueue()
        task = q.submit(agent_name="default", message="test")
        assert q.cancel(task.id)
        assert task.status == TaskStatus.CANCELLED

    def test_cancel_completed_fails(self):
        from agentbase.core.queue import MemoryRequestQueue, TaskStatus

        q = MemoryRequestQueue()
        task = q.submit(agent_name="default", message="test")
        q.update_task(task.id, status=TaskStatus.COMPLETED)
        assert not q.cancel(task.id)

    def test_process_one(self):
        from agentbase.core.queue import MemoryRequestQueue, TaskStatus

        q = MemoryRequestQueue()
        q.submit(agent_name="default", message="hello")

        def handler(t):
            return {"output": f"processed: {t.message}"}

        result = q.process_one(handler)
        assert result is not None
        assert result.status == TaskStatus.COMPLETED
        assert result.result["output"] == "processed: hello"

    def test_process_one_none_when_empty(self):
        from agentbase.core.queue import MemoryRequestQueue

        q = MemoryRequestQueue()
        assert q.process_one(lambda t: {}) is None

    def test_process_all(self):
        from agentbase.core.queue import MemoryRequestQueue, TaskStatus

        q = MemoryRequestQueue()
        q.submit(agent_name="a", message="1")
        q.submit(agent_name="a", message="2")

        results = q.process_all(lambda t: {"output": t.message})
        assert len(results) == 2
        assert all(r.status == TaskStatus.COMPLETED for r in results)

    def test_process_error(self):
        from agentbase.core.queue import MemoryRequestQueue, TaskStatus

        q = MemoryRequestQueue()
        q.submit(agent_name="a", message="boom")

        def handler(t):
            raise ValueError("crash")

        result = q.process_one(handler)
        assert result.status == TaskStatus.FAILED
        assert "crash" in result.error

    def test_update_task(self):
        from agentbase.core.queue import MemoryRequestQueue, TaskStatus

        q = MemoryRequestQueue()
        task = q.submit(agent_name="a", message="test")
        updated = q.update_task(task.id, status=TaskStatus.RUNNING, started_at="2025-01-01")
        assert updated.status == TaskStatus.RUNNING
        assert updated.started_at == "2025-01-01"


class TestQueueRegistry:
    def test_has_memory(self):
        from agentbase.core.queue import queue_registry

        assert queue_registry.has("memory")

    def test_create_memory(self):
        from agentbase.core.queue import MemoryRequestQueue, queue_registry

        q = queue_registry.create("memory")
        assert isinstance(q, MemoryRequestQueue)

    def test_create_unknown(self):
        from agentbase.core.queue import queue_registry

        with pytest.raises(KeyError, match="Unknown queue"):
            queue_registry.create("nonexistent")


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

class TestEvaluationRunner:
    def test_evaluate_single_pass(self):
        from agentbase.core.evaluation import EvalCase, EvaluationRunner

        runner = EvaluationRunner()
        cases = [EvalCase(id="1", query="What is 2+2?", expected="4", expected_keywords=["4"])]
        report = runner.evaluate(cases, lambda q: "The answer is 4")

        assert report.total == 1
        assert report.passed_count == 1
        assert report.pass_rate == 1.0

    def test_evaluate_single_fail(self):
        from agentbase.core.evaluation import EvalCase, EvaluationRunner

        runner = EvaluationRunner()
        cases = [EvalCase(id="1", query="What is 2+2?", expected="4", expected_keywords=["4"])]
        report = runner.evaluate(cases, lambda q: "I don't know")

        assert report.failed_count == 1
        assert report.pass_rate == 0.0

    def test_evaluate_multiple(self):
        from agentbase.core.evaluation import EvalCase, EvaluationRunner

        runner = EvaluationRunner()
        cases = [
            EvalCase(id="1", query="capital of France?", expected="Paris", expected_keywords=["Paris"]),
            EvalCase(id="2", query="capital of Japan?", expected="Tokyo", expected_keywords=["Tokyo"]),
            EvalCase(id="3", query="capital of Brazil?", expected="Brasilia", expected_keywords=["Brasilia"]),
        ]
        def agent_fn(q):
            if "France" in q:
                return "Paris"
            elif "Japan" in q:
                return "Tokyo"
            else:
                return "unknown"
        report = runner.evaluate(cases, agent_fn)

        assert report.total == 3
        assert report.passed_count == 2
        assert report.failed_count == 1

    def test_evaluate_handles_exception(self):
        from agentbase.core.evaluation import EvalCase, EvaluationRunner

        runner = EvaluationRunner()
        cases = [EvalCase(id="1", query="test")]
        report = runner.evaluate(cases, lambda q: (_ for _ in ()).throw(RuntimeError("boom")))

        assert report.total == 1
        assert report.failed_count == 1
        assert "boom" in report.results[0].error

    def test_metrics_in_result(self):
        from agentbase.core.evaluation import EvalCase, EvaluationRunner

        runner = EvaluationRunner()
        cases = [EvalCase(id="1", query="test", expected_keywords=["hello"])]
        report = runner.evaluate(cases, lambda q: "hello world")

        result = report.results[0]
        assert "keyword_match" in result.metrics
        assert "substring_match" in result.metrics

    def test_report_to_dict(self):
        from agentbase.core.evaluation import EvalCase, EvaluationRunner

        runner = EvaluationRunner()
        cases = [EvalCase(id="1", query="test", expected_keywords=["a"])]
        report = runner.evaluate(cases, lambda q: "a")
        d = report.to_dict()

        assert d["total"] == 1
        assert d["passed"] == 1
        assert "results" in d

    def test_evaluate_from_file(self, tmp_path):
        from agentbase.core.evaluation import EvaluationRunner

        cases_data = [
            {"id": "1", "query": "What is Python?", "expected_keywords": ["programming", "language"]},
            {"id": "2", "query": "What is Rust?", "expected_keywords": ["programming", "language"]},
        ]
        fpath = tmp_path / "cases.json"
        fpath.write_text(json.dumps(cases_data), encoding="utf-8")

        runner = EvaluationRunner()
        report = runner.evaluate_from_file(str(fpath), lambda q: "Python is a programming language")
        assert report.total == 2

    def test_add_custom_metric(self):
        from agentbase.core.evaluation import EvalCase, EvaluationRunner

        class LengthMetric:
            name = "length_check"

            def compute(self, case, actual):
                return 1.0 if len(actual) > 10 else 0.0

        runner = EvaluationRunner()
        runner.add_metric(LengthMetric())
        cases = [EvalCase(id="1", query="test")]
        report = runner.evaluate(cases, lambda q: "short answer that is long enough")
        assert "length_check" in report.results[0].metrics


# ---------------------------------------------------------------------------
# Tracer
# ---------------------------------------------------------------------------

class TestNullTracer:
    def test_start_trace_returns_id(self):
        from agentbase.core.tracer import NullTracer

        t = NullTracer()
        trace_id = t.start_trace("test")
        assert trace_id  # non-empty string

    def test_start_span(self):
        from agentbase.core.tracer import NullTracer

        t = NullTracer()
        span = t.start_span("operation")
        assert span.name == "operation"

    def test_finish_span_noop(self):
        from agentbase.core.tracer import NullTracer

        t = NullTracer()
        span = t.start_span("op")
        t.finish_span(span)  # should not raise

    def test_get_trace_empty(self):
        from agentbase.core.tracer import NullTracer

        t = NullTracer()
        assert t.get_trace("nonexistent") == []


class TestInMemoryTracer:
    def test_start_trace_and_span(self):
        from agentbase.core.tracer import InMemoryTracer

        t = InMemoryTracer()
        trace_id = t.start_trace("agent_run", agent="default")
        span = t.start_span("model_call", trace_id=trace_id)
        t.finish_span(span)

        spans = t.get_trace(trace_id)
        assert len(spans) >= 1

    def test_span_attributes(self):
        from agentbase.core.tracer import InMemoryTracer

        t = InMemoryTracer()
        span = t.start_span("op", model="gpt-4", temperature=0.0)
        assert span.attributes["model"] == "gpt-4"
        assert span.attributes["temperature"] == 0.0

    def test_span_events(self):
        from agentbase.core.tracer import InMemoryTracer

        t = InMemoryTracer()
        span = t.start_span("op")
        span.add_event("tool_called", tool_name="echo")
        assert len(span.events) == 1
        assert span.events[0]["name"] == "tool_called"

    def test_span_error(self):
        from agentbase.core.tracer import InMemoryTracer

        t = InMemoryTracer()
        span = t.start_span("op")
        t.finish_span(span, status="error", error="timeout")
        assert span.status == "error"
        assert span.error == "timeout"

    def test_all_traces(self):
        from agentbase.core.tracer import InMemoryTracer

        t = InMemoryTracer()
        t.start_trace("trace1")
        t.start_trace("trace2")
        assert len(t.all_traces()) >= 2


class TestTracerRegistry:
    def test_has_null_and_memory(self):
        from agentbase.core.tracer import tracer_registry

        assert tracer_registry.has("null")
        assert tracer_registry.has("memory")

    def test_create_null(self):
        from agentbase.core.tracer import NullTracer, tracer_registry

        t = tracer_registry.create("null")
        assert isinstance(t, NullTracer)

    def test_create_unknown(self):
        from agentbase.core.tracer import tracer_registry

        with pytest.raises(KeyError, match="Unknown tracer"):
            tracer_registry.create("nonexistent")


class TestTraceContext:
    def test_context_manager_success(self):
        from agentbase.core.tracer import InMemoryTracer, trace

        t = InMemoryTracer()
        with trace(t, "operation", agent="default") as span:
            span.add_event("step1")

        assert span.status == "ok"

    def test_context_manager_error(self):
        from agentbase.core.tracer import InMemoryTracer, trace

        t = InMemoryTracer()
        with pytest.raises(ValueError):
            with trace(t, "operation") as span:
                raise ValueError("boom")

        assert span.status == "error"
        assert "boom" in span.error


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------

class TestWorkspaceManager:
    def test_creates_dirs(self, tmp_path):
        from agentbase.core.workspace import WorkspaceManager

        ws = WorkspaceManager(tmp_path / "ws")
        assert ws.workspace_dir.exists()
        assert ws.uploads_dir.exists()
        assert ws.outputs_dir.exists()

    def test_write_and_read(self, tmp_path):
        from agentbase.core.workspace import WorkspaceManager

        ws = WorkspaceManager(tmp_path / "ws")
        ws.write("workspace", "test.txt", "hello content")
        assert ws.read("workspace", "test.txt") == "hello content"

    def test_write_to_uploads(self, tmp_path):
        from agentbase.core.workspace import WorkspaceManager

        ws = WorkspaceManager(tmp_path / "ws")
        ws.write("uploads", "user_file.txt", "uploaded content")
        assert ws.read("uploads", "user_file.txt") == "uploaded content"

    def test_write_to_outputs(self, tmp_path):
        from agentbase.core.workspace import WorkspaceManager

        ws = WorkspaceManager(tmp_path / "ws")
        ws.write("outputs", "result.json", '{"answer": 42}')
        assert "42" in ws.read("outputs", "result.json")

    def test_write_bytes(self, tmp_path):
        from agentbase.core.workspace import WorkspaceManager

        ws = WorkspaceManager(tmp_path / "ws")
        ws.write("outputs", "data.bin", b"\x00\x01\x02")
        assert ws.read_bytes("outputs", "data.bin") == b"\x00\x01\x02"

    def test_path_traversal_blocked(self, tmp_path):
        from agentbase.core.workspace import WorkspaceManager

        ws = WorkspaceManager(tmp_path / "ws")
        with pytest.raises(ValueError, match="escapes"):
            ws.resolve("workspace", "../../../etc/passwd")

    def test_list_files(self, tmp_path):
        from agentbase.core.workspace import WorkspaceManager

        ws = WorkspaceManager(tmp_path / "ws")
        ws.write("workspace", "a.txt", "a")
        ws.write("workspace", "b.txt", "b")
        ws.write("uploads", "c.txt", "c")

        ws_files = ws.list_files("workspace")
        assert len(ws_files) == 2

        ul_files = ws.list_files("uploads")
        assert len(ul_files) == 1

    def test_delete(self, tmp_path):
        from agentbase.core.workspace import WorkspaceManager

        ws = WorkspaceManager(tmp_path / "ws")
        ws.write("workspace", "temp.txt", "temp")
        assert ws.delete("workspace", "temp.txt")
        assert not ws.exists("workspace", "temp.txt")

    def test_exists(self, tmp_path):
        from agentbase.core.workspace import WorkspaceManager

        ws = WorkspaceManager(tmp_path / "ws")
        ws.write("outputs", "out.txt", "out")
        assert ws.exists("outputs", "out.txt")
        assert not ws.exists("outputs", "nonexistent.txt")

    def test_clear(self, tmp_path):
        from agentbase.core.workspace import WorkspaceManager

        ws = WorkspaceManager(tmp_path / "ws")
        ws.write("uploads", "a.txt", "a")
        ws.write("outputs", "b.txt", "b")
        ws.clear("uploads")
        assert len(ws.list_files("uploads")) == 0
        assert len(ws.list_files("outputs")) == 1

    def test_clear_all(self, tmp_path):
        from agentbase.core.workspace import WorkspaceManager

        ws = WorkspaceManager(tmp_path / "ws")
        ws.write("workspace", "a.txt", "a")
        ws.write("uploads", "b.txt", "b")
        ws.write("outputs", "c.txt", "c")
        ws.clear()
        assert len(ws.list_files("workspace")) == 0
        assert len(ws.list_files("uploads")) == 0
        assert len(ws.list_files("outputs")) == 0

    def test_unknown_kind_raises(self, tmp_path):
        from agentbase.core.workspace import WorkspaceManager

        ws = WorkspaceManager(tmp_path / "ws")
        with pytest.raises(ValueError, match="Unknown file kind"):
            ws.resolve("nonexistent_kind", "test.txt")


# ---------------------------------------------------------------------------
# Summary middleware
# ---------------------------------------------------------------------------

class TestSummaryMiddleware:
    def test_build_returns_value(self, bootstrapped):
        from agentbase.config.schema import AgentConfig
        from agentbase.extensions.middleware.summary import build_summary
        agent_config = AgentConfig(name="test", metadata={"summary": {"threshold": 10}})
        result = build_summary(context={"agent_config": agent_config})
        assert result is not None

    def test_build_default_config(self, bootstrapped):
        from agentbase.extensions.middleware.summary import build_summary

        result = build_summary(context={})
        assert result is not None

    def test_estimate_tokens(self):
        from agentbase.extensions.middleware.summary import _estimate_tokens

        assert _estimate_tokens("hello world!") == 3  # 12 chars / 4

    def test_messages_to_text(self):
        from agentbase.extensions.middleware.summary import _messages_to_text

        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        text = _messages_to_text(messages)
        assert "[user]: hello" in text
        assert "[assistant]: hi there" in text

    def test_l1_summarize_with_model(self):
        from agentbase.extensions.middleware.summary import _l1_summarize

        def model_fn(prompt):
            return "This is a summary."

        result = _l1_summarize("long conversation text", model_fn, "Summarize: {history}")
        assert result == "This is a summary."

    def test_l1_summarize_fallback(self):
        from agentbase.extensions.middleware.summary import _l1_summarize

        result = _l1_summarize("[user]: hello\n[assistant]: hi\n[user]: bye", None, "Summarize: {history}")
        assert "hello" in result
        assert "bye" in result

    def test_l2_compact_truncates(self):
        from agentbase.extensions.middleware.summary import _l2_compact

        long_text = "x" * 10000
        result = _l2_compact(long_text, None, max_tokens=100)
        assert len(result) <= 100 * 4 + 50  # max_chars + truncation marker


# ---------------------------------------------------------------------------
# Config schema — new sections
# ---------------------------------------------------------------------------

class TestNewConfigSections:
    def test_app_config_has_mcp(self):
        from agentbase.config.schema import AppConfig

        config = AppConfig()
        assert config.mcp.provider == "none"
        assert config.mcp.servers == []

    def test_app_config_has_queue(self):
        from agentbase.config.schema import AppConfig

        config = AppConfig()
        assert config.queue.provider == "none"

    def test_app_config_has_tracer(self):
        from agentbase.config.schema import AppConfig

        config = AppConfig()
        assert config.tracer.provider == "null"

    def test_app_config_with_mcp_servers(self):
        from agentbase.config.schema import AppConfig

        config = AppConfig()
        config.mcp.provider = "memory"
        config.mcp.servers = [{"name": "test", "type": "memory"}]
        assert len(config.mcp.servers) == 1

    def test_agent_config_has_capabilities(self):
        from agentbase.config.schema import AgentConfig

        config = AgentConfig(name="test", capabilities=["file_upload", "files"])
        assert "file_upload" in config.capabilities
        assert "files" in config.capabilities

    def test_agent_config_capabilities_from_string(self):
        from agentbase.config.schema import AgentConfig

        config = AgentConfig(name="test", capabilities="file_upload")
        assert config.capabilities == ["file_upload"]

    def test_agent_config_capabilities_none(self):
        from agentbase.config.schema import AgentConfig

        config = AgentConfig(name="test")
        assert config.capabilities == []


class TestGetConfigurableItems:
    def test_returns_list(self):
        from agentbase.config.schema import AgentConfig

        config = AgentConfig(name="test")
        items = config.get_configurable_items()
        assert isinstance(items, list)
        assert len(items) > 0

    def test_has_expected_fields(self):
        from agentbase.config.schema import AgentConfig

        config = AgentConfig(name="test")
        items = config.get_configurable_items()
        names = [item["name"] for item in items]
        assert "name" in names
        assert "system_prompt" in names
        assert "tools" in names
        assert "capabilities" in names
        assert "metadata" in names

    def test_items_have_metadata(self):
        from agentbase.config.schema import AgentConfig

        config = AgentConfig(name="test")
        items = config.get_configurable_items()
        for item in items:
            assert "name" in item
            assert "type" in item
            assert "description" in item

    def test_default_values(self):
        from agentbase.config.schema import AgentConfig

        config = AgentConfig(name="test")
        items = config.get_configurable_items()
        tools_item = next(i for i in items if i["name"] == "tools")
        # Pydantic uses default_factory for list fields
        default = tools_item["default"]
        if default is not None and default != []:
            # PydanticUndefined or similar — just verify it's not a required field
            assert tools_item["type"] == "list[str]"

    def test_capabilities_in_items(self):
        from agentbase.config.schema import AgentConfig

        config = AgentConfig(name="test")
        items = config.get_configurable_items()
        cap_item = next(i for i in items if i["name"] == "capabilities")
        assert cap_item["type"] == "list[str]"
        assert "capability" in cap_item["description"].lower()


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

class TestMCPTools:
    def test_list_tools(self):
        from agentbase.core.mcp import MCPManager, MemoryMCPClient
        from agentbase.extensions.tools.mcp_ops import build_mcp_list_tools

        mgr = MCPManager()
        client = MemoryMCPClient(name="test_server")
        client.register_tool("echo", lambda args: args.get("text", ""), description="Echo tool")
        mgr.add_server("test", client)

        tool_fn = build_mcp_list_tools(context={"mcp_manager": mgr})
        result = tool_fn.invoke({})
        assert "echo" in result
        assert "test_server" in result

    def test_list_tools_empty(self):
        from agentbase.core.mcp import MCPManager
        from agentbase.extensions.tools.mcp_ops import build_mcp_list_tools

        mgr = MCPManager()
        tool_fn = build_mcp_list_tools(context={"mcp_manager": mgr})
        result = tool_fn.invoke({})
        assert "no mcp tools" in result.lower()

    def test_call_tool(self):
        from agentbase.core.mcp import MCPManager, MemoryMCPClient
        from agentbase.extensions.tools.mcp_ops import build_mcp_call_tool

        mgr = MCPManager()
        client = MemoryMCPClient(name="server")
        client.register_tool("greet", lambda args: f"Hello {args.get('name', 'world')}!")
        mgr.add_server("s1", client)

        tool_fn = build_mcp_call_tool(context={"mcp_manager": mgr})
        result = tool_fn.invoke({"tool_name": "greet", "arguments": '{"name": "Alice"}'})
        assert "Alice" in result

    def test_call_tool_not_found(self):
        from agentbase.core.mcp import MCPManager
        from agentbase.extensions.tools.mcp_ops import build_mcp_call_tool

        mgr = MCPManager()
        tool_fn = build_mcp_call_tool(context={"mcp_manager": mgr})
        result = tool_fn.invoke({"tool_name": "nonexistent", "arguments": ""})
        assert "error" in result.lower() or "not found" in result.lower()

    def test_call_tool_with_string_args(self):
        from agentbase.core.mcp import MCPManager, MemoryMCPClient
        from agentbase.extensions.tools.mcp_ops import build_mcp_call_tool

        mgr = MCPManager()
        client = MemoryMCPClient(name="server")
        client.register_tool("search", lambda args: f"Searching for: {args.get('input', '')}")
        mgr.add_server("s1", client)

        tool_fn = build_mcp_call_tool(context={"mcp_manager": mgr})
        result = tool_fn.invoke({"tool_name": "search", "arguments": "not json"})
        assert "not json" in result
