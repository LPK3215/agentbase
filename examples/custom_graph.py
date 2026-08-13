#!/usr/bin/env python
"""Cookbook: 注册自定义 Graph Provider。

演示如何通过 @register_graph_provider 装饰器注册一个自定义的
知识图谱 Provider，替换默认的 NullGraphProvider。

本示例实现一个 InMemoryGraph：内存中的知识图谱，
支持实体/关系 CRUD 和搜索。

运行方式:
    python examples/custom_graph.py
    python examples/custom_graph.py --help
"""
from __future__ import annotations

import argparse
import sys
import uuid
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

from agentbase.core.graph import (
    Entity,
    GraphSearchResult,
    Relation,
)


class InMemoryGraph:
    """内存知识图谱 Provider。

    使用字典存储实体和关系，支持简单的关键词搜索。
    适合开发和测试环境。
    """

    def __init__(self) -> None:
        self._entities: dict[str, Entity] = {}
        self._relations: dict[str, Relation] = {}

    def add_entity(self, entity: Entity) -> str:
        eid = entity.id or str(uuid.uuid4())
        entity.id = eid
        self._entities[eid] = entity
        return eid

    def add_relation(self, relation: Relation) -> str:
        rid = relation.id or str(uuid.uuid4())
        relation.id = rid
        self._relations[rid] = relation
        return rid

    def get_entity(self, entity_id: str) -> Entity | None:
        return self._entities.get(entity_id)

    def search_entities(self, query: str, *, top_k: int = 10) -> list[GraphSearchResult]:
        query_lower = query.lower()
        results: list[GraphSearchResult] = []
        for entity in self._entities.values():
            score = 0.0
            if query_lower in entity.name.lower():
                score += 1.0
            if query_lower in entity.description.lower():
                score += 0.5
            if query_lower in entity.label.lower():
                score += 0.3
            if score > 0:
                results.append(GraphSearchResult(entity=entity, score=score, source="memory"))
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]

    def search_relations(self, query: str, *, top_k: int = 10) -> list[GraphSearchResult]:
        query_lower = query.lower()
        results: list[GraphSearchResult] = []
        for rel in self._relations.values():
            score = 0.0
            if query_lower in rel.relation_type.lower():
                score += 1.0
            if score > 0:
                results.append(GraphSearchResult(relation=rel, score=score, source="memory"))
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]

    def get_subgraph(self, entity_id: str, *, depth: int = 2) -> dict[str, Any]:
        """获取以指定实体为中心的子图。"""
        center = self._entities.get(entity_id)
        if not center:
            return {"entities": [], "relations": []}

        related_entities = [center]
        related_relations = []

        for rel in self._relations.values():
            if rel.source_entity == entity_id or rel.target_entity == entity_id:
                related_relations.append(rel)
                other_id = rel.target_entity if rel.source_entity == entity_id else rel.source_entity
                other = self._entities.get(other_id)
                if other and other not in related_entities:
                    related_entities.append(other)

        return {
            "entities": [e.to_dict() if hasattr(e, "to_dict") else str(e) for e in related_entities],
            "relations": [r.to_dict() if hasattr(r, "to_dict") else str(r) for r in related_relations],
        }

    def clear(self) -> None:
        self._entities.clear()
        self._relations.clear()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="注册并测试自定义 In-Memory Graph Provider",
    )
    parser.parse_args()

    print("=" * 60)
    print("Cookbook: 自定义 Graph Provider (In-Memory)")
    print("=" * 60)

    # 1. 注册
    from agentbase.core.graph import graph_registry
    print("\n1. 注册前已注册 Provider:", graph_registry.names())

    # 2. 注册（直接注册 factory 类）
    graph_registry.register("memory", InMemoryGraph, override=True)
    print("2. 已注册 'memory' Graph Provider")

    # 3. 获取 Provider
    provider = graph_registry.create("memory")
    print(f"3. 获取 Provider: {provider.__class__.__name__}")

    # 4. 添加实体
    e1_id = provider.add_entity(Entity(
        name="AgentBase", label="Project",
        description="AI Agent backend scaffold with 33 tools and 9 registries",
    ))
    e2_id = provider.add_entity(Entity(
        name="LangChain", label="Framework",
        description="Framework for building LLM applications",
    ))
    e3_id = provider.add_entity(Entity(
        name="FastAPI", label="Framework",
        description="Modern Python web framework for APIs",
    ))
    print("\n4. 添加 3 个实体: AgentBase, LangChain, FastAPI")

    # 5. 添加关系
    provider.add_relation(Relation(
        source_entity=e1_id, target_entity=e2_id,
        relation_type="depends_on",
    ))
    provider.add_relation(Relation(
        source_entity=e1_id, target_entity=e3_id,
        relation_type="uses",
    ))
    print("5. 添加 2 个关系: AgentBase depends_on LangChain, AgentBase uses FastAPI")

    # 6. 搜索实体
    results = provider.search_entities("AgentBase")
    print(f"\n6. 搜索 'AgentBase': 找到 {len(results)} 个实体")
    for r in results:
        print(f"   - {r.entity.name} (score={r.score:.1f}, label={r.entity.label})")

    # 7. 获取子图
    subgraph = provider.get_subgraph(e1_id)
    print(f"\n7. AgentBase 的子图: {len(subgraph['entities'])} 个实体, {len(subgraph['relations'])} 个关系")

    # 8. 验证注册表
    print(f"\n8. 最终已注册 Provider: {graph_registry.names()}")

    print("\n" + "=" * 60)
    print("[OK] 示例完成！在 config 中设置 graph.provider: memory 即可使用")
    print("=" * 60)


if __name__ == "__main__":
    main()
