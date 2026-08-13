# 迁移指南索引

> AgentBase 各存储 / 观测组件从默认（零依赖）实现切换到生产级实现的逐步指南。

所有迁移都遵循同一个原则：**改配置 → 装依赖 → 验证**。无需改代码。

## 可用迁移

| 迁移路径 | 涉及组件 | 依赖 | 适用场景 |
|----------|---------|------|---------|
| [SQLite → PostgreSQL](./sqlite-to-postgresql.md) | Storage / Memory / KB / Checkpointer / Audit | `psycopg` | 单机 → 多用户 / 高并发 |
| [Memory Queue → Redis Queue](./memory-to-redis.md) | 异步任务队列 | `redis` | 单进程 → 多进程 / 持久化 |
| [Null Tracer → Langfuse](./null-to-langfuse.md) | 链路追踪 | `langfuse` | 无观测 → 可视化 Trace |

## 通用步骤

1. **安装依赖** — 每篇指南开头标注了 `pip install` 命令
2. **修改 `configs/default.yaml`** — 改对应配置段的 `type` / `provider` / `dsn`
3. **重启服务** — `agentbase serve` 或 `uvicorn agentbase.api:app`
4. **验证** — 访问 `/health` 确认组件状态为 `healthy`

## 回滚

如果迁移后出现问题，只需将配置改回原值并重启即可。数据不会丢失——旧数据库文件仍在 `data/` 目录中。

## 数据备份

迁移前建议先备份：

```bash
# JSON 格式（跨数据库兼容，推荐迁移用）
agentbase backup -o backup.json --format json

# SQL 格式（同类型数据库恢复用）
agentbase backup -o backup.sql --format sql
```

恢复到新数据库：

```bash
agentbase restore backup.json --format json
```

> **注意**：`backup` / `restore` 命令操作的是 `storage` 配置指定的数据库。
> 迁移时先用旧配置备份，再改配置后恢复。
