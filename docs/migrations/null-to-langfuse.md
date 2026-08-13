# Null Tracer → Langfuse 迁移指南

将链路追踪从 `NullTracer`（无观测、零开销）切换到 `LangfuseTracer`（可视化 Trace、性能分析）。

## 前置条件

- Langfuse 账号（[langfuse.com](https://langfuse.com) 自托管或云端）
- 获取到 `LANGFUSE_PUBLIC_KEY` 和 `LANGFUSE_SECRET_KEY`

## 步骤 1：安装依赖

```bash
pip install langfuse
```

## 步骤 2：配置环境变量

Langfuse SDK 通过环境变量自动读取密钥：

```bash
# Linux / macOS
export LANGFUSE_PUBLIC_KEY="pk-lf-xxxxxxxx"
export LANGFUSE_SECRET_KEY="sk-lf-xxxxxxxx"
export LANGFUSE_HOST="https://cloud.langfuse.com"  # 或自托管 URL

# Windows PowerShell
$env:LANGFUSE_PUBLIC_KEY = "pk-lf-xxxxxxxx"
$env:LANGFUSE_SECRET_KEY = "sk-lf-xxxxxxxx"
$env:LANGFUSE_HOST = "https://cloud.langfuse.com"
```

> **生产环境**：将环境变量写入 `.env` 文件或 K8s Secret 中，不要硬编码。

## 步骤 3：修改配置

编辑 `configs/default.yaml`，添加或修改 `tracer` 段：

```yaml
tracer:
  provider: langfuse
  options: {}
```

`options` 中的键值对会作为 `**kwargs` 传递给 `Langfuse(**kwargs)` 构造函数。如果环境变量已配置，`options` 留空即可。

如果需要在配置中直接指定（不使用环境变量）：

```yaml
tracer:
  provider: langfuse
  options:
    public_key: "pk-lf-xxxxxxxx"
    secret_key: "sk-lf-xxxxxxxx"
    host: "https://cloud.langfuse.com"
```

## 步骤 4：验证

### 方式一：API 调用

```bash
# 启动服务
agentbase serve

# 调用 Agent（会触发 Trace）
curl -X POST http://localhost:8000/agents/default/invoke \
  -H "Content-Type: application/json" \
  -d '{"message": "hello"}'
```

### 方式二：检查 Langfuse Dashboard

1. 登录 Langfuse Dashboard
2. 进入 **Traces** 页面
3. 应该能看到名为 `agent.invoke` 的 Trace
4. 展开可以看到子 Span：模型调用、工具执行等

### 方式三：健康检查

```bash
curl http://localhost:8000/health
```

确认 `components` 中 `tracer` 状态为 `healthy`。

> **注意**：`health_check.check_tracer` 默认为 `false`。如需在健康检查中包含 Tracer 探活，在配置中启用：
>
> ```yaml
> health_check:
>   check_tracer: true
> ```

## 工作原理

### Trace 结构

AgentBase 的 `LangfuseTracer` 在 Agent 调用过程中自动记录以下 Span：

| Span 名称 | 触发时机 | 属性 |
|-----------|---------|------|
| `agent.invoke` | Agent 同步调用开始 | `agent_name`, `message` |
| `agent.stream` | Agent 流式调用开始 | `agent_name`, `message` |
| `model.call` | LLM 模型调用 | `model`, `temperature` |
| `tool.execute` | 工具执行 | `tool_name`, `args` |

### 懒加载

`LangfuseTracer` 采用懒加载策略：
- 配置 `provider: langfuse` 后，Tracer 对象立即创建（注册到 `tracer_registry`）
- Langfuse 客户端连接在第一次 `start_trace()` 调用时才建立
- 如果 `langfuse` 包未安装，第一次调用时抛 `ImportError`

### 零开销降级

如果 Langfuse 服务不可达：
- Trace 数据会被 Langfuse SDK 内部缓存并重试发送
- 不会阻塞 Agent 调用主流程
- 不会导致 Agent 调用失败

## 注册机制

`LangfuseTracer` 在模块加载时自动注册到 `tracer_registry`：

```python
# src/agentbase/core/tracer.py
try:
    import langfuse  # noqa: F401
    tracer_registry.register("langfuse", LangfuseTracer, override=True)
except ImportError:
    pass  # 未安装 langfuse 包时跳过注册
```

如果 `langfuse` 包未安装，`tracer.provider: langfuse` 配置会在运行时抛出 `ImportError`。

## 涉及的组件

| 组件 | 配置路径 | 默认 (Null) | 迁移后 (Langfuse) |
|------|---------|------------|------------------|
| 链路追踪 | `tracer` | 无操作 | Langfuse Dashboard |
| Agent 调用 | — | 无 Trace | 自动记录 Trace |
| 工具执行 | — | 无 Trace | 自动记录子 Span |
| `/health` | `health_check.check_tracer` | 默认不检查 | 可启用检查 |

## 回滚

将 `configs/default.yaml` 中 `tracer.provider` 改回 `null` 即可：

```yaml
tracer:
  provider: null
  options: {}
```

或直接删除 `tracer` 配置段（默认值即为 `null`）。

回滚后 Agent 调用不会产生任何 Trace，零开销。

## 常见问题

### `ImportError: Langfuse tracing requires the langfuse package`

未安装 `langfuse` 包。执行 `pip install langfuse`。

### Langfuse Dashboard 中看不到 Trace

1. 检查环境变量是否正确：`echo $LANGFUSE_PUBLIC_KEY`
2. 检查 `LANGFUSE_HOST` 是否指向正确的 Langfuse 实例
3. 确认 Agent 调用确实发生了（查看 `/metrics` 中的 `agentbase_agent_invocations_total`）
4. Langfuse SDK 有异步发送延迟，等待 5-10 秒后刷新

### 自托管 Langfuse

将 `LANGFUSE_HOST` 指向你的 Langfuse 实例 URL：

```bash
export LANGFUSE_HOST="https://langfuse.your-domain.com"
```

确保网络可达，且 API Key 有权限。

### 性能影响

Langfuse SDK 使用异步批量发送，对 Agent 调用的性能影响极小（< 1ms / Span）。在生产环境中可以安全启用。
