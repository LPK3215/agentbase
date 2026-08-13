# AgentBase Cookbook — 示例库

> 每个示例是一个独立可运行的 Python 脚本，展示如何使用 agentbase 的某个注册表或扩展机制。

## 运行方式

```bash
# 从项目根目录运行
python examples/custom_embedding.py
python examples/custom_embedding.py --help

# 或设置 PYTHONPATH
PYTHONPATH=src python examples/custom_tool.py
```

所有示例**不依赖外部服务**（不需要 PostgreSQL / Redis / OpenAI API），可在任何环境直接运行。

## 示例索引

### Provider 注册表示例（7 个）

| 示例 | 注册表 | 装饰器 | 说明 |
|------|--------|--------|------|
| `custom_embedding.py` | `embedding_registry` | `@register_embedding_provider` | 注册一个基于词频的自定义 Embedding Provider |
| `custom_search.py` | `search_registry` | `@register_search_provider` | 注册一个模拟搜索 Provider（返回预设结果） |
| `custom_queue.py` | `queue_registry` | `@register_queue_provider` | 注册一个基于文件系统的持久化队列 |
| `custom_tracer.py` | `tracer_registry` | `@register_tracer_provider` | 注册一个输出到 stderr 的简单 Tracer |
| `custom_parser.py` | `parser_registry` | `@register_parser` | 注册一个 `.log` 文件解析器 |
| `custom_mcp.py` | `mcp_registry` | `@register_mcp_client` | 注册一个模拟 MCP Client |
| `custom_graph.py` | `graph_registry` | `@register_graph_provider` | 注册一个内存知识图谱 Provider |

### 扩展类型示例（2 个）

| 示例 | 扩展类型 | 装饰器 | 说明 |
|------|---------|--------|------|
| `custom_tool.py` | Tool | `@register_tool` | 注册一个"字数统计"自定义工具 |
| `custom_middleware.py` | Middleware | `@register_middleware` | 注册一个"响应延迟注入"中间件 |

### 配置切换示例（2 个）

| 示例 | 切换内容 | 说明 |
|------|---------|------|
| `switch_storage.py` | SQLite → PostgreSQL | 演示如何通过配置切换存储后端 |
| `switch_checkpointer.py` | Memory → PostgreSQL | 演示如何通过配置切换检查点后端 |

## 设计原则

1. **零依赖**：所有示例只依赖 agentbase 核心库，不需要安装可选依赖
2. **可独立运行**：每个脚本 `python examples/xxx.py` 直接运行，有清晰输出
3. **有 `--help`**：每个脚本支持 `--help` 参数
4. **真实代码路径**：示例使用真实的注册表 API，非 mock
5. **注释丰富**：关键步骤有内联注释解释原理
