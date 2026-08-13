# Changelog

## [0.4.0] - Production Grade + Rebrand

### Added
- **JWT/RBAC authentication**: HMAC-SHA256 JWT tokens, admin/user/readonly roles, path-level permission control
- **BLEU evaluation metric**: Pure-Python BLEU-4 score for translation/generation quality
- **ROUGE-L evaluation metric**: LCS-based F1 score for summarization quality
- **Cache middleware**: TTL + LRU eviction, caches identical model calls to save API costs
- **Code execution sandbox**: `code_execute` tool — subprocess with timeout, restricted env, output capture
- **Neo4j knowledge graph**: `Neo4jGraphProvider` with Cypher queries, RRF fusion with vector search
- **OpenTelemetry tracer**: `OpenTelemetryTracer` with OTLP exporter, spans and trace context
- **MySQL storage backend**: `MySQLBackend` with auto SQL dialect conversion (`?` → `%s`)
- **Audio/video transcription**: `transcribe` tool — OpenAI Whisper API + local whisper model
- **K8s deployment**: Helm Chart (`deploy/k8s/`) + native manifests, values.yaml
- **Nginx reverse proxy**: `deploy/nginx/nginx.conf` — SSE/WebSocket proxy, rate limiting, TLS template
- **Database backup/restore**: `agentbase backup` / `agentbase restore` (SQL + JSON formats)
- **Request ID middleware**: `X-Request-ID` header, auto-generated UUID, propagated through logs
- `docs/project-positioning.md` — project identity, design philosophy, boundary principles
- `docs/backend-boundaries.md` — complete feature boundary reference (16 sections)
- 9 middleware (5 built-in: request_logger, retry, timeout, summary, cache)
- 32 tools (added code_execute, transcribe)
- 8 CLI commands (added backup, restore)

### Changed
- **Package renamed**: `myda` → `agentbase` (package name, CLI, env vars, Docker, Redis, Prometheus)
- Environment variable prefix: `MYDA_` → `AGENTBASE_`
- API version bumped to 0.4.0
- Total tests: 520, coverage: 65%
- LICENSE file added (MIT)

### Fixed
- `AgentbaseError` class names unified to PascalCase
- CI workflow updated to use `agentbase` package name
- Branding consistency across all docs and code

## [0.3.1] - Local-First Enhancements

### Added
- **SentenceTransformers local embedding**: HuggingFace models, offline semantic embeddings, zero API cost
- **Redis queue provider**: Persistent async task queue, multi-process safe, JSON serialization
- **CrossEncoder reranker**: `CrossEncoderReranker` for second-stage relevance reranking
- **Semantic chunking strategy**: Recursive chunker (title → paragraph → sentence) with overlap support
- **PPTX parser**: `PptxParser` extracts text + tables from PowerPoint slides
- **LLM document parser**: `LLMDocumentParser` uses LLM API to convert any format to structured Markdown
- **OCR parser**: `OCRParser` uses pytesseract + pillow + pdf2image for scanned documents
- **Langfuse tracer**: `LangfuseTracer` with span/trace context, API key auth
- **LLM judge evaluation metric**: `LLMJudgeMetric` uses LLM to score agent responses
- **Request ID correlation**: UUID-based request tracing across logs and responses
- RAG pipeline: 9 parsers, 3 chunking strategies, 4 embedding providers, reranker
- Total tests: 520, coverage: 65%

### Changed
- `docs/backend-boundaries.md` updated with all v0.3.1 features
- `.env.example` updated with Langfuse, SentenceTransformers, Redis variables

## [0.3.0] - Production Readiness

### Added
- **API Key authentication**: `Authorization: Bearer <key>` or `X-API-Key` header
  - Configurable via `AGENTBASE_API_KEY` environment variable
  - Health and metrics endpoints remain public
  - WebSocket auth via `?token=<key>` query parameter
- **CORS middleware**: Configurable origins via `AGENTBASE_CORS_ORIGINS` (default `*`)
- **Rate limiting**: Per-IP token bucket, 60 requests/minute (configurable)
  - Returns `429 AGENTBASE_RATE_001` when exceeded
- **Global exception handler**: Structured `{"error": "...", "code": "..."}` responses
- **pgvector integration** for production RAG:
  - Native `vector` column type with IVFFlat index
  - SQL `<=>` cosine distance operator for O(log n) retrieval
  - Automatic fallback to in-memory cosine similarity when pgvector unavailable
  - Docker image upgraded to `pgvector/pgvector:pg16`
- **Document upload API** (5 endpoints):
  - `POST /documents/upload` — multipart file upload to knowledge base
  - `GET /documents` — list documents
  - `GET /documents/{id}` — get document
  - `DELETE /documents/{id}` — delete document
  - `POST /documents/search` — search knowledge base
- **WebSocket real-time communication**: `WS /ws/agents/{name}` for bidirectional agent chat
- **Prometheus metrics**: `GET /metrics` endpoint with request counters, error tracking
- **Health endpoint** now reports `auth_enabled` status
- Environment variable overrides for `storage` section (`AGENTBASE_STORAGE__TYPE`, `AGENTBASE_STORAGE__DSN`)
- 29 new tests (21 security + 8 pgvector), total 529 tests, 84% coverage

