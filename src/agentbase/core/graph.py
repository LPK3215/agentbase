"""Knowledge graph provider registry.

Provides a pluggable interface for knowledge graph operations:
entity extraction, relation extraction, graph storage, and graph-based
retrieval with RRF (Reciprocal Rank Fusion) support.

Default: ``NullGraphProvider`` — no-op, zero dependencies.
Register real providers (Neo4j, NebulaGraph, etc.) with
``@register_graph_provider``.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable


@dataclass
class Entity:
    """A knowledge graph entity."""
    id: str = ""
    name: str = ""
    label: str = ""
    description: str = ""
    properties: dict[str, Any] = field(default_factory=dict)
    source_chunks: list[str] = field(default_factory=list)


@dataclass
class Relation:
    """A knowledge graph relation (triple)."""
    id: str = ""
    source_entity: str = ""
    target_entity: str = ""
    relation_type: str = ""
    properties: dict[str, Any] = field(default_factory=dict)
    source_chunks: list[str] = field(default_factory=list)


@dataclass
class GraphSearchResult:
    """A result from graph search."""
    entity: Entity | None = None
    relation: Relation | None = None
    score: float = 0.0
    source: str = "graph"


@runtime_checkable
class GraphProvider(Protocol):
    """Protocol for knowledge graph providers."""

    def add_entity(self, entity: Entity) -> str:
        """Add an entity. Returns entity ID."""
        ...

    def add_relation(self, relation: Relation) -> str:
        """Add a relation. Returns relation ID."""
        ...

    def get_entity(self, entity_id: str) -> Entity | None:
        """Get an entity by ID."""
        ...

    def search_entities(self, query: str, *, top_k: int = 10) -> list[GraphSearchResult]:
        """Search entities by text query."""
        ...

    def search_relations(self, query: str, *, top_k: int = 10) -> list[GraphSearchResult]:
        """Search relations by text query."""
        ...

    def get_subgraph(self, entity_id: str, *, depth: int = 2) -> dict[str, Any]:
        """Get a subgraph centered on an entity."""
        ...

    def clear(self) -> None:
        """Clear all graph data."""
        ...


class NullGraphProvider:
    """No-op graph provider — drops all operations."""

    def add_entity(self, entity: Entity) -> str:
        return entity.id or ""

    def add_relation(self, relation: Relation) -> str:
        return relation.id or ""

    def get_entity(self, entity_id: str) -> Entity | None:
        return None

    def search_entities(self, query: str, *, top_k: int = 10) -> list[GraphSearchResult]:
        return []

    def search_relations(self, query: str, *, top_k: int = 10) -> list[GraphSearchResult]:
        return []

    def get_subgraph(self, entity_id: str, *, depth: int = 2) -> dict[str, Any]:
        return {"nodes": [], "edges": []}

    def clear(self) -> None:
        pass


class InMemoryGraphProvider:
    """In-memory graph provider for testing and small datasets."""

    def __init__(self) -> None:
        self._entities: dict[str, Entity] = {}
        self._relations: dict[str, Relation] = {}

    def add_entity(self, entity: Entity) -> str:
        if not entity.id:
            entity.id = f"e_{len(self._entities)}"
        self._entities[entity.id] = entity
        return entity.id

    def add_relation(self, relation: Relation) -> str:
        if not relation.id:
            relation.id = f"r_{len(self._relations)}"
        self._relations[relation.id] = relation
        return relation.id

    def get_entity(self, entity_id: str) -> Entity | None:
        return self._entities.get(entity_id)

    def search_entities(self, query: str, *, top_k: int = 10) -> list[GraphSearchResult]:
        query_lower = query.lower()
        results: list[GraphSearchResult] = []
        for entity in self._entities.values():
            if query_lower in entity.name.lower() or query_lower in entity.description.lower():
                results.append(GraphSearchResult(entity=entity, score=1.0))
        return results[:top_k]

    def search_relations(self, query: str, *, top_k: int = 10) -> list[GraphSearchResult]:
        query_lower = query.lower()
        results: list[GraphSearchResult] = []
        for rel in self._relations.values():
            if query_lower in rel.relation_type.lower():
                results.append(GraphSearchResult(relation=rel, score=1.0))
        return results[:top_k]

    def get_subgraph(self, entity_id: str, *, depth: int = 2) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        visited: set[str] = set()

        def _expand(eid: str, d: int) -> None:
            if d <= 0 or eid in visited:
                return
            visited.add(eid)
            entity = self._entities.get(eid)
            if entity:
                nodes.append({"id": entity.id, "name": entity.name, "label": entity.label})
                for rel in self._relations.values():
                    if rel.source_entity == eid or rel.target_entity == eid:
                        edges.append({
                            "source": rel.source_entity,
                            "target": rel.target_entity,
                            "type": rel.relation_type,
                        })
                        other = rel.target_entity if rel.source_entity == eid else rel.source_entity
                        _expand(other, d - 1)

        _expand(entity_id, depth)
        return {"nodes": nodes, "edges": edges}

    def clear(self) -> None:
        self._entities.clear()
        self._relations.clear()


class GraphRegistry:
    """Thread-safe registry for graph providers."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., GraphProvider]] = {}
        self._lock = threading.RLock()

    def register(self, name: str, factory: Callable[..., GraphProvider], *, override: bool = False) -> None:
        key = name.lower()
        with self._lock:
            if key in self._factories and not override:
                raise ValueError(f"Graph provider '{name}' is already registered")
            self._factories[key] = factory

    def create(self, name: str, **kwargs: Any) -> GraphProvider:
        key = name.lower()
        with self._lock:
            if key not in self._factories:
                available = ", ".join(sorted(self._factories.keys())) or "<empty>"
                raise KeyError(f"Unknown graph provider: {name}. Available: {available}")
            factory = self._factories[key]
        return factory(**kwargs)

    def has(self, name: str) -> bool:
        with self._lock:
            return name.lower() in self._factories

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._factories.keys())

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._factories)

    def unregister(self, name: str) -> bool:
        """Remove a factory. Returns True if removed."""
        key = name.lower()
        with self._lock:
            if key not in self._factories:
                return False
            self._factories.pop(key, None)
            return True


