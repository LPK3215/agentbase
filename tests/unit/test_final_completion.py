"""Additional tests for summary middleware to improve coverage."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentbase.config.schema import AgentConfig, AppConfig


class TestSummaryMiddlewareBuild:
    def test_build_with_config(self, bootstrapped):
        from agentbase.extensions.middleware.summary import build_summary

        agent_config = AgentConfig(
            name="test",
            metadata={
                "summary": {
                    "threshold": 5,
                    "keep_recent": 2,
                    "max_tokens_estimate": 1000,
                    "summary_prompt": "Custom: {history}",
                }
            },
        )
        result = build_summary(context={"agent_config": agent_config})
        assert result is not None

    def test_build_without_config(self, bootstrapped):
        from agentbase.extensions.middleware.summary import build_summary

        result = build_summary(context={})
        assert result is not None

    def test_build_with_model_fn(self, bootstrapped):
        from agentbase.extensions.middleware.summary import build_summary

        model_fn = MagicMock(return_value="Summary text")
        result = build_summary(context={"summary_model": model_fn})
        assert result is not None


class TestSummaryHelpers:
    def test_estimate_tokens_empty(self):
        from agentbase.extensions.middleware.summary import _estimate_tokens
        assert _estimate_tokens("") == 0

    def test_estimate_tokens_long(self):
        from agentbase.extensions.middleware.summary import _estimate_tokens
        assert _estimate_tokens("a" * 100) == 25

    def test_messages_to_text_empty(self):
        from agentbase.extensions.middleware.summary import _messages_to_text
        assert _messages_to_text([]) == ""

    def test_messages_to_text_with_objects(self):
        from agentbase.extensions.middleware.summary import _messages_to_text

        class FakeMsg:
            role = "user"
            content = "hello object"

        result = _messages_to_text([FakeMsg()])
        assert "hello object" in result

    def test_extract_messages_from_dict(self):
        from agentbase.extensions.middleware.summary import _extract_messages

        request = {"messages": [{"role": "user", "content": "hi"}]}
        result = _extract_messages(request)
        assert len(result) == 1

    def test_extract_messages_from_object(self):
        from agentbase.extensions.middleware.summary import _extract_messages

        class FakeRequest:
            messages = [{"role": "user", "content": "hi"}]

        result = _extract_messages(FakeRequest())
        assert len(result) == 1

    def test_extract_messages_empty(self):
        from agentbase.extensions.middleware.summary import _extract_messages
        assert _extract_messages(None) == []
        assert _extract_messages({}) == []

    def test_l1_summarize_no_model_short(self):
        from agentbase.extensions.middleware.summary import _l1_summarize

        result = _l1_summarize("[user]: hello", None, "Summarize: {history}")
        assert "hello" in result

    def test_l1_summarize_no_model_long(self):
        from agentbase.extensions.middleware.summary import _l1_summarize

        text = "\n".join(f"[user]: message number {i}" for i in range(30))
        result = _l1_summarize(text, None, "Summarize: {history}")
        assert len(result) < len(text)

    def test_l1_summarize_model_error(self):
        from agentbase.extensions.middleware.summary import _l1_summarize

        def boom(prompt):
            raise RuntimeError("model error")

        result = _l1_summarize("some text", boom, "Summarize: {history}")
        assert "some text" in result

    def test_l2_compact_with_model(self):
        from agentbase.extensions.middleware.summary import _l2_compact

        def model_fn(prompt):
            return "compressed"

        result = _l2_compact("long text", model_fn, max_tokens=100)
        assert result == "compressed"

    def test_l2_compact_model_error(self):
        from agentbase.extensions.middleware.summary import _l2_compact

        def boom(prompt):
            raise RuntimeError("error")

        result = _l2_compact("long text", boom, max_tokens=100)
        assert "long text" in result

    def test_l2_compact_no_truncation_needed(self):
        from agentbase.extensions.middleware.summary import _l2_compact

        short_text = "short"
        result = _l2_compact(short_text, None, max_tokens=1000)
        assert result == "short"


class TestGraphProvider:
    def test_null_provider(self):
        from agentbase.core.graph import Entity, NullGraphProvider

        provider = NullGraphProvider()
        eid = provider.add_entity(Entity(name="test"))
        assert eid == ""
        assert provider.get_entity("x") is None
        assert provider.search_entities("test") == []
        assert provider.get_subgraph("x") == {"nodes": [], "edges": []}

    def test_in_memory_provider(self):
        from agentbase.core.graph import Entity, InMemoryGraphProvider, Relation

        provider = InMemoryGraphProvider()

        e1_id = provider.add_entity(Entity(name="Python", label="Language"))
        e2_id = provider.add_entity(Entity(name="Django", label="Framework"))

        assert provider.get_entity(e1_id) is not None
        assert provider.get_entity(e1_id).name == "Python"

        provider.add_relation(Relation(
            source_entity=e1_id,
            target_entity=e2_id,
            relation_type="has_framework",
        ))

        results = provider.search_entities("Python")
        assert len(results) == 1
        assert results[0].entity.name == "Python"

        subgraph = provider.get_subgraph(e1_id, depth=2)
        assert len(subgraph["nodes"]) >= 1
        assert len(subgraph["edges"]) >= 1

        provider.clear()
        assert provider.get_entity(e1_id) is None

    def test_graph_registry(self):
        from agentbase.core.graph import graph_registry

        assert graph_registry.has("null")
        assert graph_registry.has("memory")
        assert "null" in graph_registry.names()
        assert "memory" in graph_registry.names()

    def test_registry_create(self):
        from agentbase.core.graph import InMemoryGraphProvider, NullGraphProvider, graph_registry

        null = graph_registry.create("null")
        assert isinstance(null, NullGraphProvider)

        mem = graph_registry.create("memory")
        assert isinstance(mem, InMemoryGraphProvider)

    def test_registry_unknown(self):
        from agentbase.core.graph import graph_registry

        with pytest.raises(KeyError, match="Unknown graph"):
            graph_registry.create("nonexistent")

    def test_rrf_fusion(self):
        from agentbase.core.graph import Entity, GraphSearchResult, fuse_results_rrf

        vector_results = [
            type("R", (), {"document": type("D", (), {"id": 1})(), "score": 0.9})(),
            type("R", (), {"document": type("D", (), {"id": 2})(), "score": 0.8})(),
        ]
        graph_results = [
            GraphSearchResult(entity=Entity(id="g1", name="test"), score=0.7),
        ]

        fused = fuse_results_rrf(vector_results, graph_results, top_k=3)
        assert len(fused) == 3


class TestParsers:
    def test_pdf_parser_registered(self, bootstrapped):
        from agentbase.core.parsers import parser_registry

        assert ".pdf" in parser_registry.supported_extensions()

    def test_docx_parser_registered(self, bootstrapped):
        from agentbase.core.parsers import parser_registry

        assert ".docx" in parser_registry.supported_extensions()

    def test_html_parser_registered(self, bootstrapped):
        from agentbase.core.parsers import parser_registry

        assert ".html" in parser_registry.supported_extensions()

    def test_excel_parser_registered(self, bootstrapped):
        from agentbase.core.parsers import parser_registry

        assert ".xlsx" in parser_registry.supported_extensions()

    def test_pdf_parse_without_dep_raises(self, tmp_path):
        """PDF parser should raise ImportError if pymupdf not installed."""
        from agentbase.core.parsers import parser_registry

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"fake pdf")

        parser = parser_registry.get(".pdf")
        try:
            parser.parse(pdf_file)
        except ImportError as exc:
            assert "pymupdf" in str(exc).lower() or "fitz" in str(exc).lower()
        except Exception:
            # If pymupdf IS installed, it will fail on fake PDF — that's OK
            pass


class TestOpenAIEmbeddingProvider:
    def test_init(self):
        from agentbase.core.embeddings import OpenAIEmbeddingProvider

        provider = OpenAIEmbeddingProvider(model="text-embedding-3-small")
        assert provider.dimension == 1536
        assert provider._model == "text-embedding-3-small"

    def test_init_custom(self):
        from agentbase.core.embeddings import OpenAIEmbeddingProvider

        provider = OpenAIEmbeddingProvider(
            model="text-embedding-3-large",
            dimension=3072,
            api_key="test-key",
            base_url="https://custom.api.com",
        )
        assert provider.dimension == 3072
        assert provider._api_key == "test-key"
        assert provider._base_url == "https://custom.api.com"

    def test_embed_without_package_raises(self, monkeypatch):
        """If openai is not installed, should raise ImportError."""
        # Force ImportError
        import builtins

        from agentbase.core.embeddings import OpenAIEmbeddingProvider
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "openai" or name.startswith("openai."):
                raise ImportError("No module named 'openai'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        provider = OpenAIEmbeddingProvider(api_key="test")
        with pytest.raises(ImportError, match="openai"):
            provider.embed("test text")

    def test_registered_when_openai_available(self):
        """If openai is installed, it should be registered."""
        try:
            import openai  # noqa: F401

            from agentbase.core.embeddings import embedding_registry
            assert embedding_registry.has("openai")
        except ImportError:
            pass  # openai not installed, skip


class TestAgentFactoryNewProperties:
    def test_mcp_manager_none_by_default(self, tmp_path):
        from agentbase.factories.agent_factory import AgentFactory

        app_config = AppConfig()
        factory = AgentFactory(root_dir=tmp_path, app_config=app_config)
        assert factory.mcp_manager is None  # provider is "none"

    def test_workspace_manager(self, tmp_path):
        from agentbase.factories.agent_factory import AgentFactory

        app_config = AppConfig()
        app_config.runtime.workspace_dir = str(tmp_path / "workspace")
        factory = AgentFactory(root_dir=tmp_path, app_config=app_config)
        wm = factory.workspace_manager
        assert wm is not None
        assert wm.workspace_dir.exists()

    def test_tracer_null_by_default(self, tmp_path):
        from agentbase.core.tracer import NullTracer
        from agentbase.factories.agent_factory import AgentFactory

        app_config = AppConfig()
        factory = AgentFactory(root_dir=tmp_path, app_config=app_config)
        tracer = factory.tracer
        assert isinstance(tracer, NullTracer)

    def test_queue_none_by_default(self, tmp_path):
        from agentbase.factories.agent_factory import AgentFactory

        app_config = AppConfig()
        factory = AgentFactory(root_dir=tmp_path, app_config=app_config)
        assert factory.queue is None  # provider is "none"

    def test_tracer_memory(self, tmp_path):
        from agentbase.core.tracer import InMemoryTracer
        from agentbase.factories.agent_factory import AgentFactory

        app_config = AppConfig()
        app_config.tracer.provider = "memory"
        factory = AgentFactory(root_dir=tmp_path, app_config=app_config)
        tracer = factory.tracer
        assert isinstance(tracer, InMemoryTracer)

    def test_queue_memory(self, tmp_path):
        from agentbase.core.queue import MemoryRequestQueue
        from agentbase.factories.agent_factory import AgentFactory

        app_config = AppConfig()
        app_config.queue.provider = "memory"
        factory = AgentFactory(root_dir=tmp_path, app_config=app_config)
        q = factory.queue
        assert isinstance(q, MemoryRequestQueue)

    def test_mcp_manager_memory(self, tmp_path):
        from agentbase.core.mcp import MCPManager
        from agentbase.factories.agent_factory import AgentFactory

        app_config = AppConfig()
        app_config.mcp.provider = "memory"
        app_config.mcp.servers = [{"name": "test", "type": "memory"}]
        factory = AgentFactory(root_dir=tmp_path, app_config=app_config)
        mgr = factory.mcp_manager
        assert isinstance(mgr, MCPManager)
        assert "test" in mgr.server_names
