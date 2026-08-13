#!/usr/bin/env python
"""Cookbook: 从 SQLite 切换到 PostgreSQL 存储后端。

演示如何通过修改 YAML 配置切换 agentbase 的存储后端。
不需要改任何代码，纯配置驱动。

本脚本展示两种配置方式的对比，不实际连接数据库。

运行方式:
    python examples/switch_storage.py
    python examples/switch_storage.py --help
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

from agentbase.config.schema import StorageConfig


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Switch storage backend (SQLite to PostgreSQL)",
    )
    parser.parse_args()

    print("=" * 60)
    print("Cookbook: 切换存储后端 (SQLite → PostgreSQL)")
    print("=" * 60)

    # 1. SQLite 配置（开发环境）
    sqlite_config = StorageConfig(
        type="sqlite",
        db_dir="data",
    )
    print("\n1. SQLite 配置（开发环境）:")
    print(f"   type: {sqlite_config.type}")
    print(f"   db_dir: {sqlite_config.db_dir}")
    print(f"   dsn: {sqlite_config.dsn}")
    print(f"   数据库文件: {sqlite_config.db_dir}/memory.db")

    # 2. PostgreSQL 配置（生产环境）
    pg_config = StorageConfig(
        type="postgres",
        dsn="postgresql://agentbase:agentbase@127.0.0.1:5432/agentbase",
    )
    print("\n2. PostgreSQL 配置（生产环境）:")
    print(f"   type: {pg_config.type}")
    print(f"   dsn: {pg_config.dsn}")

    # 3. MySQL 配置（可选）
    mysql_config = StorageConfig(
        type="mysql",
        dsn="mysql://agentbase:agentbase@127.0.0.1:3306/agentbase",
    )
    print("\n3. MySQL 配置（可选）:")
    print(f"   type: {mysql_config.type}")
    print(f"   dsn: {mysql_config.dsn}")

    # 4. YAML 配置示例
    print("\n4. YAML 配置示例:")
    print()
    print("   # SQLite（开发环境 — 零配置）")
    print("   storage:")
    print("     type: sqlite")
    print("     db_dir: data")
    print()
    print("   # PostgreSQL（生产环境）")
    print("   storage:")
    print("     type: postgres")
    print("     dsn: postgresql://user:pass@host:5432/dbname")
    print()
    print("   # MySQL（可选）")
    print("   storage:")
    print("     type: mysql")
    print("     dsn: mysql://user:pass@host:3306/dbname")
    print()

    # 5. 切换步骤
    print("5. 切换步骤:")
    print("   a. 编辑 configs/default.yaml 中的 storage 段")
    print("   b. 将 type 从 sqlite 改为 postgres")
    print("   c. 设置 dsn 为你的 PostgreSQL 连接字符串")
    print("   d. 安装 PostgreSQL 依赖: pip install agentbase[postgres]")
    print("   e. 重启 agentbase 即可，数据会自动使用新后端")
    print("   f. 如需迁移数据: agentbase backup -o backup.json --format json")
    print("      然后切换配置后: agentbase restore backup.json --format json")

    # 6. 健康检查
    print("\n6. 切换后验证:")
    print("   curl http://localhost:8000/health")
    print("   检查 response 中的 storage_connected 字段")

    print("\n" + "=" * 60)
    print("[OK] 示例完成！纯配置切换，无需改代码")
    print("=" * 60)


if __name__ == "__main__":
    main()
