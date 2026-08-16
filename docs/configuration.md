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
| `name` | string | `gpt-4.1-mini` | Model name (default.yaml overrides to `deepseek-chat`) |
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
| `type` | string | `sqlite` | Checkpointer type (`memory`/`sqlite`/`postgres`/`mysql`) |
| `dsn` | string\|null | `null` | Data source name |
| `options` | dict | `{}` | Checkpointer-specific options |

- `postgres`: `dsn: postgresql://user:pass@127.0.0.1:5432/agentbase`
- `sqlite`: `dsn: sqlite:///./data/checkpoints.db`
- `memory`: In-process only, lost on restart (not recommended for production)

### `storage` Section

Controls the storage backend for `MemoryManager` and `KnowledgeBase`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | `sqlite` \| `postgres` \| `mysql` \| `mongodb` | `sqlite` | Storage backend type |
| `db_dir` | string | `data` | Directory for SQLite files (when `type=sqlite`) |
| `dsn` | string\|null | `null` | PostgreSQL/MySQL/MongoDB connection string (when `type=postgres`/`mysql`/`mongodb`) |

```yaml
# PostgreSQL (production)
storage:
  type: postgres
  dsn: postgresql://agentbase:agentbase@127.0.0.1:5432/agentbase

# MySQL (production)
# storage:
#   type: mysql
#   dsn: mysql+pymysql://agentbase:agentbase@127.0.0.1:3306/agentbase

# MongoDB (production, NoSQL)
# storage:
#   type: mongodb
#   dsn: mongodb://agentbase:agentbase@127.0.0.1:27017/agentbase

# SQLite (dev/zero-config)
storage:
  type: sqlite
  db_dir: data
```

### `embedding` Section

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | string | `none` | Provider name (`none`/`hash`/`openai`/custom) |
| `options` | dict | `{}` | Provider-specific options |

- `none`: Disable embeddings, use text search only (default).
- `hash`: Zero-dependency deterministic hash embeddings (testing).
- `openai`: OpenAI text-embedding (requires `openai` package + `OPENAI_API_KEY`).
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
| `recursion_limit` | int | `50` | Max recursion depth (clamped to 1–200) |
| `max_concurrency` | int | `4` | Max concurrent operations (clamped to 1–32) |
| `session_ttl_seconds` | float\|null | `null` | Session TTL in seconds (null = never expire) |

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

### `auth` Section

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | `api_key` \| `jwt` \| `none` | `api_key` | Auth type |
| `secret` | string | (empty) | JWT signing secret (required when `type=jwt`) |
| `token_expiry_hours` | int | `24` | JWT token expiry |
| `role_permissions` | dict | `{}` | Role-permission mapping |
| `api_key` | string\|null | `null` | Override env-based API key |

### `rate_limit` Section

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable per-IP rate limiting |
| `max_requests` | int | `60` | Max requests per window |
| `window_seconds` | int | `60` | Window size in seconds |
| `burst` | int | `10` | Burst capacity |
| `quotas` | dict | `{}` | Per-role custom limits |

### `cors` Section

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `allow_origins` | list | `["*"]` | Allowed origins |
| `allow_methods` | list | `["*"]` | Allowed methods |
| `allow_headers` | list | `["*"]` | Allowed headers |
| `allow_credentials` | bool | `false` | Send credentials (forced false when origins contain `*`) |

### `metrics` Section

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable Prometheus metrics |
| `path` | string | `/metrics` | Metrics endpoint path |
| `collect_latency` | bool | `true` | Record request latency |
| `collect_agent_metrics` | bool | `true` | Record per-agent counts |

### `health_check` Section

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `check_storage` | bool | `true` | Check storage connectivity |
| `check_queue` | bool | `true` | Check queue connectivity |
| `check_embedding` | bool | `false` | Check embedding availability |
| `check_search` | bool | `false` | Check search availability |
| `check_tracer` | bool | `false` | Check tracer connectivity |

### `audit` Section

Enable structured audit logging. Provides `/audit/events/*` query + export API.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable audit logging |
| `provider` | string | `sqlite` | Provider name |
| `db_dir` | string | `data` | SQLite directory |
| `dsn` | string\|null | `null` | PostgreSQL/MySQL DSN override |
| `options` | dict | `{}` | Extra provider kwargs |

