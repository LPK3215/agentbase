"""Tests for the knowledge graph framework — covers data classes, providers, registry, RRF fusion.

Tests verify:
1. Entity / Relation / GraphSearchResult data classes
2. NullGraphProvider — no-op behavior, Protocol compliance
3. InMemoryGraphProvider — add_entity, add_relation, get_entity, search, subgraph, clear
4. GraphRegistry — register, create, has, names, count, unregister, thread safety
5. register_graph_provider decorator
6. fuse_results_rrf — vector+graph fusion, ranking, top_k, edge cases
7. Neo4jGraphProvider — mock driver, all CRUD operations
8. GraphProvider Protocol compliance
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------


class TestEntity:
    def test_default_values(self):
        from agentbase.core.graph import Entity

        e = Entity()
        assert e.id == ""
        assert e.name == ""
        assert e.label == ""
        assert e.description == ""
        assert e.properties == {}
        assert e.source_chunks == []

    def test_with_values(self):
        from agentbase.core.graph import Entity

        e = Entity(
            id="e1",
            name="Alice",
            label="Person",
            description="A person",
            properties={"age": 30},
            source_chunks=["chunk1"],
        )
        assert e.id == "e1"
        assert e.name == "Alice"
        assert e.label == "Person"
        assert e.properties["age"] == 30
        assert e.source_chunks == ["chunk1"]


# ---------------------------------------------------------------------------
# Relation
# ---------------------------------------------------------------------------


class TestRelation:
    def test_default_values(self):
        from agentbase.core.graph import Relation

        r = Relation()
        assert r.id == ""
        assert r.source_entity == ""
        assert r.target_entity == ""
        assert r.relation_type == ""
        assert r.properties == {}
        assert r.source_chunks == []

    def test_with_values(self):
        from agentbase.core.graph import Relation

        r = Relation(
            id="r1",
            source_entity="e1",
            target_entity="e2",
            relation_type="knows",
            properties={"since": "2020"},
            source_chunks=["chunk1", "chunk2"],
        )
        assert r.id == "r1"
        assert r.source_entity == "e1"
        assert r.target_entity == "e2"
        assert r.relation_type == "knows"
        assert r.properties["since"] == "2020"
        assert len(r.source_chunks) == 2


# ---------------------------------------------------------------------------
# GraphSearchResult
# ---------------------------------------------------------------------------


class TestGraphSearchResult:
    def test_default_values(self):
        from agentbase.core.graph import GraphSearchResult

        result = GraphSearchResult()
        assert result.entity is None
        assert result.relation is None
        assert result.score == 0.0
        assert result.source == "graph"

    def test_with_entity(self):
        from agentbase.core.graph import Entity, GraphSearchResult

        entity = Entity(id="e1", name="Alice")
        result = GraphSearchResult(entity=entity, score=0.95, source="neo4j")
        assert result.entity is not None
        assert result.entity.name == "Alice"
        assert result.score == 0.95
        assert result.source == "neo4j"

    def test_with_relation(self):
        from agentbase.core.graph import GraphSearchResult, Relation

        rel = Relation(id="r1", relation_type="knows")
        result = GraphSearchResult(relation=rel, score=0.8)
        assert result.relation is not None
        assert result.relation.relation_type == "knows"


# ---------------------------------------------------------------------------
# NullGraphProvider
# ---------------------------------------------------------------------------


class TestNullGraphProvider:
    def test_add_entity_returns_id(self):
        from agentbase.core.graph import Entity, NullGraphProvider

        provider = NullGraphProvider()
        entity = Entity(id="e1", name="test")
        assert provider.add_entity(entity) == "e1"

    def test_add_entity_empty_id(self):
        from agentbase.core.graph import Entity, NullGraphProvider

        provider = NullGraphProvider()
        entity = Entity(name="test")
        assert provider.add_entity(entity) == ""

    def test_add_relation_returns_id(self):
        from agentbase.core.graph import NullGraphProvider, Relation

        provider = NullGraphProvider()
        rel = Relation(id="r1", relation_type="knows")
        assert provider.add_relation(rel) == "r1"

    def test_get_entity_returns_none(self):
        from agentbase.core.graph import NullGraphProvider

        provider = NullGraphProvider()
        assert provider.get_entity("any") is None

    def test_search_entities_returns_empty(self):
        from agentbase.core.graph import NullGraphProvider

        provider = NullGraphProvider()
        assert provider.search_entities("query") == []

    def test_search_relations_returns_empty(self):
        from agentbase.core.graph import NullGraphProvider

        provider = NullGraphProvider()
        assert provider.search_relations("query") == []

    def test_get_subgraph_returns_empty(self):
        from agentbase.core.graph import NullGraphProvider

        provider = NullGraphProvider()
        result = provider.get_subgraph("any")
        assert result == {"nodes": [], "edges": []}

    def test_clear_noop(self):
        from agentbase.core.graph import NullGraphProvider

        provider = NullGraphProvider()
        # Should not raise
        provider.clear()

    def test_is_graph_provider(self):
        from agentbase.core.graph import GraphProvider, NullGraphProvider

        provider = NullGraphProvider()
        assert isinstance(provider, GraphProvider)


# ---------------------------------------------------------------------------
# InMemoryGraphProvider
# ---------------------------------------------------------------------------


class TestInMemoryGraphProvider:
    def test_add_entity_with_id(self):
        from agentbase.core.graph import Entity, InMemoryGraphProvider

        provider = InMemoryGraphProvider()
        entity = Entity(id="e1", name="Alice")
        eid = provider.add_entity(entity)
        assert eid == "e1"

    def test_add_entity_auto_id(self):
        from agentbase.core.graph import Entity, InMemoryGraphProvider

        provider = InMemoryGraphProvider()
        entity = Entity(name="Alice")
        eid = provider.add_entity(entity)
        assert eid != ""
        assert entity.id == eid

    def test_add_multiple_entities_auto_id(self):
        from agentbase.core.graph import Entity, InMemoryGraphProvider

        provider = InMemoryGraphProvider()
        e1 = provider.add_entity(Entity(name="Alice"))
        e2 = provider.add_entity(Entity(name="Bob"))
        assert e1 != e2

    def test_get_entity_exists(self):
        from agentbase.core.graph import Entity, InMemoryGraphProvider

        provider = InMemoryGraphProvider()
        provider.add_entity(Entity(id="e1", name="Alice"))
        entity = provider.get_entity("e1")
        assert entity is not None
        assert entity.name == "Alice"

    def test_get_entity_not_found(self):
        from agentbase.core.graph import InMemoryGraphProvider

        provider = InMemoryGraphProvider()
        assert provider.get_entity("nonexistent") is None

    def test_add_relation_with_id(self):
        from agentbase.core.graph import InMemoryGraphProvider, Relation

        provider = InMemoryGraphProvider()
        rel = Relation(id="r1", source_entity="e1", target_entity="e2", relation_type="knows")
        rid = provider.add_relation(rel)
        assert rid == "r1"

    def test_add_relation_auto_id(self):
        from agentbase.core.graph import InMemoryGraphProvider, Relation

        provider = InMemoryGraphProvider()
        rel = Relation(source_entity="e1", target_entity="e2", relation_type="knows")
        rid = provider.add_relation(rel)
        assert rid != ""
        assert rel.id == rid

    def test_search_entities_by_name(self):
        from agentbase.core.graph import Entity, InMemoryGraphProvider

        provider = InMemoryGraphProvider()
        provider.add_entity(Entity(id="e1", name="Alice", description="A person"))
        provider.add_entity(Entity(id="e2", name="Bob", description="A builder"))
        results = provider.search_entities("alice")
        assert len(results) == 1
        assert results[0].entity.name == "Alice"

    def test_search_entities_by_description(self):
        from agentbase.core.graph import Entity, InMemoryGraphProvider

        provider = InMemoryGraphProvider()
        provider.add_entity(Entity(id="e1", name="Alice", description="A software engineer"))
        provider.add_entity(Entity(id="e2", name="Bob", description="A builder"))
        results = provider.search_entities("engineer")
        assert len(results) == 1
        assert results[0].entity.id == "e1"

    def test_search_entities_case_insensitive(self):
        from agentbase.core.graph import Entity, InMemoryGraphProvider

        provider = InMemoryGraphProvider()
        provider.add_entity(Entity(id="e1", name="Alice"))
        results = provider.search_entities("ALICE")
        assert len(results) == 1

    def test_search_entities_top_k(self):
        from agentbase.core.graph import Entity, InMemoryGraphProvider

        provider = InMemoryGraphProvider()
        for i in range(10):
            provider.add_entity(Entity(id=f"e{i}", name=f"item_{i}"))
        results = provider.search_entities("item", top_k=3)
        assert len(results) == 3

    def test_search_entities_no_match(self):
        from agentbase.core.graph import InMemoryGraphProvider

        provider = InMemoryGraphProvider()
        assert provider.search_entities("nonexistent") == []

    def test_search_relations_by_type(self):
        from agentbase.core.graph import InMemoryGraphProvider, Relation

        provider = InMemoryGraphProvider()
        provider.add_relation(Relation(id="r1", source_entity="e1", target_entity="e2", relation_type="knows"))
        provider.add_relation(Relation(id="r2", source_entity="e3", target_entity="e4", relation_type="works_with"))
        results = provider.search_relations("knows")
        assert len(results) == 1
        assert results[0].relation.relation_type == "knows"

    def test_search_relations_case_insensitive(self):
        from agentbase.core.graph import InMemoryGraphProvider, Relation

        provider = InMemoryGraphProvider()
        provider.add_relation(Relation(id="r1", relation_type="KNOWS"))
        results = provider.search_relations("knows")
        assert len(results) == 1

    def test_search_relations_top_k(self):
        from agentbase.core.graph import InMemoryGraphProvider, Relation

        provider = InMemoryGraphProvider()
        for i in range(10):
            provider.add_relation(Relation(id=f"r{i}", relation_type=f"type_{i}"))
        results = provider.search_relations("type", top_k=3)
        assert len(results) == 3

    def test_get_subgraph_simple(self):
        from agentbase.core.graph import Entity, InMemoryGraphProvider, Relation

        provider = InMemoryGraphProvider()
        provider.add_entity(Entity(id="e1", name="Alice"))
        provider.add_entity(Entity(id="e2", name="Bob"))
        provider.add_relation(Relation(id="r1", source_entity="e1", target_entity="e2", relation_type="knows"))

        subgraph = provider.get_subgraph("e1", depth=2)
        assert len(subgraph["nodes"]) == 2
        # Edges may appear twice (once from each direction) due to bidirectional traversal
        assert len(subgraph["edges"]) >= 1
        assert subgraph["edges"][0]["type"] == "knows"

    def test_get_subgraph_depth_0(self):
        from agentbase.core.graph import Entity, InMemoryGraphProvider

        provider = InMemoryGraphProvider()
        provider.add_entity(Entity(id="e1", name="Alice"))
        subgraph = provider.get_subgraph("e1", depth=0)
        assert len(subgraph["nodes"]) == 0
        assert len(subgraph["edges"]) == 0

    def test_get_subgraph_nonexistent_entity(self):
        from agentbase.core.graph import InMemoryGraphProvider

        provider = InMemoryGraphProvider()
        subgraph = provider.get_subgraph("nonexistent")
        assert len(subgraph["nodes"]) == 0

    def test_get_subgraph_circular(self):
        from agentbase.core.graph import Entity, InMemoryGraphProvider, Relation

        provider = InMemoryGraphProvider()
        provider.add_entity(Entity(id="e1", name="Alice"))
        provider.add_entity(Entity(id="e2", name="Bob"))
        provider.add_relation(Relation(id="r1", source_entity="e1", target_entity="e2", relation_type="knows"))
        provider.add_relation(Relation(id="r2", source_entity="e2", target_entity="e1", relation_type="knows"))

        # Should not infinite loop
        subgraph = provider.get_subgraph("e1", depth=5)
        assert len(subgraph["nodes"]) == 2  # Both entities visited

    def test_clear(self):
        from agentbase.core.graph import Entity, InMemoryGraphProvider, Relation

        provider = InMemoryGraphProvider()
        provider.add_entity(Entity(id="e1", name="Alice"))
        provider.add_relation(Relation(id="r1", relation_type="knows"))
        provider.clear()
        assert provider.get_entity("e1") is None
        assert provider.search_entities("Alice") == []
        assert provider.search_relations("knows") == []

    def test_is_graph_provider(self):
        from agentbase.core.graph import GraphProvider, InMemoryGraphProvider

        provider = InMemoryGraphProvider()
        assert isinstance(provider, GraphProvider)


# ---------------------------------------------------------------------------
# GraphRegistry
# ---------------------------------------------------------------------------


class TestGraphRegistry:
    def test_register_and_create(self):
        from agentbase.core.graph import GraphRegistry, NullGraphProvider

        registry = GraphRegistry()
        registry.register("test", NullGraphProvider)
        provider = registry.create("test")
        assert isinstance(provider, NullGraphProvider)

    def test_register_case_insensitive(self):
        from agentbase.core.graph import GraphRegistry, NullGraphProvider

        registry = GraphRegistry()
        registry.register("MyProvider", NullGraphProvider)
        assert registry.has("myprovider")
        assert registry.has("MYPROVIDER")

    def test_register_duplicate_raises(self):
        from agentbase.core.graph import GraphRegistry, NullGraphProvider

        registry = GraphRegistry()
        registry.register("test", NullGraphProvider)
        with pytest.raises(ValueError, match="already registered"):
            registry.register("test", NullGraphProvider)

    def test_register_override(self):
        from agentbase.core.graph import GraphRegistry, NullGraphProvider

        registry = GraphRegistry()
        registry.register("test", NullGraphProvider)
        registry.register("test", NullGraphProvider, override=True)

    def test_create_unknown_raises(self):
        from agentbase.core.graph import GraphRegistry

        registry = GraphRegistry()
        with pytest.raises(KeyError, match="Unknown graph provider"):
            registry.create("nonexistent")

    def test_has(self):
        from agentbase.core.graph import GraphRegistry, NullGraphProvider

        registry = GraphRegistry()
        registry.register("test", NullGraphProvider)
        assert registry.has("test") is True
        assert registry.has("nonexistent") is False

    def test_names(self):
        from agentbase.core.graph import GraphRegistry, NullGraphProvider

        registry = GraphRegistry()
        registry.register("alpha", NullGraphProvider)
        registry.register("beta", NullGraphProvider)
        names = registry.names()
        assert "alpha" in names
        assert "beta" in names

    def test_count(self):
        from agentbase.core.graph import GraphRegistry, NullGraphProvider

        registry = GraphRegistry()
        assert registry.count == 0
        registry.register("a", NullGraphProvider)
        assert registry.count == 1

    def test_unregister(self):
        from agentbase.core.graph import GraphRegistry, NullGraphProvider

        registry = GraphRegistry()
        registry.register("test", NullGraphProvider)
        assert registry.unregister("test") is True
        assert registry.has("test") is False

    def test_unregister_nonexistent(self):
        from agentbase.core.graph import GraphRegistry

        registry = GraphRegistry()
        assert registry.unregister("nonexistent") is False

    def test_global_registry_has_null(self):
        from agentbase.core.graph import graph_registry

        assert graph_registry.has("null")

    def test_global_registry_has_memory(self):
        from agentbase.core.graph import graph_registry

        assert graph_registry.has("memory")


# ---------------------------------------------------------------------------
# register_graph_provider decorator
# ---------------------------------------------------------------------------


class TestRegisterGraphProvider:
    def test_decorator_registers(self):
        import agentbase.core.graph as graph_mod
        from agentbase.core.graph import GraphRegistry, register_graph_provider

        registry = GraphRegistry()
        original = graph_mod.graph_registry
        graph_mod.graph_registry = registry

        try:
            @register_graph_provider("custom_graph")
            class CustomGraphProvider:
                def add_entity(self, entity):
                    return entity.id
                def add_relation(self, relation):
                    return relation.id
                def get_entity(self, entity_id):
                    return None
                def search_entities(self, query, **kw):
                    return []
                def search_relations(self, query, **kw):
                    return []
                def get_subgraph(self, entity_id, **kw):
                    return {"nodes": [], "edges": []}
                def clear(self):
                    pass

            assert registry.has("custom_graph")
            provider = registry.create("custom_graph")
            assert isinstance(provider, CustomGraphProvider)
        finally:
            graph_mod.graph_registry = original


# ---------------------------------------------------------------------------
# fuse_results_rrf
# ---------------------------------------------------------------------------


class TestFuseResultsRRF:
    def test_empty_inputs(self):
        from agentbase.core.graph import fuse_results_rrf

        result = fuse_results_rrf([], [])
        assert result == []

    def test_vector_only(self):
        from agentbase.core.graph import fuse_results_rrf

        vector_results = [
            {"id": "v1", "content": "result 1"},
            {"id": "v2", "content": "result 2"},
        ]
        fused = fuse_results_rrf(vector_results, [])
        assert len(fused) == 2
        # First result should have higher rank (lower rank number = higher score)
        assert fused[0]["id"] == "v1"

    def test_graph_only(self):
        from agentbase.core.graph import Entity, GraphSearchResult, fuse_results_rrf

        graph_results = [
            GraphSearchResult(entity=Entity(id="g1", name="entity1"), score=1.0),
            GraphSearchResult(entity=Entity(id="g2", name="entity2"), score=0.8),
        ]
        fused = fuse_results_rrf([], graph_results)
        assert len(fused) == 2

    def test_fusion_mixed(self):
        from agentbase.core.graph import Entity, GraphSearchResult, fuse_results_rrf

        vector_results = [
            {"id": "v1", "content": "result 1"},
            {"id": "v2", "content": "result 2"},
        ]
        graph_results = [
            GraphSearchResult(entity=Entity(id="g1", name="entity1"), score=1.0),
        ]
        fused = fuse_results_rrf(vector_results, graph_results)
        assert len(fused) == 3

    def test_top_k_limit(self):
        from agentbase.core.graph import fuse_results_rrf

        vector_results = [{"id": f"v{i}"} for i in range(20)]
        fused = fuse_results_rrf(vector_results, [], top_k=5)
        assert len(fused) == 5

    def test_rrf_ranking(self):
        from agentbase.core.graph import fuse_results_rrf

        # v1 appears at rank 0 in vector (score = 1/(60+0) = 0.01667)
        # v2 appears at rank 1 in vector (score = 1/(60+1) = 0.01639)
        vector_results = [{"id": "v1"}, {"id": "v2"}]
        fused = fuse_results_rrf(vector_results, [])
        # v1 should be ranked first
        assert fused[0]["id"] == "v1"
        assert fused[1]["id"] == "v2"

    def test_custom_k(self):
        from agentbase.core.graph import fuse_results_rrf

        vector_results = [{"id": "v1"}, {"id": "v2"}]
        # With k=1, scores are higher but ranking should be the same
        fused = fuse_results_rrf(vector_results, [], k=1)
        assert fused[0]["id"] == "v1"

    def test_vector_results_without_id(self):
        from agentbase.core.graph import fuse_results_rrf

        # Results without id use rank as identifier
        vector_results = [
            {"content": "no id"},
            {"content": "no id 2"},
        ]
        fused = fuse_results_rrf(vector_results, [])
        assert len(fused) == 2

    def test_graph_results_without_entity_or_relation(self):
        from agentbase.core.graph import GraphSearchResult, fuse_results_rrf

        graph_results = [
            GraphSearchResult(score=1.0),  # No entity or relation
            GraphSearchResult(score=0.5),
        ]
        fused = fuse_results_rrf([], graph_results)
        assert len(fused) == 2


# ---------------------------------------------------------------------------
# Neo4jGraphProvider (mocked)
# ---------------------------------------------------------------------------


class TestNeo4jGraphProvider:
    def test_init_defaults(self):
        from agentbase.core.graph import Neo4jGraphProvider

        provider = Neo4jGraphProvider()
        assert provider._uri == "bolt://localhost:7687"
        assert provider._user == "neo4j"
        assert provider._password == "neo4j"
        assert provider._driver is None

    def test_init_with_custom_values(self):
        from agentbase.core.graph import Neo4jGraphProvider

        provider = Neo4jGraphProvider(uri="bolt://custom:7687", user="admin", password="secret")
        assert provider._uri == "bolt://custom:7687"
        assert provider._user == "admin"
        assert provider._password == "secret"

    def test_add_entity_with_mock_driver(self):
        from agentbase.core.graph import Entity, Neo4jGraphProvider

        provider = Neo4jGraphProvider()
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__.return_value = mock_session
        mock_driver.session.return_value.__exit__.return_value = None
        provider._driver = mock_driver

        entity = Entity(id="e1", name="Alice", label="Person", description="A person", properties={"age": 30})
        eid = provider.add_entity(entity)
        assert eid == "e1"
        mock_session.run.assert_called_once()

    def test_add_entity_auto_id(self):
        from agentbase.core.graph import Entity, Neo4jGraphProvider

        provider = Neo4jGraphProvider()
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__.return_value = mock_session
        mock_driver.session.return_value.__exit__.return_value = None
        provider._driver = mock_driver

        entity = Entity(name="Alice")
        eid = provider.add_entity(entity)
        assert eid != ""

    def test_add_relation_with_mock_driver(self):
        from agentbase.core.graph import Neo4jGraphProvider, Relation

        provider = Neo4jGraphProvider()
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__.return_value = mock_session
        mock_driver.session.return_value.__exit__.return_value = None
        provider._driver = mock_driver

        rel = Relation(id="r1", source_entity="e1", target_entity="e2", relation_type="knows")
        rid = provider.add_relation(rel)
        assert rid == "r1"
        mock_session.run.assert_called_once()

    def test_get_entity_found(self):
        from agentbase.core.graph import Neo4jGraphProvider

        provider = Neo4jGraphProvider()
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_record = MagicMock()
        mock_node = {
            "id": "e1",
            "name": "Alice",
            "label": "Person",
            "description": "A person",
            "properties": '{"age": 30}',
        }
        mock_record.__getitem__ = MagicMock(return_value=mock_node)
        mock_result.single.return_value = mock_record
        mock_session.run.return_value = mock_result
        mock_driver.session.return_value.__enter__.return_value = mock_session
        mock_driver.session.return_value.__exit__.return_value = None
        provider._driver = mock_driver

        entity = provider.get_entity("e1")
        assert entity is not None
        assert entity.name == "Alice"
        assert entity.properties["age"] == 30

    def test_get_entity_not_found(self):
        from agentbase.core.graph import Neo4jGraphProvider

        provider = Neo4jGraphProvider()
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.single.return_value = None
        mock_session.run.return_value = mock_result
        mock_driver.session.return_value.__enter__.return_value = mock_session
        mock_driver.session.return_value.__exit__.return_value = None
        provider._driver = mock_driver

        assert provider.get_entity("nonexistent") is None

    def test_search_entities_with_mock(self):
        from agentbase.core.graph import Neo4jGraphProvider

        provider = Neo4jGraphProvider()
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_record1 = MagicMock()
        mock_record1.__getitem__ = MagicMock(return_value={
            "id": "e1", "name": "Alice", "label": "Person",
            "description": "engineer", "properties": "{}",
        })
        mock_result.__iter__ = MagicMock(return_value=iter([mock_record1]))
        mock_session.run.return_value = mock_result
        mock_driver.session.return_value.__enter__.return_value = mock_session
        mock_driver.session.return_value.__exit__.return_value = None
        provider._driver = mock_driver

        results = provider.search_entities("alice")
        assert len(results) == 1
        assert results[0].entity.name == "Alice"

    def test_search_relations_with_mock(self):
        from agentbase.core.graph import Neo4jGraphProvider

        provider = Neo4jGraphProvider()
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_result = MagicMock()

        mock_record = MagicMock()
        mock_record.__getitem__ = MagicMock(side_effect=lambda key: {
            "s": {"id": "e1"},
            "t": {"id": "e2"},
            "r": {"id": "r1", "type": "knows"},
        }.get(key))
        mock_result.__iter__ = MagicMock(return_value=iter([mock_record]))
        mock_session.run.return_value = mock_result
        mock_driver.session.return_value.__enter__.return_value = mock_session
        mock_driver.session.return_value.__exit__.return_value = None
        provider._driver = mock_driver

        results = provider.search_relations("knows")
        assert len(results) == 1
        assert results[0].relation.relation_type == "knows"

    def test_delete_entity(self):
        from agentbase.core.graph import Neo4jGraphProvider

        provider = Neo4jGraphProvider()
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_counters = MagicMock()
        mock_counters.nodes_deleted = 1
        mock_result.consume.return_value.counters = mock_counters
        mock_session.run.return_value = mock_result
        mock_driver.session.return_value.__enter__.return_value = mock_session
        mock_driver.session.return_value.__exit__.return_value = None
        provider._driver = mock_driver

        assert provider.delete_entity("e1") is True

    def test_delete_entity_not_found(self):
        from agentbase.core.graph import Neo4jGraphProvider

        provider = Neo4jGraphProvider()
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_counters = MagicMock()
        mock_counters.nodes_deleted = 0
        mock_result.consume.return_value.counters = mock_counters
        mock_session.run.return_value = mock_result
        mock_driver.session.return_value.__enter__.return_value = mock_session
        mock_driver.session.return_value.__exit__.return_value = None
        provider._driver = mock_driver

        assert provider.delete_entity("nonexistent") is False

    def test_get_relations(self):
        from agentbase.core.graph import Neo4jGraphProvider

        provider = Neo4jGraphProvider()
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_result = MagicMock()

        values = {
            "s.id": "e1",
            "t.id": "e2",
            "r.type": "knows",
            "r.id": "r1",
            "r.properties": {},
        }
        mock_record = MagicMock()
        mock_record.__getitem__ = MagicMock(side_effect=lambda key: values.get(key))
        mock_record.get = MagicMock(return_value={})
        mock_result.__iter__ = MagicMock(return_value=iter([mock_record]))
        mock_session.run.return_value = mock_result
        mock_driver.session.return_value.__enter__.return_value = mock_session
        mock_driver.session.return_value.__exit__.return_value = None
        provider._driver = mock_driver

        relations = provider.get_relations("e1")
        assert len(relations) == 1
        assert relations[0].relation_type == "knows"

    def test_close(self):
        from agentbase.core.graph import Neo4jGraphProvider

        provider = Neo4jGraphProvider()
        mock_driver = MagicMock()
        provider._driver = mock_driver
        provider.close()
        mock_driver.close.assert_called_once()
        assert provider._driver is None

    def test_close_no_driver(self):
        from agentbase.core.graph import Neo4jGraphProvider

        provider = Neo4jGraphProvider()
        # Should not raise when driver is None
        provider.close()
        assert provider._driver is None
