# AgentBase

[![PyPI](https://img.shields.io/pypi/v/agentbase.svg)](https://pypi.org/project/agentbase/)
[![Python](https://img.shields.io/pypi/pyversions/agentbase.svg)](https://pypi.org/project/agentbase/)
[![CI](https://github.com/LPK3215/agentbase/actions/workflows/ci.yml/badge.svg)](https://github.com/LPK3215/agentbase/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://img.shields.io/badge/tests-2686-brightgreen.svg)](#)
[![Coverage](https://img.shields.io/badge/coverage-79%25-green.svg)](#)

<p align="center">
  <em>
    <strong>AI Agent 智能体脚手架</strong> · <strong>Agent 智能体框架</strong> · <strong>AI Agent 项目脚手架</strong> ·
    <strong>Agent 智能体后端</strong> · <strong>AI 智能体开发平台</strong> ·
    LLM Agent Framework · Agent Scaffold · LLM 应用脚手架 · Agentic AI Backend
  </em>
</p>

**Configuration-driven AI Agent backend for secondary development** — a production-grade **AI Agent framework / LLM agent scaffold** built on [deepagents](https://pypi.org/project/deepagents/), [LangChain](https://pypi.org/project/langchain/), and [LangGraph](https://pypi.org/project/langgraph/). Assemble and run production-grade **AI agents / intelligent agent systems** from YAML configuration, without writing boilerplate. Use it as an **agent application scaffolding** layer, an **AI agent service framework**, or an **intelligent agent development starter kit**.

`agentbase` provides YAML configuration, pluggable extension registries, component factories, a 20-command CLI, and a FastAPI service layer with 100 REST/WebSocket routes. It wires together the infrastructure every AI Agent backend needs: model configuration, prompt templates, user management, API key management, session management, memory management, knowledge base with RAG, document parsing, task queues, API security, tracing, and evaluation — all with sensible defaults and every component swappable via a one-line config change.

## Key Features

- **Config-driven agent assembly** — agents, models, storage, embeddings defined in YAML, validated by `agentbase doctor`
- **Pluggable registry system** — 25 extension registries (tools, middleware, subagents, parsers, embeddings, search, MCP, queue, tracer, graph, storage, checkpointer, model manager, prompt manager, user manager, API key manager, usage tracking, webhook, feedback, notification, conversation, audit, experiment, redaction, secrets); swap PostgreSQL ↔ SQLite, OpenAI ↔ local embeddings by changing one line
- **Full API server** — FastAPI with 100 routes: agent invoke/stream/resume, WebSocket real-time chat, async task queue, document upload + KB search, audit query + export, A/B experiments, model CRUD + connectivity testing, prompt template CRUD + rendering, user CRUD + authentication, API key CRUD + revocation + verification, OAuth2 third-party login (Google/GitHub), session management + cleanup, token usage tracking + cost statistics, webhook event notification + endpoint CRUD + delivery records, user feedback collection + ratings + statistics, notification center (create/broadcast/query/mark-read), conversation history management, rate-limit admin, Prometheus metrics, OpenAPI docs
- **RAG knowledge base** — 9 document formats (PDF/DOCX/HTML/XLSX/PPTX…), 3 chunking strategies, pgvector native `<=>` cosine retrieval, in-memory fallback
- **37 built-in tools** — file ops, skill/memory/knowledge-base CRUD, web search & fetch, HTTP request, read-only DB query, MCP client, sandboxed code execution, email sending, audio transcription
- **9 middleware** — request_logger, retry, timeout, summary, cache, redact_output, rate_limit, model_router, audit_log
- **Enterprise hardening** — API key auth, JWT/RBAC, OAuth2 (Google/GitHub), CORS, rate limiting, request tracing, structured `agentbase_<domain>_<nnn>` error codes, Docker deployment
- **2,686 tests, 79% coverage** — full CI pipeline via GitHub Actions

## Architecture

```mermaid
graph TD
    subgraph "Entry Points (CLI / FastAPI / WebSocket)"
        A[agentbase CLI<br/>20 commands] --> C[Service Layer<br/>100 REST + WS routes]
        B[FastAPI App] --> C
    end

    subgraph "Core (config-driven, pluggable)"
        C --> D[YAML Config<br/>validated by agentbase doctor]
        D --> E[Extension Registries<br/>25 pluggable providers · tools · middleware · subagents · parsers<br/>embeddings · search · MCP · queue · tracer · graph · storage · checkpointer<br/>model_manager · prompt_manager · user_manager · apikey_manager · usage<br/>webhook · feedback · notification · conversation · audit · experiment<br/>redaction · secrets · migration · oauth2]
        E --> F[Component Factories<br/>deepagents + LangChain + LangGraph]
    end

    subgraph "Infrastructure Services"
        F --> G[Agent Runtime & Session]
        F --> H[Memory & Knowledge Base (RAG)]
        F --> I[Model/Prompt/User/APIKey Manager]
        F --> J[Queue & Webhook & Usage Tracking]
        F --> K[Audit & Tracing & Feedback]
        F --> L[Notification & Conversation & OAuth2]
        F --> M[Migration & Evaluation & Secrets]
    end

    style D fill:#fff3cd
    style E fill:#d1e7dd
    style F fill:#cfe2ff
```

**Why agentbase?** Building an AI Agent backend involves repetitive infrastructure work: model configuration, memory management, knowledge base, document parsing, task queues, API security, tracing, and more. AgentBase handles all of this with sensible defaults — and every component is pluggable via a registry system. Swap databases, embedding models, queues, or tracers by changing one line of config. No rewrite required.

## Requirements

- Python >= 3.11
- PostgreSQL 16+ with pgvector (for production, via Docker or local install)
- Or SQLite (zero-config, no install needed, dev/single-user)

## Installation

```bash
# From source (development)
pip install .

# Or with uv
uv pip install .

# With optional provider extras
pip install ".[openai,anthropic,google]"

# With PostgreSQL support
pip install ".[postgres]"

# With API server support
pip install ".[api]"

# With RAG document parsing (PDF, DOCX)
pip install ".[rag]"

# With everything
pip install ".[all]"

# With development tooling
pip install ".[dev]"
```

## Quick Start

See [docs/quickstart.md](docs/quickstart.md) for a complete end-to-end guide.

```bash
# 1. Set your model API key
export DEEPSEEK_API_KEY="your-key-here"

# 2. Start PostgreSQL (with pgvector)
docker compose up -d postgres

# 3. Validate your setup
agentbase doctor

# 4. Run an agent
agentbase run "Hello, what can you do?"

# 5. Start the API server
agentbase serve --reload
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `agentbase doctor` | Validate configuration and agent assembly |
| `agentbase agents` | List available agent profiles |
| `agentbase extensions` | List registered extensions |
| `agentbase extensions --verbose` | Show extension metadata |
| `agentbase run "message"` | Invoke an agent (single response) |
| `agentbase stream "message"` | Stream an agent response |
| `agentbase resume --thread-id ID --decision approve` | Resume an interrupted agent run |
| `agentbase serve --port 8000` | Start the FastAPI server |
| `agentbase backup -o backup.sql` | Backup database (SQL/JSON format) |
| `agentbase restore backup.sql` | Restore database from backup |
| `agentbase worker` | Start a queue worker process |
| `agentbase version` | Print version information |
| `agentbase config validate` | Validate configuration files |
| `agentbase config show` | Display resolved configuration |
| `agentbase db init` | Initialize migration scripts directory |
| `agentbase db upgrade` | Upgrade database to latest schema |
| `agentbase db downgrade` | Downgrade database by one step |
| `agentbase db current` | Show current migration revision |
| `agentbase db heads` | Show head migration revisions |
| `agentbase db history` | Show migration history |
| `agentbase db stamp --revision REV` | Stamp database with a revision |

### Common Options

- `--root <dir>` — Project root directory
- `--agent <name>` — Agent profile to use
- `--thread-id <id>` — Thread ID for session continuity

## API Server

Start the server:

```bash
agentbase serve --host 0.0.0.0 --port 8000 --reload
```

### Authentication

Set `AGENTBASE_API_KEY` to enable API Key authentication:

```bash
# Enable auth
export AGENTBASE_API_KEY="your-secret-key"

# Call API with key
curl -H "Authorization: Bearer your-secret-key" http://localhost:8000/agents

# Or use X-API-Key header
curl -H "X-API-Key: your-secret-key" http://localhost:8000/agents

# Disable auth (dev mode)
export AGENTBASE_API_KEY=""
```

Endpoints marked **public** don't require authentication.

### Endpoints (100 total)

<details>
<summary>Click to expand full endpoint list</summary>

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/health` | Health check | Public |
| GET | `/metrics` | Prometheus metrics | Public |
| GET | `/agents` | List all agents | Required |
| GET | `/agents/{name}` | Get agent config | Required |
| GET | `/agents/{name}/configurable` | Get configurable items | Required |
| POST | `/agents/{name}/invoke` | Invoke agent (sync) | Required |
| POST | `/agents/{name}/stream` | Stream agent (SSE) | Required |
| POST | `/agents/{name}/resume` | Resume interrupted agent | Required |
| POST | `/queue/submit` | Submit async task | Required |
| GET | `/queue/{task_id}` | Get task status | Required |
| GET | `/queue` | List tasks | Required |
| DELETE | `/queue/{task_id}` | Cancel task | Required |
| POST | `/queue/process` | Process pending tasks | Required |
| POST | `/documents/upload` | Upload file to knowledge base | Required |
| GET | `/documents` | List documents | Required |
| GET | `/documents/{id}` | Get document | Required |
| DELETE | `/documents/{id}` | Delete document | Required |
| POST | `/documents/search` | Search knowledge base | Required |
| GET | `/audit/events` | Query audit logs | Required |
| GET | `/audit/events/count` | Count audit events | Required |
| GET | `/audit/events/export` | Export audit logs (JSON/CSV/YAML) | Required |
| GET | `/experiments` | List A/B test experiments | Required |
| POST | `/experiments` | Create experiment | Required |
| GET | `/experiments/{name}` | Get experiment details | Required |
| DELETE | `/experiments/{name}` | Delete experiment | Required |
| POST | `/experiments/{name}/assign` | Assign request to variant | Required |
| POST | `/experiments/{name}/results` | Record experiment result | Required |
| GET | `/experiments/{name}/stats` | Get experiment statistics | Required |
| GET | `/models` | List model configs | Required |
| POST | `/models` | Register model config | Required |
| GET | `/models/{name}` | Get model config | Required |
| PATCH | `/models/{name}` | Update model config | Required |
| DELETE | `/models/{name}` | Delete model config | Required |
| POST | `/models/{name}/test` | Test model connectivity | Required |
| GET | `/prompts` | List prompt templates | Required |
| POST | `/prompts` | Register prompt template | Required |
| GET | `/prompts/{name}` | Get prompt template | Required |
| PATCH | `/prompts/{name}` | Update prompt template | Required |
| DELETE | `/prompts/{name}` | Delete prompt template | Required |
| POST | `/prompts/{name}/render` | Render prompt with variables | Required |
| GET | `/users` | List users | Required |
| POST | `/users` | Register user | Required |
| GET | `/users/{username}` | Get user details | Required |
| PATCH | `/users/{username}` | Update user | Required |
| DELETE | `/users/{username}` | Delete user | Required |
| POST | `/auth/register` | User registration | Required |
| POST | `/auth/login` | User login | Required |
| GET | `/auth/oauth2/providers` | List OAuth2 providers | Public |
| GET | `/auth/oauth2/{provider}/authorize` | OAuth2 authorize redirect | Public |
| GET | `/auth/oauth2/{provider}/callback` | OAuth2 callback + JWT | Public |
| GET | `/sessions` | List sessions | Required |
| GET | `/sessions/stats` | Session statistics | Required |
| GET | `/sessions/{thread_id}` | Get session details | Required |
| DELETE | `/sessions/{thread_id}` | Cancel session | Required |
| POST | `/sessions/cleanup` | Cleanup sessions | Required |
| GET | `/apikeys` | List API keys | Required |
| POST | `/apikeys` | Create API key | Required |
| GET | `/apikeys/{key_id}` | Get API key | Required |
| PATCH | `/apikeys/{key_id}` | Update API key | Required |
| DELETE | `/apikeys/{key_id}` | Delete API key | Required |
| POST | `/apikeys/{key_id}/revoke` | Revoke API key | Required |
| POST | `/apikeys/verify` | Verify API key | Required |
| GET | `/admin/rate-limit` | View rate-limit bucket status | Required |
| DELETE | `/admin/rate-limit/buckets` | Clear all rate-limit buckets | Required |
| POST | `/admin/rate-limit/quotas/{role}` | Set role rate-limit quota | Required |
| GET | `/usage/stats` | Aggregated usage statistics | Required |
| GET | `/usage/records` | Query usage records (paginated) | Required |
| GET | `/usage/summary` | Usage summary (totals) | Required |
| DELETE | `/usage/records` | Clear all usage records | Required |
| GET | `/webhooks` | List webhook endpoints | Required |
| POST | `/webhooks` | Register webhook endpoint | Required |
| GET | `/webhooks/{endpoint_id}` | Get webhook endpoint detail | Required |
| PATCH | `/webhooks/{endpoint_id}` | Update webhook endpoint | Required |
| DELETE | `/webhooks/{endpoint_id}` | Delete webhook endpoint | Required |
| POST | `/webhooks/{endpoint_id}/test` | Test webhook endpoint (sync) | Required |
| GET | `/webhooks/deliveries` | Query webhook deliveries (paginated) | Required |
| GET | `/webhooks/stats` | Webhook delivery statistics | Required |
| GET | `/feedback` | Query user feedback (paginated) | Required |
| POST | `/feedback` | Submit user feedback (rating/comment) | Required |
| GET | `/feedback/stats` | Aggregate feedback statistics | Required |
| GET | `/feedback/{record_id}` | Get feedback record detail | Required |
| PATCH | `/feedback/{record_id}` | Update feedback fields | Required |
| DELETE | `/feedback/{record_id}` | Delete feedback record | Required |
| WS | `/ws/agents/{name}` | WebSocket real-time agent | Token |
| GET | `/notifications` | List notifications | Required |
| POST | `/notifications` | Create notification | Required |
| GET | `/notifications/stats` | Notification statistics | Required |
| GET | `/notifications/unread-count` | Unread count for user | Required |
| POST | `/notifications/broadcast` | Broadcast to all users | Required |
| POST | `/notifications/read-all` | Mark all as read | Required |
| GET | `/notifications/{id}` | Get notification detail | Required |
| PATCH | `/notifications/{id}` | Update notification | Required |
| POST | `/notifications/{id}/read` | Mark as read | Required |
| POST | `/notifications/{id}/unread` | Mark as unread | Required |
| DELETE | `/notifications/{id}` | Delete notification | Required |
| GET | `/conversations` | List conversations (paginated, filterable) | Required |
| GET | `/conversations/stats` | Aggregate conversation statistics | Required |
| GET | `/conversations/{thread_id}` | Get conversation history (with messages) | Required |
| PATCH | `/conversations/{thread_id}` | Update conversation metadata | Required |
| DELETE | `/conversations/{thread_id}` | Delete conversation | Required |
| GET | `/docs` | OpenAPI docs (Swagger) | Public |
| GET | `/redoc` | API docs (ReDoc) | Public |

</details>

### OAuth2 Third-Party Login (Google/GitHub)

Enable OAuth2 login to allow users to authenticate via Google or GitHub accounts:

```yaml
# configs/default.yaml
oauth2:
  enabled: true
  providers:
    google:
      client_id: "xxx.apps.googleusercontent.com"
      client_secret: "${GOOGLE_OAUTH_SECRET}"
      redirect_uri: "http://localhost:8000/auth/oauth2/google/callback"
      scopes: ["openid", "email", "profile"]
      default_roles: ["user"]
    github:
      client_id: "Iv1.xxx"
      client_secret: "${GITHUB_OAUTH_SECRET}"
      redirect_uri: "http://localhost:8000/auth/oauth2/github/callback"
      scopes: ["user:email"]
      default_roles: ["user"]
```

**Flow**: `GET /auth/oauth2/{provider}/authorize` → redirect to provider → callback with authorization code → exchange for access token → fetch user info → auto-register or match existing user by email → issue JWT.

**CSRF protection**: Each authorize request generates a one-time state token (10-minute expiry). The callback endpoint validates the state before proceeding.

Requires `user_manager.enabled=true` for auto-registration.

### WebSocket

Real-time bidirectional agent communication:

```javascript
const ws = new WebSocket("ws://localhost:8000/ws/agents/default?token=your-key");
ws.send(JSON.stringify({message: "Hello", thread_id: null}));
ws.onmessage = (e) => console.log(JSON.parse(e.data));
```

### File Upload

Upload documents to the knowledge base via multipart form:

```bash
curl -X POST http://localhost:8000/documents/upload \
  -H "Authorization: Bearer your-key" \
  -F "file=@report.pdf" \
  -F "title=Quarterly Report"
```

### Prometheus Metrics

Metrics available at `GET /metrics` (Prometheus format):

- `agentbase_requests_total` — Total HTTP requests
- `agentbase_agent_invocations_total` — Agent invocations
- `agentbase_documents_uploaded_total` — Documents uploaded
- `agentbase_errors_total` — Server errors (5xx)
- `agentbase_requests_by_path{path="..."}` — Requests by path
- `agentbase_requests_by_status{status="..."}` — Requests by status code

API docs available at `http://localhost:8000/docs`.

## Configuration

See [docs/configuration.md](docs/configuration.md) for full reference.

Key files:
- `configs/default.yaml` — Application config (model, storage, checkpointer, embedding, etc.)
- `configs/agents/*.yaml` — Agent profiles (default, coder, researcher, interrupt_demo)
- `.env` — Environment variables (see `.env.example`)

### Key Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENTBASE_API_KEY` | (empty) | API Key for authentication (empty = disabled) |
| `AGENTBASE_CORS_ORIGINS` | `*` | Allowed CORS origins (comma-separated) |
| `AGENTBASE_STORAGE__TYPE` | `sqlite` | Storage backend (`sqlite`/`postgres`/`mysql`/`mongodb`) |
| `AGENTBASE_STORAGE__DSN` | from config | PostgreSQL/MySQL/MongoDB connection string |
| `AGENTBASE_CHECKPOINTER__TYPE` | `sqlite` | Checkpointer type (`sqlite`/`postgres`/`memory`/`mysql`) |
| `AGENTBASE_EMBEDDING__PROVIDER` | `none` | Embedding provider (`none`/`hash`/`openai`) |
| `AGENTBASE_AUTH__TYPE` | `api_key` | Auth type (`api_key`/`jwt`) |
| `AGENTBASE_AUTH__SECRET` | (empty) | JWT signing secret (required when `type=jwt`) |
| `AGENTBASE_OAUTH2__ENABLED` | `false` | Enable OAuth2 login (Google/GitHub) |
| `AGENTBASE_USER_MANAGER__ENABLED` | `false` | Enable user CRUD + auth |
| `AGENTBASE_MODEL_MANAGER__ENABLED` | `false` | Enable model CRUD + connectivity testing |
| `AGENTBASE_PROMPT_MANAGER__ENABLED` | `false` | Enable prompt template CRUD + rendering |
| `AGENTBASE_APIKEY_MANAGER__ENABLED` | `false` | Enable API key CRUD + revocation + verification |
| `AGENTBASE_USAGE__ENABLED` | `false` | Enable token usage tracking + cost statistics |
| `AGENTBASE_WEBHOOK__ENABLED` | `false` | Enable webhook event notification |
| `AGENTBASE_FEEDBACK__ENABLED` | `false` | Enable user feedback collection |
| `AGENTBASE_NOTIFICATION__ENABLED` | `false` | Enable in-app notification center |
| `AGENTBASE_CONVERSATION__ENABLED` | `false` | Enable conversation history recording |
| `AGENTBASE_AUDIT__ENABLED` | `false` | Enable structured audit logging |
| `AGENTBASE_EXPERIMENT__ENABLED` | `false` | Enable A/B testing framework |
| `AGENTBASE_REDACTION__ENABLED` | `false` | Enable PII/secrets masking |
| `AGENTBASE_SECRETS__ENABLED` | `false` | Enable secrets encryption at rest |
| `AGENTBASE_DB_QUERY__ENABLED` | `false` | Enable read-only DB query tool |
| `AGENTBASE_MIGRATION__ENABLED` | `true` | Enable Alembic migration CLI |
| `AGENTBASE_APP__ENV` | `dev` | Environment label |
| `AGENTBASE_APP__LOG_LEVEL` | `INFO` | Log level |

## Core Services & Pluggable Providers

| Layer | Default | How to Replace |
|-------|---------|----------------|
| Storage | SQLite (zero-config) | `storage.type: postgres` / `mysql` / `mongodb` |
| Document Parsing | txt, md, pdf, docx, html, xlsx, pptx, LLM, OCR | `@register_parser()` |
| Embeddings | None (disabled) | `@register_embedding_provider("hash")` / `"openai"` |
| Web Search | None (disabled) | `@register_search_provider("duckduckgo")` / `"tavily"` |
| MCP | None (disabled) | `mcp.provider: memory` |
| Queue | None (sync) | `queue.provider: memory` / `redis` / `celery` |
| Tracer | Null (no-op) | `tracer.provider: memory` / `langfuse` / `opentelemetry` |
| Knowledge Graph | Null (no-op) | `@register_graph_provider("neo4j")` |
| Model Manager | Null (disabled) | `model_manager.provider: memory` |
| Prompt Manager | Null (disabled) | `prompt_manager.provider: memory` |
| User Manager | Null (disabled) | `user_manager.provider: memory` |
| API Key Manager | Null (disabled) | `apikey_manager.provider: memory` |
| Usage Tracking | Null (disabled) | `usage.provider: memory` |
| Webhook | Null (disabled) | `webhook.provider: memory` |
| Feedback | Null (disabled) | `feedback.provider: memory` |
| Notification Center | Null (disabled) | `notification.provider: memory` |
| Conversation History | Null (disabled) | `conversation.provider: memory` |
| Audit Log | Null (disabled) | `audit.provider: sqlite` |
| A/B Testing | Null (disabled) | `experiment.provider: memory` |
| PII Redaction | Null (disabled) | `redaction.provider: regex` |
| Secrets Encryption | Null (disabled) | `secrets.provider: fernet` |
| Checkpointer | SQLite | `checkpointer.type: postgres` / `memory` / `mysql` |
| OAuth2 Login | Disabled | `oauth2.enabled: true` (Google/GitHub) |
| Workspace | Filesystem | `WorkspaceManager` |

See [docs/core-services.md](docs/core-services.md) for details.

## Built-in Tools (37)

| Tool | Description |
|------|-------------|
| `echo` / `list_workspace` | Workspace utilities |
| `get_time` / `now_local` | Current UTC/local timestamp |
| `read_file` / `write_file` / `grep` | File operations (1MB limit, binary detection) |
| `skill_*` (6) | Skill CRUD + search |
| `memory_*` (7) | Memory CRUD + search + batch save + count |
| `kb_*` (8) | Knowledge base CRUD + ingest + search |
| `web_search` / `web_fetch` / `http_request` | Web search, fetch, HTTP requests |
| `db_query` | Read-only SELECT queries (whitelist, injection prevention) |
| `mcp_list_tools` / `mcp_call_tool` | MCP server tools |
| `code_execute` | Sandboxed Python execution (timeout, env isolation) |
| `transcribe` | Audio/video transcription (Whisper API/local) |
| `email_sender` | SMTP email (text/HTML, multi-recipient, SSL/TLS) |

## Built-in Middleware (9)

- `request_logger` — Log model call requests/responses with duration
- `retry` — Exponential backoff retry with jitter
- `timeout` — Model call timeout control
- `summary` — L1/L2 conversation history compaction
- `cache` — LRU + TTL response caching
- `redact_output` — PII/sensitive data masking in model outputs
- `rate_limit` — Per-agent/global model call rate limiting
- `model_router` — Multi-model routing (round_robin/weighted/random/failover)
- `audit_log` — Audit event recording for model calls

## RAG Pipeline

```
Upload → Parse (9 formats) → Chunk (3 strategies) → Embed (Hash/OpenAI/SentenceTransformers) → Store (pgvector)
                                                                        ↓
Query → Embed → pgvector cosine distance (IVFFlat) → Top-K results → Agent
```

When pgvector is available, uses native `vector` columns and `<=>` operator for O(log n) retrieval.
Otherwise falls back to in-memory cosine similarity.

## Error Codes

See [docs/error-codes.md](docs/error-codes.md). All errors carry `agentbase_<domain>_<nnn>`.

## Project Structure

```
agentbase/
├── configs/               # Configuration files
│   ├── default.yaml       # App config (model, storage, embedding, search, mcp, queue, tracer)
│   └── agents/            # Agent profiles
├── src/agentbase/
│   ├── api.py             # FastAPI service layer (100 routes, auth, CORS, rate limit, metrics)
│   ├── cli.py             # CLI entry point (20 commands)
│   ├── config/            # Config loading & schema
│   ├── core/              # 30 core modules (memory, knowledge, queue, queue_celery, skills, workspace, storage, storage_mongodb, mcp, tracer, graph, audit, redaction, secrets, experiment, migration, model_manager, prompt, user_manager, apikey_manager, oauth2, usage, webhook, feedback, notification, conversation, evaluation, parsers, embeddings, search)
│   ├── factories/         # Component factories
│   ├── registry/          # Extension registries (25 pluggable providers)
│   ├── runtime/           # AgentRunner, events, errors, logging
│   └── extensions/        # Built-in extensions (tools, middleware, subagents, parsers, auth)
├── tests/                 # 2,686 tests, 79% coverage
├── Dockerfile             # Container image
├── docker-compose.yml     # PostgreSQL (pgvector) + API
├── .env.example           # Environment variable template
└── pyproject.toml         # Project metadata & dependencies
```

## Docker Deployment

```bash
# Start everything (PostgreSQL + API)
docker compose up -d

# API available at http://localhost:8000
docker compose logs -f api

# With API key authentication
AGENTBASE_API_KEY="secret" docker compose up -d
```

## Documentation

| Guide | Content |
|-------|---------|
| [Quick Start](docs/quickstart.md) | End-to-end setup & first agent in 11 steps |
| [Configuration](docs/configuration.md) | Full config reference (YAML + env vars) |
| [Core Services](docs/core-services.md) | 30 core modules & pluggable provider swaps |
| [Extensions](docs/extensions.md) | 25 extension registries, tools, middleware |
| [Error Codes](docs/error-codes.md) | `agentbase_<domain>_<nnn>` structured errors |
| [Backend Boundaries](docs/backend-boundaries.md) | Architecture & separation of concerns |
| [Project Positioning](docs/project-positioning.md) | Why agentbase exists, design principles |

## Project Positioning

**AgentBase is a scaffolding layer for building AI Agent backends.** It is not a model, a vector database, or a UI kit — it is the engineering backbone between your LLM and your product. Three things define it:

1. **Configuration-first** — agents, models, storage, embeddings, search, MCP, queue, tracer are all declared in YAML and validated by `agentbase doctor`. No boilerplate.
2. **Everything is pluggable** — every subsystem is a registered extension. Swap PostgreSQL↔SQLite, OpenAI↔local embeddings, DuckDuckGo↔Tavily with a one-line config change.
3. **Secondary development ready** — build your own agents, tools, middleware, parsers, providers via simple decorators (`@register_tool`, `@register_parser`, `@register_embedding_provider`, …).

Whether you need an **AI Agent 智能体脚手架**, an **LLM agent framework**, a **RAG-backed intelligent agent system**, or a **FastAPI agent service layer** — AgentBase is designed to be the starting point you extend, not reinvent.

## Contributing

Contributions, issues, and feature requests are welcome. See the [issues page](https://github.com/LPK3215/agentbase/issues) to get started.

- Report bugs / request features via GitHub Issues
- Check the [ROADMAP](docs/ROADMAP.md) for planned modules
- Follow the [Backend Boundaries](docs/backend-boundaries.md) for code contributions

## License

[MIT](LICENSE) © [LPK3215](https://github.com/LPK3215)
