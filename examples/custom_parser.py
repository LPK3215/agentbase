#!/usr/bin/env python
"""Cookbook: 注册自定义文档解析器。

演示如何通过 @register_parser 装饰器注册一个自定义的
文档解析器，处理 .log 文件格式。

运行方式:
    python examples/custom_parser.py
    python examples/custom_parser.py --help
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

from agentbase.core.parsers import TextParser, parser_registry, register_parser


class LogParser:
    """解析 .log 文件为结构化 Markdown。

    将日志行转换为 Markdown 表格格式：
    | Timestamp | Level | Message |
    |-----------|-------|---------|
    | 2024-01-01 12:00:00 | INFO | Starting server... |
    """

    def __init__(self) -> None:
        self._text_parser = TextParser()

    def parse(self, file_path: str | Path) -> str:
        """解析 .log 文件为 Markdown 表格。"""
        content = self._text_parser.parse(file_path)
        lines = content.strip().splitlines()

        # 检测是否是标准日志格式（时间戳 + 级别 + 消息）
        log_pattern = re.compile(
            r"^(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+"
            r"(INFO|WARN|ERROR|DEBUG|TRACE)\s+(.*)$"
        )

        matched_lines = []
        unmatched_lines = []
        for line in lines:
            m = log_pattern.match(line)
            if m:
                matched_lines.append((m.group(1), m.group(2), m.group(3)))
            else:
                unmatched_lines.append(line)

        if not matched_lines:
            # 不是标准日志格式，直接返回原文
            return content

        # 构建 Markdown 表格
        parts = ["| Timestamp | Level | Message |", "|-----------|-------|---------|"]
        for ts, level, msg in matched_lines:
            parts.append(f"| {ts} | {level} | {msg} |")

        if unmatched_lines:
            parts.append("")
            parts.append("## Unmatched Lines")
            for line in unmatched_lines:
                parts.append(f"```\n{line}\n```")

        return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="注册并测试自定义 .log 文件解析器",
    )
    parser.parse_args()

    print("=" * 60)
    print("Cookbook: 自定义文档解析器 (.log)")
    print("=" * 60)

    # 1. 注册前
    print("\n1. 注册前已注册解析器数量:", parser_registry.count)
    print("   支持的扩展名:", parser_registry.supported_extensions())

    # 2. 注册
    register_parser(".log", override=True)(LogParser)
    print("\n2. 已注册 '.log' 解析器: LogParser")

    # 3. 创建测试日志文件
    log_content = """2024-01-15 10:30:00 INFO Starting agentbase server on port 8000
2024-01-15 10:30:01 INFO Loaded 2 agent configurations
2024-01-15 10:30:05 WARN Rate limit threshold reached for IP 127.0.0.1
2024-01-15 10:30:10 ERROR Failed to connect to PostgreSQL: connection refused
2024-01-15 10:30:15 INFO Retry attempt 1/3
2024-01-15 10:30:16 INFO PostgreSQL connection established"""

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".log", delete=False, encoding="utf-8"
    ) as f:
        f.write(log_content)
        temp_path = f.name

    print(f"\n3. 创建测试日志文件: {temp_path}")

    # 4. 解析
    log_parser = LogParser()
    result = log_parser.parse(Path(temp_path))
    print("\n4. 解析结果（Markdown 表格）:")
    print("-" * 60)
    print(result)
    print("-" * 60)

    # 5. 清理
    Path(temp_path).unlink(missing_ok=True)

    print("\n5. 清理临时文件完成")

    print("\n" + "=" * 60)
    print("[OK] 示例完成！.log 文件现在可以通过 parser_registry 自动解析")
    print("=" * 60)


if __name__ == "__main__":
    main()
