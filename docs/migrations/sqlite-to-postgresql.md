# SQLite → PostgreSQL 迁移指南

将 Storage / Memory / KnowledgeBase / Checkpointer / Audit 从 SQLite（零配置、单机）迁移到 PostgreSQL（多用户、高并发）。

## 前置条件

- PostgreSQL 14+ 已安装并运行
- 有创建数据库的权限

## 步骤 1：安装依赖

```bash
pip install psycopg
```

> 如果使用 `agentbase[postgres]` extras 安装，则已包含。

## 步骤 2：创建数据库

```bash
# 连接 PostgreSQL
psql -U postgres

# 创建数据库和用户
CREATE DATABASE agentbase;
CREATE USER agentbase WITH PASSWORD 'agentbase';
GRANT ALL PRIVILEGES ON DATABASE agentbase TO agentbase;

# 退出
\q
```

验证连接：

```bash
psql -U agentbase -d agentbase -h 127.0.0.1 -W
```

## 步骤 3：备份现有数据（如果有的话）

> 如果是全新部署，跳过此步。

确保 `configs/default.yaml` 中 `storage.type` 仍为 `sqlite`，然后执行：

```bash
# JSON 格式备份（跨数据库兼容，推荐）
agentbase backup -o backup.json --format json
```

这会导出 `data/memory.db` 中的所有表数据为 JSON 文件。

## 步骤 4：修改配置

编辑 `configs/default.yaml`：

```yaml
# 存储层 — 影响 Memory / KnowledgeBase / Audit
storage:
  type: postgres
  db_dir: data          # 保留，SQLite 回滚时用
  dsn: postgresql://agentbase:agentbase@127.0.0.1:5432/agentbase

# 会话检查点
checkpointer:
  type: postgres
  dsn: postgresql://agentbase:agentbase@127.0.0.1:5432/agentbase
  options: {}

# 审计日志（如果启用了）
audit:
  enabled: true
  provider: sqlite       # 保留为 sqlite 也可，或切换到 postgres
  db_dir: data
  dsn: postgresql://agentbase:agentbase@127.0.0.1:5432/agentbase
```

> **Docker 部署**：将 `127.0.0.1` 改为 `postgres`（容器服务名）。
>
> ```yaml
> dsn: postgresql://agentbase:agentbase@postgres:5432/agentbase
> ```

## 步骤 5：恢复数据到新数据库

```bash
# 恢复 JSON 备份到 PostgreSQL
agentbase restore backup.json --format json
```

> `restore` 命令会自动在目标数据库中创建表并插入数据。
> SQL 方言转换是自动的（`AUTOINCREMENT` → `SERIAL`，`?` → `%s`）。

## 步骤 6：验证

### 方式一：命令行

```bash
# 检查健康状态
agentbase doctor
```

### 方式二：API

```bash
# 启动服务
agentbase serve

# 另一个终端检查健康
curl http://localhost:8000/health
```

确认返回的 `components` 中 `storage` 状态为 `healthy`。

### 方式三：直接查询

```bash
psql -U agentbase -d agentbase -h 127.0.0.1 -W
\dt
# 应该看到 memories, documents, chunks, audit_events 等表
```

## 自动 SQL 方言转换

AgentBase 的 `StorageBackend` 抽象层会自动处理 SQL 方言差异：

| SQLite 语法 | PostgreSQL 语法 | 转换时机 |
|-------------|-----------------|---------|
| `INTEGER PRIMARY KEY AUTOINCREMENT` | `SERIAL PRIMARY KEY` | `executescript()` 时自动转换 |
| `?` 占位符 | `%s` 占位符 | 上层代码统一使用 `%s`，无需感知 |

因此，所有 `CREATE TABLE` 语句和 `INSERT` / `SELECT` 语句在不同后端下都能正确执行。

## 涉及的组件

| 组件 | 配置路径 | 默认 (SQLite) | 迁移后 (PostgreSQL) |
|------|---------|--------------|-------------------|
| 长期记忆 | `storage` | `data/memory.db` | PostgreSQL `memories` 表 |
| 知识库 | `storage` | `data/knowledge.db` | PostgreSQL `documents` / `chunks` 表 |
| 审计日志 | `audit` | `data/audit.db` | PostgreSQL `audit_events` 表 |
| 会话检查点 | `checkpointer` | `data/checkpoints.db` | PostgreSQL `checkpoints` 表 |

> **注意**：Memory、KnowledgeBase 和 Audit 共享同一个 `storage` 配置。
> Checkpointer 有独立的 `checkpointer` 配置段，但通常指向同一个数据库。

## 回滚

如果需要回退到 SQLite：

1. 将 `configs/default.yaml` 中 `storage.type` 改回 `sqlite`
2. 将 `checkpointer.type` 改回 `sqlite`
3. 重启服务

PostgreSQL 中的数据不会丢失，但 SQLite 会从空表开始（除非你从 PostgreSQL 备份后恢复）。

## 常见问题

### `ImportError: PostgreSQL backend requires psycopg`

未安装 `psycopg` 包。执行 `pip install psycopg`。

### `connection refused`

PostgreSQL 服务未启动，或 `dsn` 中的 host/port 不正确。检查：
- PostgreSQL 服务状态：`pg_isready -h 127.0.0.1 -p 5432`
- 防火墙是否放行 5432 端口

### `authentication failed`

用户名或密码错误。确认 `pg_hba.conf` 允许密码认证，并检查 `dsn` 中的凭据。
