# Configuration Reference

> **AgentBase** — a configuration-driven AI Agent backend / LLM agent framework / 智能体脚手架. This document describes all configuration options for `agentbase`.

**Documentation index:** [README](../README.md) · [Quick Start](quickstart.md) · [Core Services](core-services.md) · [Extensions](extensions.md) · [Error Codes](error-codes.md) · [Backend Boundaries](backend-boundaries.md) · [Project Positioning](project-positioning.md)

## Application Configuration (`configs/default.yaml`)

### `app` Section

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | `agentbase` | Application name |
| `env` | string | `dev` | Environment label |
| `log_level` | string | `INFO` | Logging level (DEBUG/INFO/WARNING/ERROR) |

### `model` Section

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | string | `openai` | Model provider |
| `name` | string | `deepseek-chat` | Model name |
| `temperature` | float | `0.0` | Sampling temperature |
| `max_tokens` | int\|null | `null` | Max tokens in response |
| `timeout_seconds` | int | `120` | Request timeout |
| `base_url` | string\|null | `null` | API base URL |
| `api_key_env` | string\|null | `null` | Environment variable name for API key |
| `extra` | dict | `{}` | Extra model kwargs |

### `backend` Section

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | string | `filesystem` | Backend type |
| `root_dir` | string | `workspace` | Backend root directory |
| `options` | dict | `{}` | Backend-specific options |

### `checkpointer` Section

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | string | `postgres` | Checkpointer type (`memory`/`sqlite`/`postgres`/`mysql`) |
| `dsn` | string\|null | `null` | Data source name |
| `options` | dict | `{}` | Checkpointer-specific options |

- `postgres`: `dsn: postgresql://user:pass@127.0.0.1:5432/agentbase`
- `sqlite`: `dsn: sqlite:///./data/checkpoints.db`
- `memory`: In-process only, lost on restart (not recommended for production)

### `storage` Section

Controls the storage backend for `MemoryManager` and `KnowledgeBase`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | `sqlite` \| `postgres` \| `mysql` | `postgres` | Storage backend type |
| `db_dir` | string | `data` | Directory for SQLite files (when `type=sqlite`) |
| `dsn` | string\|null | `null` | PostgreSQL connection string (when `type=postgres`) |

```yaml
# PostgreSQL (production)
storage:
  type: postgres
  dsn: postgresql://postgres:postgres@127.0.0.1:5432/agentbase

# SQLite (dev/zero-config)
storage:
  type: sqlite
  db_dir: data
```

### `embedding` Section

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | string | `hash` | Provider name (`none`/`hash`/`openai`/custom) |
| `options` | dict | `{}` | Provider-specific options |

- `hash`: Zero-dependency deterministic hash embeddings (default, testing).
- `openai`: OpenAI text-embedding (requires `openai` package + `OPENAI_API_KEY`).
- `none`: Disable embeddings, use text search only.
- Register custom providers with `@register_embedding_provider("name")`.

### `web_search` Section

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | string | `none` | Provider name (`none`/`duckduckgo`/`tavily`/custom) |
| `options` | dict | `{}` | Provider-specific options |

### `mcp` Section

Controls MCP (Model Context Protocol) server integration.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | string | `none` | Provider name (`none`/`memory`/custom) |
| `servers` | list | `[]` | Server configs: `[{name: ..., type: ..., options: ...}]` |
| `options` | dict | `{}` | Provider-specific options |

```yaml
mcp:
  provider: memory
  servers:
    - name: tools_server
      type: memory
      options: {}
```

### `queue` Section

Controls async request queue for background task processing.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | string | `none` | Provider name (`none`/`memory`/`redis`/custom) |
| `options` | dict | `{}` | Provider-specific options |

- `none` (default): Sync mode, no queue.
- `memory`: In-process queue for async task handling.

### `tracer` Section

Controls tracing and observability.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | string | `null` | Provider name (`null`/`memory`/custom) |
| `options` | dict | `{}` | Provider-specific options |

- `null` (default): No-op tracer, zero overhead.
- `memory`: In-memory tracer for testing and debugging.
- Register custom providers (Langfuse, OpenTelemetry) with `@register_tracer_provider`.

### `runtime` Section

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `default_agent` | string | `default` | Default agent profile |
| `config_dir` | string | `configs` | Config directory |
| `workspace_dir` | string | `workspace` | Workspace directory |
| `stream_modes` | list | `["messages", "updates"]` | LangGraph stream modes |
| `recursion_limit` | int | `50` | Max recursion depth |
| `max_concurrency` | int | `4` | Max concurrent operations (min 4) |

### `extensions` Section

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `autodiscover` | list | `[...]` | Modules to auto-discover extensions |
| `extra_modules` | list | `[]` | Additional extension modules |

## Agent Configuration (`configs/agents/*.yaml`)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | (required) | Agent name |
| `description` | string | `""` | Human-readable description |
| `model` | object\|null | `null` | Model override |
| `system_prompt` | string | `"You are a helpful deep agent."` | System prompt |
| `memory` | list | `[]` | Memory file paths |
| `skills` | list | `[]` | Skill directory paths |
| `tools` | list | `[]` | Tool names to enable |
| `middleware` | list | `[]` | Middleware names to enable |
| `subagents` | list | `[]` | Subagent names to enable |
| `permissions` | list | `[]` | Permission rules |
| `interrupt_on` | dict | `{}` | Interrupt configuration |
| `response_format` | any\|null | `null` | Response format spec |
| `capabilities` | list | `[]` | Agent capability flags |
| `metadata` | dict | `{}` | Arbitrary metadata (summary config, etc.) |

### Context Schema

`AgentConfig.get_configurable_items()` returns a list of field descriptors for
auto-generating forms or API responses:

```python
config = AgentConfig(name="my_agent")
items = config.get_configurable_items()
# [{"name": "system_prompt", "type": "text", "default": "...", "description": "..."}, ...]
```

## Environment Variables

All YAML values can be overridden with `agentbase_` prefixed environment variables:

- `AGENTBASE_APP__LOG_LEVEL=DEBUG`
- `AGENTBASE_MODEL__NAME=gpt-4`
- `AGENTBASE_STORAGE__TYPE=sqlite`
- `AGENTBASE_STORAGE__DSN=`
- `AGENTBASE_CHECKPOINTER__TYPE=memory`
- `AGENTBASE_CHECKPOINTER__DSN=`

See `.env.example` for the full list.

## Docker Compose

```bash
# Start PostgreSQL + API
docker compose up -d

# Start PostgreSQL only
docker compose up -d postgres
```
