#!/usr/bin/env python
"""Cookbook: 注册自定义 Tool。

演示如何通过 @register_tool 装饰器注册一个自定义的
LangChain Tool，供 Agent 调用。

本示例实现一个 word_count 工具：统计文本的字数。

运行方式:
    python examples/custom_tool.py
    python examples/custom_tool.py --help
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.tools import register_tool, tool_registry

_META = ExtensionMeta(
    name="word_count",
    kind="tool",
    description="Count words in a text string.",
    default_enabled=False,
)


@register_tool("word_count", meta=_META)
def build_word_count_tool(context: dict[str, Any] | None = None):
    """构建 word_count 工具。

    返回一个 LangChain @tool 装饰的函数。
    """
    from langchain_core.tools import tool

    @tool
    def word_count(text: str) -> str:
        """Count the number of words in the given text.

        Args:
            text: The text to count words in.

        Returns:
            A string describing the word count.
        """
        words = text.split()
        return f"The text contains {len(words)} word(s)."

    return word_count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="注册并测试自定义 word_count 工具",
    )
    parser.parse_args()

    print("=" * 60)
    print("Cookbook: 自定义 Tool (word_count)")
    print("=" * 60)

    # 1. 注册前
    # 注意：@register_tool 在模块导入时已自动注册
    print("\n1. 工具已通过 @register_tool 装饰器自动注册")
    print("   注册名: word_count")
    print(f"   元数据: name={_META.name}, kind={_META.kind}, default_enabled={_META.default_enabled}")

    # 2. 验证注册
    is_registered = tool_registry.has("word_count")
    print(f"\n2. 验证注册: tool_registry.has('word_count') = {is_registered}")

    # 3. 构建工具（get 返回 builder 函数，调用它获得实际工具实例）
    builder = tool_registry.get("word_count")
    tool_func = builder(context={})
    print(f"\n3. 构建工具: {tool_func.name}")
    print(f"   描述: {tool_func.description}")

    # 4. 调用工具
    test_texts = [
        "Hello world",
        "The quick brown fox jumps over the lazy dog",
        "AgentBase is an AI Agent backend scaffold",
    ]

    print("\n4. 测试调用:")
    for text in test_texts:
        result = tool_func.invoke({"text": text})
        print(f"   输入: '{text}'")
        print(f"   输出: {result}")
        print()

    # 5. 查看注册表中的工具总数
    all_tools = tool_registry.names()
    print(f"5. 注册表中的工具总数: {len(all_tools)}")
    print(f"   包含 word_count: {'word_count' in all_tools}")

    print("\n" + "=" * 60)
    print("[OK] 示例完成！在 agent 配置中添加 'word_count' 到 tools 列表即可使用")
    print("=" * 60)


if __name__ == "__main__":
    main()
