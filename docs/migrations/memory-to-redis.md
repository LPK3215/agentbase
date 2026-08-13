# Memory Queue → Redis Queue 迁移指南

将异步任务队列从 `MemoryRequestQueue`（单进程、非持久化）迁移到 `RedisRequestQueue`（多进程、持久化、 survives 重启）。

## 前置条件

- Redis 6.0+ 已安装并运行
- 有连接 Redis 的网络权限

## 步骤 1：安装依赖

```bash
pip install redis
```

## 步骤 2：启动 Redis

```bash
# 本地启动（开发用）
redis-server

# 或使用 Docker
docker run -d --name redis -p 6379:6379 redis:7-alpine
```

验证连接：

```bash
redis-cli ping
# 应返回 PONG
```

## 步骤 3：修改配置

编辑 `configs/default.yaml`，添加或修改 `queue` 段：

```yaml
queue:
  provider: redis
  options:
    host: localhost
    port: 6379
    db: 0
    # password: your-password   # 如果 Redis 设置了密码
    # url: redis://:password@host:port/db  # 或者用 URL 方式连接
```

> **Docker 部署**：将 `host` 改为 `redis`（容器服务名）。
>
> ```yaml
> queue:
>   provider: redis
>   options:
>     host: redis
>     port: 6379
>     db: 0
> ```

## 步骤 4：验证

### 方式一：API 调用

```bash
# 启动服务
agentbase serve

# 提交一个异步任务
curl -X POST http://localhost:8000/queue/submit \
  -H "Content-Type: application/json" \
  -d '{"agent_name": "default", "message": "hello"}'

# 查看任务状态（用返回的 task_id）
curl http://localhost:8000/queue/{task_id}
```

### 方式二：检查 Redis

```bash
redis-cli

# 查看队列中的任务
KEYS agentbase:task:*

# 查看待处理任务列表
LRANGE agentbase:tasks:pending 0 -1
```

## 工作原理

### 数据结构

| Redis Key | 类型 | 说明 |
|-----------|------|------|
| `agentbase:task:{id}` | String (JSON) | 单个任务的完整序列化数据 |
| `agentbase:tasks:index` | Set | 所有任务 ID 的索引（用于列表查询） |
| `agentbase:tasks:pending` | List | 待处理任务 ID 的队列（LPUSH 取最新） |

### 多 Worker 支持

Redis Queue 支持多个进程同时消费同一个队列：

```bash
# 终端 1：启动第一个 Worker
agentbase worker

# 终端 2：启动第二个 Worker
agentbase worker
```

两个 Worker 会自动从 Redis 队列中竞争获取任务（原子操作，无重复执行）。

### 持久化

Redis Queue 的数据在 Redis 持久化的前提下可以 survives 进程重启：

```bash
# 在 redis.conf 中启用 AOF 持久化
appendonly yes
appendfsync everysec
```

## 从 Memory Queue 迁移

> Memory Queue 中的任务存储在进程内存中，无法导出。迁移前请确保所有重要任务已完成。

1. 等待所有 `pending` / `running` 状态的任务完成
2. 修改配置为 `queue.provider: redis`
3. 重启服务

迁移后，新提交的任务将进入 Redis 队列。

## 涉及的组件

| 组件 | 配置路径 | 默认 (Memory) | 迁移后 (Redis) |
|------|---------|--------------|---------------|
| 异步任务队列 | `queue` | 进程内存 | Redis |
| `/queue/submit` API | — | 内存中 | Redis 中 |
| `/queue/{id}` API | — | 内存查询 | Redis 查询 |
| `agentbase worker` | — | 内存消费 | Redis 消费 |

## 回滚

将 `configs/default.yaml` 中 `queue.provider` 改回 `none` 或删除 `queue` 段即可。

> Redis 中的任务数据不会自动清理。如需清理：
> ```bash
> redis-cli DEL agentbase:tasks:pending agentbase:tasks:index
> redis-cli --eval <(echo "for _,k in ipairs(redis.call('KEYS','agentbase:task:*')) do redis.call('DEL',k) end") , 0
> ```

## 常见问题

### `ImportError: Redis queue requires the redis package`

未安装 `redis` 包。执行 `pip install redis`。

### `ConnectionError: Error 111 connecting to localhost:6379`

Redis 服务未启动。检查：
- Redis 进程：`redis-cli ping`
- 端口是否被占用：`lsof -i :6379` 或 `netstat -an | findstr 6379`

### 任务提交成功但 Worker 消费不到

检查 Worker 是否使用了相同的 Redis 配置。Worker 启动时会读取 `configs/default.yaml`，确保配置一致。

### 密码认证失败

如果 Redis 设置了 `requirepass`，在配置中添加 `password` 字段或使用 `url` 方式：

```yaml
queue:
  provider: redis
  options:
    url: redis://:your-password@localhost:6379/0
```
