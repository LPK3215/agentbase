# AgentBase

[![PyPI](https://img.shields.io/pypi/v/agentbase.svg)](https://pypi.org/project/agentbase/)
[![Python](https://img.shields.io/pypi/pyversions/agentbase.svg)](https://pypi.org/project/agentbase/)
[![CI](https://github.com/LPK3215/agentbase/actions/workflows/ci.yml/badge.svg)](https://github.com/LPK3215/agentbase/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://img.shields.io/badge/tests-520-brightgreen.svg)](#)
[![Coverage](https://img.shields.io/badge/coverage-65%25-green.svg)](#)

**Configuration-driven AI Agent backend for secondary development** — built on [deepagents](https://pypi.org/project/deepagents/), [LangChain](https://pypi.org/project/langchain/), and [LangGraph](https://pypi.org/project/langgraph/). Assemble and run production-grade AI agents from YAML configuration, without writing boilerplate.

`agentbase` provides YAML configuration, pluggable extension registries, component factories, a 10-command CLI, and a FastAPI service layer with 21 REST/WebSocket routes. It wires together the infrastructure every AI Agent backend needs: model configuration, memory management, knowledge base with RAG, document parsing, task queues, API security, tracing, and evaluation — all with sensible defaults and every component swappable via a one-line config change.

## Key Features

- **Config-driven agent assembly** — agents, models, storage, embeddings defined in YAML, validated by `agentbase doctor`
- **Pluggable registry system** — 9 extension registries (tools, middleware, subagents, parsers, embeddings, search, MCP, queue, tracer); swap PostgreSQL ↔ SQLite, OpenAI ↔ local embeddings by changing one line
- **Full API server** — FastAPI with 21 endpoints: agent invoke/stream/resume, WebSocket real-time chat, async task queue, document upload + KB search, Prometheus metrics, OpenAPI docs
- **RAG knowledge base** — 9 document formats (PDF/DOCX/HTML/XLSX/PPTX…), 3 chunking strategies, pgvector native `<=>` cosine retrieval, in-memory fallback
- **32 built-in tools** — file ops, skill/memory/knowledge-base CRUD, web search & fetch, MCP client, sandboxed code execution, audio transcription
- **Enterprise hardening** — API key auth, CORS, rate limiting, request tracing, structured `agentbase_<domain>_<nnn>` error codes, Docker deployment
- **520 tests, 65% coverage** — full CI pipeline via GitHub Actions

**Why agentbase?** Building an AI Agent backend involves repetitive infrastructure work: model configuration, memory management, knowledge base, document parsing, task queues, API security, tracing, and more. AgentBase handles all of this with sensible defaults — and every component is pluggable via a registry system. Swap databases, embedding models, queues, or tracers by changing one line of config. No rewrite required.

## Requirements

- Python >= 3.11
- PostgreSQL 16+ with pgvector (via Docker or local install)

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
| `agentbase init` | Initialize a new project (generate config, agent, directory structure) |
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

### Endpoints (21 total)

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
| WS | `/ws/agents/{name}` | WebSocket real-time agent | Token |
| GET | `/docs` | OpenAPI docs (Swagger) | Public |
| GET | `/redoc` | API docs (ReDoc) | Public |

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
| `AGENTBASE_STORAGE__TYPE` | `postgres` | Storage backend (`postgres`/`sqlite`/`mysql`) |
| `AGENTBASE_STORAGE__DSN` | from config | PostgreSQL connection string |
| `AGENTBASE_CHECKPOINTER__TYPE` | `postgres` | Checkpointer type (`postgres`/`sqlite`/`memory`/`mysql`) |
| `AGENTBASE_EMBEDDING__PROVIDER` | `hash` | Embedding provider (`hash`/`openai`/`none`) |
| `AGENTBASE_APP__ENV` | `dev` | Environment label |
| `AGENTBASE_APP__LOG_LEVEL` | `INFO` | Log level |

## Core Services & Pluggable Providers

| Layer | Default | How to Replace |
|-------|---------|----------------|
| Storage | PostgreSQL (pgvector) | `storage.type: sqlite` |
| Document Parsing | txt, md, pdf, docx, html, xlsx | `@register_parser()` |
| Embeddings | Hash (zero-dep) | `@register_embedding_provider("openai")` |
| Web Search | DuckDuckGo | `@register_search_provider("tavily")` |
| MCP | None | `mcp.provider: memory` |
| Queue | None (sync) | `queue.provider: memory` |
| Tracer | Null (no-op) | `tracer.provider: memory` |
| Knowledge Graph | Null (no-op) | `@register_graph_provider("neo4j")` |
| Workspace | Filesystem | `WorkspaceManager` |

See [docs/core-services.md](docs/core-services.md) for details.

## Built-in Tools (32)

| Tool | Description |
|------|-------------|
| `echo` | Echo text back |
| `get_time` / `now_local` | Current UTC/local timestamp |
| `read_file` / `write_file` / `grep` / `list_workspace` | File operations |
| `skill_*` (6) | Skill CRUD + search |
| `memory_*` (5) | Memory CRUD + search |
| `kb_*` (8) | Knowledge base CRUD + ingest + search |
| `web_search` / `web_fetch` | Web search + fetch |
| `mcp_list_tools` / `mcp_call_tool` | MCP server tools |
| `code_execute` | Execute Python code in a sandboxed subprocess |
| `transcribe` | Transcribe audio/video to text (Whisper API/local) |

## Built-in Middleware (5)

- `request_logger`, `retry`, `timeout`, `summary`, `cache`

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
│   ├── api.py             # FastAPI service layer (21 routes, auth, CORS, rate limit, metrics)
│   ├── cli.py             # CLI entry point (10 commands)
│   ├── config/            # Config loading & schema
│   ├── core/              # 13 core services (skills, memory, knowledge, storage, parsers, embeddings, search, mcp, queue, evaluation, tracer, workspace, graph)
│   ├── factories/         # Component factories
│   ├── registry/          # Extension registries (9 pluggable providers)
│   ├── runtime/           # AgentRunner, events, errors, logging
│   └── extensions/        # Built-in extensions (tools, middleware, subagents, parsers, auth)
├── tests/                 # 520 tests, 65% coverage
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

## License

[MIT](LICENSE)
