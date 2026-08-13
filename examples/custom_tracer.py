#!/usr/bin/env python
"""Cookbook: 注册自定义 Tracer Provider。

演示如何通过 @register_tracer_provider 装饰器注册一个自定义的
追踪 Provider，替换默认的 NullTracer。

本示例实现一个 SimpleTracer：将 trace 事件输出到 stderr，
不依赖外部服务（如 Langfuse / OpenTelemetry）。

运行方式:
    python examples/custom_tracer.py
    python examples/custom_tracer.py --help
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

from agentbase.core.tracer import tracer_registry, NullTracer


class SimpleTracer(NullTracer):
    """简单的 Tracer，将事件输出到 stderr。

    继承 NullTracer 以获得所有 no-op 方法的默认实现，
    只覆盖关键方法来添加输出逻辑。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._span_count = 0

    def start_span(
        self,
        name: str,
        *,
        agent: str | None = None,
        kind: str = "internal",
        attributes: dict[str, Any] | None = None,
    ) -> Any:
        """开始一个 span，返回 span context。"""
        with self._lock:
            self._span_count += 1
            span_id = self._span_count

        attrs_str = ", ".join(f"{k}={v}" for k, v in (attributes or {}).items())
        print(
            f"[TRACE] → #{span_id} {name} (agent={agent}, kind={kind}"
            + (f", {attrs_str}" if attrs_str else "")
            + ")",
            file=sys.stderr,
        )
        return {"id": span_id, "name": name, "start_time": time.time()}

    def end_span(self, span: Any, *, status: str = "ok", attributes: dict[str, Any] | None = None) -> None:
        """结束一个 span。"""
        duration = time.time() - span.get("start_time", time.time()) if isinstance(span, dict) else 0
        print(
            f"[TRACE] ← #{span.get('id', '?')} {span.get('name', '?')} "
            f"status={status} duration={duration:.3f}s",
            file=sys.stderr,
        )

    def add_event(self, span: Any, name: str, *, attributes: dict[str, Any] | None = None) -> None:
        """在 span 中添加一个事件。"""
        attrs_str = ", ".join(f"{k}={v}" for k, v in (attributes or {}).items())
        print(
            f"[TRACE]   #{span.get('id', '?')} event: {name}"
            + (f" ({attrs_str})" if attrs_str else ""),
            file=sys.stderr,
        )

    def flush(self) -> None:
        """刷新缓冲区。"""
        print(f"[TRACE] Flushed {self._span_count} spans", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="注册并测试自定义 Simple Tracer Provider",
    )
    parser.parse_args()

    print("=" * 60)
    print("Cookbook: 自定义 Tracer Provider")
    print("=" * 60)

    # 1. 注册前
    print("\n1. 注册前已注册 Provider:", tracer_registry.names())

    # 2. 注册（直接注册 factory 类）
    tracer_registry.register("simple", SimpleTracer, override=True)
    print("2. 已注册 'simple' Provider")

    # 3. 获取 Provider
    tracer = tracer_registry.create("simple")
    print(f"3. 获取 Provider: {tracer.__class__.__name__}")

    # 4. 模拟 Agent 调用追踪
    print("\n4. 模拟 Agent 调用追踪（输出到 stderr）:")

    # 启动根 span
    root_span = tracer.start_span(
        "agent.invoke",
        agent="default",
        kind="agent",
        attributes={"message": "What is agentbase?"},
    )

    # 添加子 span：模型调用
    model_span = tracer.start_span(
        "model.call",
        agent="default",
        kind="model",
        attributes={"provider": "openai", "model": "deepseek-chat"},
    )
    tracer.add_event(model_span, "token.generated", attributes={"count": 42})
    tracer.end_span(model_span, status="ok", attributes={"tokens": 42})

    # 添加子 span：工具调用
    tool_span = tracer.start_span(
        "tool.call",
        agent="default",
        kind="tool",
        attributes={"tool": "web_search"},
    )
    tracer.end_span(tool_span, status="ok", attributes={"results": 3})

    # 结束根 span
    tracer.end_span(root_span, status="ok", attributes={"output_length": 150})

    # 5. 刷新
    tracer.flush()

    # 6. 验证注册表
    print(f"\n5. 最终已注册 Provider: {tracer_registry.names()}")

    print("\n" + "=" * 60)
    print("[OK] 示例完成！在 config 中设置 tracer.provider: simple 即可使用")
    print("=" * 60)


if __name__ == "__main__":
    main()
