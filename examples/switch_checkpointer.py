#!/usr/bin/env python
"""Cookbook: 从 Memory 切换到 PostgreSQL 检查点后端。

演示如何通过修改 YAML 配置切换 agentbase 的 Agent 会话检查点后端。
检查点用于持久化 Agent 对话状态，支持中断恢复。

运行方式:
    python examples/switch_checkpointer.py
    python examples/switch_checkpointer.py --help
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

from agentbase.config.schema import CheckpointerConfig


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Switch checkpointer backend (Memory to SQLite to PostgreSQL)",
    )
    parser.parse_args()

    print("=" * 60)
    print("Cookbook: 切换检查点后端 (Memory → PostgreSQL)")
    print("=" * 60)

    # 1. Memory 配置（默认，开发环境）
    memory_config = CheckpointerConfig(type="memory")
    print("\n1. Memory 配置（开发环境 — 进程重启后丢失）:")
    print(f"   type: {memory_config.type}")
    print("   说明: Agent 对话状态存储在内存中，重启后丢失")

    # 2. SQLite 配置（单机持久化）
    sqlite_config = CheckpointerConfig(type="sqlite")
    print("\n2. SQLite 配置（单机持久化 — 进程重启后保留）:")
    print(f"   type: {sqlite_config.type}")
    print("   说明: Agent 对话状态持久化到 SQLite 文件")

    # 3. PostgreSQL 配置（多进程共享）
    pg_config = CheckpointerConfig(
        type="postgres",
        dsn="postgresql://agentbase:agentbase@127.0.0.1:5432/agentbase",
    )
    print("\n3. PostgreSQL 配置（生产环境 — 多进程共享）:")
    print(f"   type: {pg_config.type}")
    print(f"   dsn: {pg_config.dsn}")
    print("   说明: Agent 对话状态持久化到 PostgreSQL，支持多实例共享")

    # 4. MySQL 配置（可选）
    mysql_config = CheckpointerConfig(
        type="mysql",
        dsn="mysql://agentbase:agentbase@127.0.0.1:3306/agentbase",
    )
    print("\n4. MySQL 配置（可选）:")
    print(f"   type: {mysql_config.type}")
    print(f"   dsn: {mysql_config.dsn}")

    # 5. YAML 配置示例
    print("\n5. YAML 配置示例:")
    print()
    print("   # Memory（开发环境 — 默认）")
    print("   checkpointer:")
    print("     type: memory")
    print()
    print("   # SQLite（单机持久化）")
    print("   checkpointer:")
    print("     type: sqlite")
    print()
    print("   # PostgreSQL（生产环境）")
    print("   checkpointer:")
    print("     type: postgres")
    print("     dsn: postgresql://user:pass@host:5432/dbname")
    print()

    # 6. 切换步骤
    print("6. 切换步骤:")
    print("   a. 编辑 configs/default.yaml 中的 checkpointer 段")
    print("   b. 将 type 从 memory 改为 postgres（或 sqlite）")
    print("   c. 设置 dsn 为你的数据库连接字符串")
    print("   d. 安装对应依赖:")
    print("      PostgreSQL: pip install agentbase[postgres]")
    print("      MySQL:      pip install agentbase[mysql]")
    print("   e. 重启 agentbase，新的对话将使用新的检查点后端")

    # 7. 中断恢复示例
    print("\n7. 中断恢复流程（依赖检查点）:")
    print("   # Agent 调用时自动保存检查点")
    print("   curl -X POST http://localhost:8000/agents/default/invoke \\")
    print("     -H 'Content-Type: application/json' \\")
    print("     -d '{\"message\": \"hello\"}'")
    print()
    print("   # 使用 thread_id 恢复对话")
    print("   curl -X POST http://localhost:8000/agents/default/resume \\")
    print("     -H 'Content-Type: application/json' \\")
    print("     -d '{\"thread_id\": \"<from-invoke>\", \"decision\": \"approve\"}'")
    print()
    print("   # CLI 方式")
    print("   agentbase resume --thread-id <id> --decision approve")

    print("\n" + "=" * 60)
    print("[OK] 示例完成！纯配置切换，无需改代码")
    print("=" * 60)


if __name__ == "__main__":
    main()