### Changed
- Docker Compose passes `AGENTBASE_API_KEY`, `AGENTBASE_CORS_ORIGINS`, `AGENTBASE_CHECKPOINTER__DSN`, `AGENTBASE_STORAGE__DSN` to API container
- API version bumped to 0.3.0
- `.env.example` fully documented with all environment variables
- README updated with authentication guide, WebSocket example, file upload example, metrics description
- `configs/default.yaml` DSN credentials unified to `agentbase:agentbase` (matching docker-compose)

### Fixed
- **DSN credential mismatch**: `docker-compose.yml` created `agentbase:agentbase` user but `configs/default.yaml` used `postgres:postgres` — caused connection failure on `docker compose up`
- Docker Compose now uses container hostname `postgres` in DSN (not `127.0.0.1`)
- `FakeModel` test fixture: added `__getattr__` fallback for deepagents internal methods

## [0.2.0] - Full-Stack Backend Scaffold

### Added
- FastAPI service layer (`src/agentbase/api.py`) with 12 endpoints:
  - `GET /health` — health check
  - `GET /agents` — list all agents
  - `GET /agents/{name}` — get agent config
  - `GET /agents/{name}/configurable` — get configurable items (Context Schema)
  - `POST /agents/{name}/invoke` — sync agent invocation
  - `POST /agents/{name}/stream` — SSE streaming
  - `POST /agents/{name}/resume` — resume interrupted agent
  - `POST /queue/submit` — submit async task
  - `GET /queue/{task_id}` — get task status
  - `GET /queue` — list tasks (with filters)
  - `DELETE /queue/{task_id}` — cancel task
  - `POST /queue/process` — process pending tasks
- CLI `agentbase serve` command for starting the API server
- 13 core modules:
  - `core/mcp.py` — MCP (Model Context Protocol) client registry + MCPManager
  - `core/queue.py` — async request queue with Task lifecycle
  - `core/evaluation.py` — agent evaluation framework with metrics
  - `core/tracer.py` — tracing/observability with Span + TraceContext
  - `core/workspace.py` — structured file management (uploads/outputs/workspace)
  - `core/graph.py` — knowledge graph provider registry + RRF fusion
  - `core/skills.py`, `core/memory.py`, `core/knowledge.py`, `core/storage.py`
  - `core/parsers.py`, `core/embeddings.py`, `core/search.py`
- 9 pluggable provider registries with zero-config defaults
- PostgreSQL as default storage backend (with SQLite SQL dialect auto-conversion)
- `PostgresBackend._convert_sql()` — auto-converts `AUTOINCREMENT` to `SERIAL`
- MCP tools: `mcp_list_tools`, `mcp_call_tool`
- Summary middleware with L1/L2 conversation compaction
- Document parsers: PdfParser, DocxParser, HtmlParser, ExcelParser
- `OpenAIEmbeddingProvider` — auto-registered when `openai` package is installed
- `AgentConfig.get_configurable_items()` — Context Schema for form generation
- `AgentConfig.capabilities` field for agent capability flags
- Config schema: `MCPConfig`, `QueueConfig`, `TracerConfig`
- Dockerfile for containerized deployment
- docker-compose.yml with PostgreSQL + API services
- `.env.example` with all environment variables
- `docs/quickstart.md` — 11-step end-to-end guide
- Environment variable overrides for `storage` section (`AGENTBASE_STORAGE__TYPE`, etc.)
- 491 unit tests + 7 smoke/integration tests (498 total), 80% coverage
- `--cov-fail-under=60` CI coverage gate

### Changed
- Default storage switched from SQLite to PostgreSQL
- Default checkpointer switched from SQLite to PostgreSQL
- Default embedding provider changed from `none` to `hash` (vector search enabled)
- Default model switched to `deepseek-chat` via DeepSeek API
- `AgentFactory` now injects `mcp_manager`, `workspace_manager`, `tracer`, `queue` into context
- `docker-compose.yml` now includes both PostgreSQL and API services
- CI now installs `[dev,api,postgres,openai,rag]` extras
- README completely rewritten with API server docs, Docker deployment, and full tool list

### Fixed
- PostgreSQL SQL dialect compatibility (`AUTOINCREMENT` → `SERIAL`)
- DSN uses `127.0.0.1` instead of `localhost` to avoid IPv6 timeout
- `mock_model` fixture: added `bind_tools()`, `count()`, `__getattr__` fallback
- Smoke tests now override storage/checkpointer to SQLite/memory via env vars
- Integration test validates config loading without requiring real model assembly
- `ParserRegistry.register()` deduplicates extensions within a single parser

## [0.1.0] - MVP

### Added
- Error code system (`agentbase_<domain>_<nnn>`) on all `agentbaseError` subclasses
- Structured JSON logging with secret redaction
- Extension metadata infrastructure (`ExtensionMeta`)
- File system tools: `read_file`, `write_file`, `grep` with workspace boundary protection
- Timezone tool: `now_local` with ISO 8601 output
- Middleware: `retry` and `timeout`
- Subagent: `researcher` for research and summarization
- Multi-agent configurations: `coder`, `researcher`, `interrupt_demo`
- Doctor health check with 14 checks
- Interrupt/resume flow
- Test suite (67 tests)
- CI/CD: lint, test, build, docs-check, audit workflows
- Release workflow triggered by `v*` tags