# Global registry
graph_registry = GraphRegistry()
graph_registry.register("null", NullGraphProvider)
graph_registry.register("memory", InMemoryGraphProvider)


def register_graph_provider(name: str, *, override: bool = False):
    """Decorator to register a graph provider."""

    def decorator(factory: Callable[..., GraphProvider]):
        graph_registry.register(name, factory, override=override)
        return factory

    return decorator


def fuse_results_rrf(
    vector_results: list[Any],
    graph_results: list[GraphSearchResult],
    *,
    k: int = 60,
    top_k: int = 10,
) -> list[Any]:
    """Fuse vector and graph search results using Reciprocal Rank Fusion.

    Args:
        vector_results: Results from vector search (with ``score`` attribute or key).
        graph_results: Results from graph search.
        k: RRF constant (default 60).
        top_k: Number of results to return.

    Returns:
        Fused and re-ranked results.
    """
    scores: dict[str, float] = {}
    results_map: dict[str, Any] = {}

    for rank, result in enumerate(vector_results):
        # Try to get a unique identifier from the result
        if hasattr(result, "document"):
            doc_id = str(result.document.id) if result.document.id else str(rank)
        elif isinstance(result, dict):
            doc_id = str(result.get("id", rank))
        else:
            doc_id = str(rank)

        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
        results_map[doc_id] = result

    for rank, result in enumerate(graph_results):
        if result.entity:
            gid = f"graph:{result.entity.id}"
        elif result.relation:
            gid = f"graph:{result.relation.id}"
        else:
            gid = f"graph:{rank}"

        scores[gid] = scores.get(gid, 0.0) + 1.0 / (k + rank)
        results_map[gid] = result

    # Sort by fused score
    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return [results_map[rid] for rid in sorted_ids[:top_k]]


# ---------------------------------------------------------------------------
# Neo4j graph provider
# ---------------------------------------------------------------------------

