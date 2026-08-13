#!/usr/bin/env python
"""Cookbook: 注册自定义 MCP Client。

演示如何通过 @register_mcp_client 装饰器注册一个自定义的
MCP (Model Context Protocol) 客户端。

本示例实现一个 MockMCPClient：返回预设的工具列表，
不连接真实 MCP 服务器。

运行方式:
    python examples/custom_mcp.py
    python examples/custom_mcp.py --help
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

from agentbase.core.mcp import mcp_registry, register_mcp_client


class MockMCPClient:
    """模拟 MCP 客户端。

    返回预设的工具定义，不连接真实服务器。
    适合开发和测试环境。
    """

    TOOLS: list[dict[str, Any]] = [
        {
            "name": "calculator",
            "description": "A simple calculator tool that can add, subtract, multiply, and divide.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": ["add", "sub", "mul", "div"]},
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["operation", "a", "b"],
            },
        },
        {
            "name": "translator",
            "description": "Translate text between languages.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "from_lang": {"type": "string"},
                    "to_lang": {"type": "string"},
                },
                "required": ["text", "to_lang"],
            },
        },
    ]

    def list_tools(self) -> list[dict[str, Any]]:
        """返回可用工具列表。"""
        return self.TOOLS.copy()

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """调用指定工具。"""
        if name == "calculator":
            op = arguments.get("operation")
            a = arguments.get("a", 0)
            b = arguments.get("b", 0)
            if op == "add":
                return {"result": a + b}
            elif op == "sub":
                return {"result": a - b}
            elif op == "mul":
                return {"result": a * b}
            elif op == "div":
                if b == 0:
                    return {"error": "Division by zero"}
                return {"result": a / b}
            return {"error": f"Unknown operation: {op}"}
        elif name == "translator":
            return {"translated": f"[{arguments.get('to_lang', '??')}] {arguments.get('text', '')}"}
        return {"error": f"Unknown tool: {name}"}

    def close(self) -> None:
        """关闭连接。"""
        pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="注册并测试自定义 Mock MCP Client",
    )
    parser.parse_args()

    print("=" * 60)
    print("Cookbook: 自定义 MCP Client")
    print("=" * 60)

    # 1. 注册前
    print("\n1. 注册前已注册 MCP Client:", mcp_registry.names())

    # 2. 注册
    register_mcp_client("mock", description="Mock MCP client for testing")(MockMCPClient)
    print("2. 已注册 'mock' MCP Client")

    # 3. 创建实例
    client = mcp_registry.create("mock")
    print(f"3. 创建实例: {client.__class__.__name__}")

    # 4. 列出工具
    tools = client.list_tools()
    print(f"\n4. 可用工具 ({len(tools)} 个):")
    for t in tools:
        print(f"   - {t['name']}: {t['description'][:50]}...")

    # 5. 调用 calculator 工具
    result = client.call_tool("calculator", {"operation": "add", "a": 10, "b": 20})
    print(f"\n5. 调用 calculator(add, 10, 20): {result}")

    result = client.call_tool("calculator", {"operation": "mul", "a": 5, "b": 6})
    print(f"   调用 calculator(mul, 5, 6): {result}")

    # 6. 调用 translator 工具
    result = client.call_tool("translator", {"text": "Hello", "to_lang": "zh"})
    print(f"   调用 translator(Hello, zh): {result}")

    # 7. 验证注册表
    print(f"\n6. 最终已注册 MCP Client: {mcp_registry.names()}")

    print("\n" + "=" * 60)
    print("[OK] 示例完成！在 config 中设置 mcp.provider: mock 即可使用")
    print("=" * 60)


if __name__ == "__main__":
    main()
