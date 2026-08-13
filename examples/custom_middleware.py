#!/usr/bin/env python
"""Cookbook: 注册自定义 Middleware。

演示如何通过 @register_middleware 装饰器注册一个自定义的
模型调用中间件。

本示例实现一个 latency_injector 中间件：在模型响应中
注入人工延迟，用于测试超时和重试行为。

运行方式:
    python examples/custom_middleware.py
    python examples/custom_middleware.py --help
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.middleware import middleware_registry, register_middleware

_META = ExtensionMeta(
    name="latency_injector",
    kind="middleware",
    description="Inject artificial latency into model calls for testing.",
    default_enabled=False,
)


@register_middleware("latency_injector", meta=_META)
def build_latency_injector(context: dict[str, Any] | None = None):
    """构建延迟注入中间件。

    返回一个 wrap_model_call 函数列表。
    """
    # 从 agent config metadata 读取延迟配置
    delay_ms = 100  # 默认 100ms
    if context and "agent_config" in context:
        agent_cfg = context["agent_config"]
        if hasattr(agent_cfg, "metadata") and agent_cfg.metadata:
            delay_ms = agent_cfg.metadata.get("latency_injector_delay_ms", delay_ms)

    def wrap_model_call(handler):
        """包装模型调用，注入延迟。"""

        def wrapped(request):
            # 调用前延迟
            time.sleep(delay_ms / 1000.0)

            # 执行实际调用
            response = handler(request)

            # 调用后延迟
            time.sleep(delay_ms / 1000.0)

            return response

        return wrapped

    return [wrap_model_call]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="注册并测试自定义 latency_injector 中间件",
    )
    parser.parse_args()

    print("=" * 60)
    print("Cookbook: 自定义 Middleware (latency_injector)")
    print("=" * 60)

    # 1. 注册信息
    print("\n1. 中间件已通过 @register_middleware 装饰器自动注册")
    print(f"   注册名: {_META.name}")
    print(f"   描述: {_META.description}")
    print(f"   默认启用: {_META.default_enabled}")

    # 2. 验证注册
    is_registered = middleware_registry.has("latency_injector")
    print(f"\n2. 验证注册: middleware_registry.has('latency_injector') = {is_registered}")

    # 3. 构建中间件（get 返回 builder 函数，调用它获得中间件列表）
    builder = middleware_registry.get("latency_injector")
    middleware_list = builder(context={})
    print(f"\n3. 构建中间件: 返回 {len(middleware_list)} 个 wrapper 函数")

    # 4. 测试中间件行为
    print("\n4. 测试中间件行为:")

    # 模拟一个 handler
    call_log: list[str] = []

    def mock_handler(request):
        call_log.append(f"handler called with: {request}")
        return {"output": f"processed: {request}"}

    # 包装 handler
    wrapped = middleware_list[0](mock_handler)

    # 调用并测量时间
    start = time.time()
    result = wrapped("test message")
    elapsed = time.time() - start

    print("   输入: 'test message'")
    print(f"   输出: {result}")
    print(f"   耗时: {elapsed:.3f}s (包含 ~0.2s 注入延迟)")
    print(f"   Handler 被调用: {len(call_log)} 次")

    # 5. 查看注册表
    all_mw = middleware_registry.names()
    print(f"\n5. 注册表中的中间件总数: {len(all_mw)}")
    print(f"   包含 latency_injector: {'latency_injector' in all_mw}")

    print("\n" + "=" * 60)
    print("[OK] 示例完成！在 agent 配置中添加 'latency_injector' 到 middleware 列表即可使用")
    print("  可在 agent metadata 中设置 latency_injector_delay_ms 调整延迟（默认 100ms）")
    print("=" * 60)


if __name__ == "__main__":
    main()