class Neo4jGraphProvider:
    """Neo4j-backed knowledge graph provider.

    Stores entities as nodes and relations as edges in Neo4j.
    Supports Cypher queries for graph traversal and retrieval.

    Requires ``neo4j`` package and a running Neo4j instance.

    Usage::

        from agentbase.core.graph import Neo4jGraphProvider
        provider = Neo4jGraphProvider(uri="bolt://localhost:7687",
                                       user="neo4j", password="password")

    Or via config::

        graph:
          provider: neo4j
          options:
            uri: bolt://localhost:7687
            user: neo4j
            password: password
    """

    def __init__(
        self,
        *,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "neo4j",
    ) -> None:
        self._uri = uri
        self._user = user
        self._password = password
        self._driver = None

    def _get_driver(self) -> Any:
        if self._driver is None:
            try:
                from neo4j import GraphDatabase
            except ImportError as exc:
                raise ImportError(
                    "Neo4j graph provider requires the neo4j package. "
                    "Install with: pip install neo4j"
                ) from exc
            self._driver = GraphDatabase.driver(self._uri, auth=(self._user, self._password))
        return self._driver

    def add_entity(self, entity: Entity) -> str:
        import json
        if not entity.id:
            from uuid import uuid4
            entity.id = str(uuid4())
        driver = self._get_driver()
        with driver.session() as session:
            session.run(
                "MERGE (e:Entity {id: $id}) SET e.name = $name, e.label = $label, "
                "e.description = $desc, e.properties = $props",
                id=entity.id,
                name=entity.name,
                label=entity.label,
                desc=entity.description,
                props=json.dumps(entity.properties, ensure_ascii=False),
            )
        return entity.id

    def add_relation(self, relation: Relation) -> str:
        import json
        if not relation.id:
            from uuid import uuid4
            relation.id = str(uuid4())
        driver = self._get_driver()
        with driver.session() as session:
            session.run(
                "MATCH (s:Entity {id: $src}), (t:Entity {id: $tgt}) "
                "MERGE (s)-[r:RELATES {type: $rtype}]->(t) "
                "SET r.id = $rid, r.properties = $props",
                src=relation.source_entity,
                tgt=relation.target_entity,
                rtype=relation.relation_type,
                rid=relation.id,
                props=json.dumps(relation.properties, ensure_ascii=False),
            )
        return relation.id

    def get_entity(self, entity_id: str) -> Entity | None:
        import json
        driver = self._get_driver()
        with driver.session() as session:
            result = session.run("MATCH (e:Entity {id: $id}) RETURN e", id=entity_id)
            record = result.single()
            if record is None:
                return None
            node = record["e"]
            props_raw = node.get("properties", "{}")
            props = json.loads(props_raw) if isinstance(props_raw, str) else dict(props_raw or {})
            return Entity(
                id=node["id"],
                name=node.get("name", ""),
                label=node.get("label", ""),
                description=node.get("description", ""),
                properties=props,
            )

    def search_entities(self, query: str, *, top_k: int = 10) -> list[GraphSearchResult]:
        import json
        driver = self._get_driver()
        with driver.session() as session:
            result = session.run(
                "MATCH (e:Entity) WHERE e.name CONTAINS $q OR e.description CONTAINS $q "
                "RETURN e LIMIT $limit",
                q=query,
                limit=top_k,
            )
            results: list[GraphSearchResult] = []
            for record in result:
                node = record["e"]
                props_raw = node.get("properties", "{}")
                props = json.loads(props_raw) if isinstance(props_raw, str) else dict(props_raw or {})
                results.append(GraphSearchResult(
                    entity=Entity(
                        id=node["id"],
                        name=node.get("name", ""),
                        label=node.get("label", ""),
                        description=node.get("description", ""),
                        properties=props,
                    ),
                    score=1.0,
                    source="neo4j",
                ))
            return results

    def search_relations(self, query: str, *, top_k: int = 10) -> list[GraphSearchResult]:
        driver = self._get_driver()
        with driver.session() as session:
            result = session.run(
                "MATCH (s:Entity)-[r:RELATES]->(t:Entity) "
                "WHERE r.type CONTAINS $q "
                "RETURN s, r, t LIMIT $limit",
                q=query,
                limit=top_k,
            )
            results: list[GraphSearchResult] = []
            for record in result:
                results.append(GraphSearchResult(
                    relation=Relation(
                        id=record["r"].get("id", ""),
                        source_entity=record["s"]["id"],
                        target_entity=record["t"]["id"],
                        relation_type=record["r"].get("type", ""),
                    ),
                    score=1.0,
                    source="neo4j",
                ))
            return results

    def delete_entity(self, entity_id: str) -> bool:
        driver = self._get_driver()
        with driver.session() as session:
            result = session.run(
                "MATCH (e:Entity {id: $id}) DETACH DELETE e",
                id=entity_id,
            )
            return result.consume().counters.nodes_deleted > 0

    def get_relations(self, entity_id: str) -> list[Relation]:
        driver = self._get_driver()
        with driver.session() as session:
            result = session.run(
                "MATCH (s:Entity {id: $id})-[r:RELATES]->(t:Entity) "
                "RETURN s.id, t.id, r.type, r.id, r.properties",
                id=entity_id,
            )
            relations: list[Relation] = []
            for record in result:
                relations.append(Relation(
                    id=record["r.id"],
                    source_entity=record["s.id"],
                    target_entity=record["t.id"],
                    relation_type=record["r.type"],
                    properties=dict(record.get("r.properties", {})),
                ))
            return relations

    def close(self) -> None:
        if self._driver:
            self._driver.close()
            self._driver = None


# Register Neo4j if available
try:
    import neo4j  # noqa: F401
    graph_registry.register("neo4j", Neo4jGraphProvider, override=True)
except ImportError:
    pass