### `experiment` Section

Enable A/B testing. Provides `/experiments/*` API.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable experiments |
| `provider` | string | `memory` | Provider name |
| `options` | dict | `{}` | Extra provider kwargs |

### `redaction` Section

Enable PII/secrets masking in text. Works with `redact_output` middleware.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable redaction |
| `provider` | string | `regex` | Provider name |
| `options` | dict | `{}` | Extra provider kwargs |

### `secrets` Section

Enable secrets encryption at rest.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable secrets encryption |
| `provider` | string | `fernet` | Provider name |
| `key_file` | string | `.secret_key` | Encryption key file |
| `secrets_file` | string | `.secrets.json` | Encrypted secrets store |
| `options` | dict | `{}` | Extra provider kwargs |

### `db_query` Section

Configure the read-only DB query tool.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable tool registration |
| `dsn` | string | `""` | Database connection string |
| `max_rows` | int | `100` | Max rows returned (hard cap 1000) |
| `timeout_seconds` | int | `10` | Query timeout (hard cap 30) |
| `allowed_tables` | list | `[]` | Table whitelist (empty = all) |

### `model_manager` Section

Enable multi-model CRUD and testing. Provides `/models/*` API.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable model management |
| `provider` | string | `memory` | Provider name |
| `options` | dict | `{}` | Extra provider kwargs |

### `prompt_manager` Section

Enable prompt template CRUD and rendering. Provides `/prompts/*` API.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable prompt management |
| `provider` | string | `memory` | Provider name |
| `options` | dict | `{}` | Extra provider kwargs |

### `user_manager` Section

Enable user CRUD and authentication. Provides `/users/*` and `/auth/*` API.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable user management |
| `provider` | string | `memory` | Provider name |
| `options` | dict | `{}` | Extra provider kwargs |

### `apikey_manager` Section

Enable API key CRUD and revocation. Provides `/apikeys/*` API.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable API key management |
| `provider` | string | `memory` | Provider name |
| `options` | dict | `{}` | Extra provider kwargs |

### `migration` Section

Enable Alembic database migration. CLI: `agentbase db init/upgrade/downgrade/current/heads/history/stamp`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable migration CLI |
| `scripts_dir` | string | `migrations` | Alembic scripts directory |

### `oauth2` Section

Enable OAuth2 third-party login (Google/GitHub). Provides `/auth/oauth2/*` API.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable OAuth2 |
| `providers` | dict | `{}` | Provider configs (google/github) |

### `usage` Section

Enable token usage tracking and cost statistics. Provides `/usage/*` API.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable usage tracking |
| `provider` | string | `memory` | Provider name |
| `max_records` | int | `100000` | Max records before FIFO eviction |
| `pricing` | dict | `{}` | Custom pricing table |
| `options` | dict | `{}` | Extra provider kwargs |

### `webhook` Section

Enable webhook event notification. Provides `/webhooks/*` API.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable webhook |
| `provider` | string | `memory` | Provider name |
| `timeout_seconds` | float | `10.0` | Delivery timeout |
| `max_retries` | int | `3` | Max retry attempts |
| `retry_backoff` | float | `1.0` | Base backoff seconds |
| `options` | dict | `{}` | Extra provider kwargs |

### `feedback` Section

Enable user feedback collection. Provides `/feedback/*` API.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable feedback |
| `provider` | string | `memory` | Provider name |
| `max_records` | int | `50000` | Max records before eviction |
| `options` | dict | `{}` | Extra provider kwargs |

### `notification` Section

Enable in-app notification center. Provides `/notifications/*` API.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable notifications |
| `provider` | string | `memory` | Provider name |
| `max_records` | int | `100000` | Max records before eviction |
| `options` | dict | `{}` | Extra provider kwargs |

### `conversation` Section

Enable conversation history recording. Provides `/conversations/*` API.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable conversation history |
| `provider` | string | `memory` | Provider name |
| `max_conversations` | int | `10000` | Max conversations before eviction |
| `options` | dict | `{}` | Extra provider kwargs |

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
