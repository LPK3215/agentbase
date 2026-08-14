# Changelog

## [Unreleased]

### Added
- **Model management service (ModelProvider)**: Multi-model CRUD and connectivity testing. New `core/model_manager.py` with `ModelProvider` Protocol, `InMemoryModelProvider` (default), `NullModelProvider` (disabled), `ModelManager` wrapper with `test()` method, and `@register_model_provider` decorator for custom providers. 6 new API endpoints: `GET /models`, `POST /models`, `GET /models/{name}`, `PATCH /models/{name}`, `DELETE /models/{name}`, `POST /models/{name}/test`. Config: `model_manager.enabled` + `model_manager.provider`. Added `tests/unit/test_core_model_manager.py` (39 tests: CRUD, null provider, registry, singleton, concurrency, protocol compliance). Total routes: 33 → 39, total tests: 1588 → 1627.
- **Prompt template management service (PromptProvider)**: Prompt template CRUD and variable rendering. New `core/prompt.py` with `PromptProvider` Protocol, `InMemoryPromptProvider` (default), `NullPromptProvider` (disabled), `PromptManager` wrapper with `render()` and `extract_variables()` methods, and `@register_prompt_provider` decorator for custom providers. 6 new API endpoints: `GET /prompts`, `POST /prompts`, `GET /prompts/{name}`, `PATCH /prompts/{name}`, `DELETE /prompts/{name}`, `POST /prompts/{name}/render`. Config: `prompt_manager.enabled` + `prompt_manager.provider`. Added `tests/unit/test_core_prompt.py` (55 tests: CRUD, null provider, render, variable extraction, registry, singleton, concurrency, protocol compliance). Total routes: 39 → 45, total tests: 1627 → 1682.

### Fixed
- **`code_execute` tool could never be assembled**: the tool function was registered directly via `@register_tool` instead of a builder function, violating the tool-factory contract (`builder(context)`). Assembly raised `TypeError` twice and the tool was silently skipped (`skip_on_error=True`). Fixed by registering `build_code_execute_tool()` which wraps the sandbox function in a LangChain `@tool`, following the same pattern as all other 36 tools. Added `tests/unit/test_tool_code_execute.py` (8 tests: factory assembly, real subprocess execution, timeout/size limits, error paths).
- **JWT hardcoded default secret (P0 security)**: `AuthConfig.secret` and `JWTAuth.__init__` both defaulted to `"agentbase-default-secret"` — anyone knowing this value could forge JWT tokens when `auth.type: jwt` was configured without an explicit secret. Fixed by: (1) defaulting `secret` to empty string in both `AuthConfig` and `JWTAuth`; (2) `_get_jwt_auth()` now raises `ConfigError` (`AGENTBASE_CONFIG_002`) on empty secret when `type=jwt` (fail-fast); (3) `JWTAuth` generates a random ephemeral secret as last-resort fail-safe (with warning log); (4) `docker-compose.yml` removed the insecure fallback. Added `tests/unit/test_jwt_security.py` (11 tests).
- **API key timing side-channel (P1 security)**: `_verify_api_key()` and `_verify_auth()` used Python `==` for API key comparison, which short-circuits on the first differing byte — enabling timing attacks to recover the key byte-by-byte. Fixed by replacing all 3 comparison sites with `hmac.compare_digest()` (constant-time comparison). Added 6 direct unit tests in `test_api_security.py::TestApiKeyConstantTime`.
- **CORS wildcard + credentials (P1 security)**: `CORSMiddleware` was hardcoded with `allow_credentials=True` while defaulting to `allow_origins=["*"]` — a combination forbidden by the CORS spec that causes browsers to reflect arbitrary Origin headers, effectively disabling CORS protection. Fixed by: (1) `allow_credentials` is now dynamically `False` when origins contain `*`; (2) `CORSConfig.allow_credentials` default changed from `True` to `False`; (3) added `effective_credentials()` method to schema. Added 6 tests (2 HTTP-level + 4 schema unit).
- **Stream semaphore bypass (P1 concurrency)**: `AgentRunner.stream()` held the `threading.Semaphore` only around `agent.stream()` (iterator creation) and released it before iterating — meaning `max_concurrency` was effectively bypassed for all streaming requests. Fixed by moving the semaphore to `acquire()` before iteration and `release()` in a `finally` block, so it covers the entire stream consumption. Added 2 concurrency tests (threading-based blocking verification + error-release verification).

### Docs
- README metrics corrected to match code reality: 32 → 37 built-in tools, 21 → 33 API routes, 520 → 1563 tests, 65% → 74% coverage

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
