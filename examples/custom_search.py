#!/usr/bin/env python
"""Cookbook: 注册自定义 Search Provider。

演示如何通过 @register_search_provider 装饰器注册一个自定义的
Web 搜索 Provider，替换默认的 DuckDuckGo。

本示例实现一个 MockSearch：返回预设的搜索结果，不发起真实网络请求。

运行方式:
    python examples/custom_search.py
    python examples/custom_search.py --help
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

from agentbase.core.search import SearchResult, search_registry


class MockSearch:
    """模拟搜索引擎，返回预设结果。

    适合开发和测试环境，不依赖外部 API。
    """

    MOCK_RESULTS: list[dict[str, str]] = [
        {
            "title": "AgentBase Documentation",
            "url": "https://github.com/example/agentbase",
            "snippet": "AgentBase is an AI Agent backend scaffold with 37 tools, 9 middleware, and 25 pluggable registries.",
            "source": "mock",
        },
        {
            "title": "Getting Started with AgentBase",
            "url": "https://github.com/example/agentbase/blob/main/docs/quickstart.md",
            "snippet": "Quick start guide: clone, configure, and run your first AI agent in 5 minutes.",
            "source": "mock",
        },
        {
            "title": "LangChain Integration Guide",
            "url": "https://github.com/example/agentbase/blob/main/docs/extensions.md",
            "snippet": "Learn how to extend AgentBase with custom tools, middleware, and parsers using decorators.",
            "source": "mock",
        },
    ]

    def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        results: list[SearchResult] = []
        for item in self.MOCK_RESULTS[:max_results]:
            # 简单的关键词匹配：如果查询词出现在 title 或 snippet 中，提升排名
            score = 0
            query_lower = query.lower()
            if query_lower in item["title"].lower():
                score += 2
            if query_lower in item["snippet"].lower():
                score += 1
            results.append(SearchResult(
                title=item["title"],
                url=item["url"],
                snippet=item["snippet"],
                source=item["source"],
            ))
        return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="注册并测试自定义 Mock Search Provider",
    )
    parser.parse_args()

    print("=" * 60)
    print("Cookbook: 自定义 Search Provider")
    print("=" * 60)

    # 1. 注册前
    print("\n1. 注册前已注册 Provider:", search_registry.names())

    # 2. 注册（手动注册实例）
    mock_provider = MockSearch()
    search_registry.register("mock", mock_provider, override=True)
    print("2. 已注册 'mock' Provider")

    # 3. 获取并测试
    provider = search_registry.get("mock")
    print(f"3. 获取 Provider: {provider.__class__.__name__}")

    # 4. 执行搜索
    query = "AgentBase"
    results = provider.search(query, max_results=3)
    print(f"\n4. 搜索 '{query}' 返回 {len(results)} 条结果:")
    for i, r in enumerate(results, 1):
        print(f"   [{i}] {r.title}")
        print(f"       URL: {r.url}")
        print(f"       摘要: {r.snippet[:60]}...")

    # 5. 验证注册表
    print(f"\n5. 最终已注册 Provider: {search_registry.names()}")

    print("\n" + "=" * 60)
    print("[OK] 示例完成！在 config 中设置 web_search.provider: mock 即可使用")
    print("=" * 60)


if __name__ == "__main__":
    main()
