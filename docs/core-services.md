# Core Services & Pluggable Providers

> **AgentBase** — a configuration-driven AI Agent backend / LLM agent framework / 智能体脚手架. This document covers the core services and their pluggable providers.

**Documentation index:** [README](../README.md) · [Quick Start](quickstart.md) · [Configuration](configuration.md) · [Extensions](extensions.md) · [Error Codes](error-codes.md) · [Backend Boundaries](backend-boundaries.md) · [Project Positioning](project-positioning.md)

`agentbase` separates **management** (CRUD operations) from **implementation** (how data is stored, parsed, embedded, searched). Management is built-in; implementation is pluggable with zero-config defaults.

## Architecture

```
Management Layer (built-in)          Implementation Layer (pluggable)
─────────────────────────            ────────────────────────────────
SkillManager          ───────►       Markdown files (default)
MemoryManager         ───────►       SQLite / PostgreSQL / MySQL / MongoDB
KnowledgeBase         ───────►       Storage + Parsers + Embeddings
MCPManager            ───────►       MCP clients (memory / custom)
WorkspaceManager      ───────►       Filesystem (uploads/outputs/workspace)
EvaluationRunner      ───────►       Metric calculators
ModelManager          ───────►       In-memory / custom (model configs)
PromptManager         ───────►       In-memory / custom (prompt templates)
UserManager           ───────►       In-memory / custom (users + auth)
OAuth2Manager         ───────►       Google / GitHub (third-party login)
ApiKeyManager         ───────►       In-memory / custom (API keys + revocation)
UsageManager         ───────►       In-memory / custom (token usage + cost)
WebhookManager        ───────►       In-memory / custom (event notifications)
FeedbackManager       ───────►       In-memory / custom (user feedback + ratings)
NotificationManager   ───────►       In-memory / custom (in-app notifications)
ConversationManager   ───────►       In-memory / custom (conversation history)
AuditManager          ───────►       In-memory / SQLite / PostgreSQL (audit log)
MigrationManager      ───────►       Alembic (database schema migration)
                                      ↓
                            ┌─────────────────────────┐
                            │ ParserRegistry          │ → txt, md, pdf, docx, html, xlsx, pptx, LLM, OCR
                            │ EmbeddingRegistry       │ → none (default) / hash (zero-dep) / openai / sentence-transformers
                            │ SearchRegistry           │ → none (default) / duckduckgo / tavily / custom
                            │ StorageBackend          │ → sqlite / postgres / mysql / mongodb (DSN switch)
                            │ MCPRegistry             │ → none (default) / memory / custom
                            │ QueueRegistry           │ → memory / redis / celery / custom
                            │ TracerRegistry          │ → null / memory / langfuse / opentelemetry / custom
                            │ GraphRegistry           │ → null / memory / neo4j / custom
                            └─────────────────────────┘
```

## 1. Storage Backend

**Default**: SQLite (zero-config, file-based, code default)
**Production**: PostgreSQL (via Docker Compose)

```yaml
storage:
  type: postgres
  dsn: postgresql://agentbase:agentbase@127.0.0.1:5432/agentbase
```

The `PostgresBackend` auto-converts SQLite-style SQL (`AUTOINCREMENT` → `SERIAL`),
so upper-layer code uses a unified SQL style for both backends.

## 2. Document Parser Registry

**Built-in**: `TextParser`, `MarkdownParser`, `PdfParser`, `DocxParser`, `HtmlParser`, `ExcelParser`, `PptxParser`, `LLMDocumentParser`, `OCRParser`

PDF/DOCX/HTML/Excel parsers are registered at bootstrap time. They require optional
dependencies (`pymupdf`, `python-docx`, `beautifulsoup4`, `openpyxl`) — install with
`pip install agentbase[rag]`.

## 3. Embedding Provider Registry

**Default**: `NoneEmbeddingProvider` (embeddings disabled, text search only)
**Built-in**: `HashEmbedding` (zero-dependency, testing), `OpenAIEmbeddingProvider` (auto-registered when `openai` package is installed), `SentenceTransformersEmbeddingProvider` (local models)

Embeddings are **persisted** to the `kb_chunks.embedding` column on document insert.
Search loads stored vectors instead of recomputing.

## 4. Web Search Provider Registry

**Default**: `None` (disabled)
**Built-in**: `DuckDuckGoSearch` (no API key, rate-limited), `TavilySearch` (requires API key)

Register custom providers with `@register_search_provider("name")`.

## 5. MCP (Model Context Protocol) Registry

**Default**: `None` (disabled)
**Built-in**: `MemoryMCPClient` (in-memory, for testing)

```python
from agentbase.core.mcp import register_mcp_client

@register_mcp_client("my_server")
class MyMCPClient:
    def connect(self): ...
    def list_tools(self): return [...]
    def call_tool(self, name, args): return MCPToolResult(content="...")
```

The `MCPManager` aggregates tools from all connected servers. Agents access them
via `mcp_list_tools` and `mcp_call_tool` tools.

## 6. Queue Registry

**Default**: `none` (sync mode, no queue)
**Built-in**: `MemoryRequestQueue` (in-process), `RedisRequestQueue` (persistent), `CeleryRequestQueue` (distributed)

Submit async tasks, check status, and process them with a handler function.

```python
from agentbase.core.queue import MemoryRequestQueue

queue = MemoryRequestQueue()
task = queue.submit(agent_name="default", message="background job")
result = queue.process_one(lambda t: {"output": "done"})
```

## 7. Tracer Registry

**Default**: `NullTracer` (no-op, zero overhead)
**Built-in**: `InMemoryTracer` (stores spans for inspection)

```python
from agentbase.core.tracer import trace, InMemoryTracer

tracer = InMemoryTracer()
with trace(tracer, "model_call", agent="default") as span:
    span.add_event("api_called")
    # ... do work ...
# span.status == "ok"
```

Register custom providers (Langfuse, OpenTelemetry) with `@register_tracer_provider`.

## 8. Graph Registry

**Default**: `NullGraphProvider` (no-op)
**Built-in**: `InMemoryGraphProvider` (entity/relation CRUD + subgraph traversal), `Neo4jGraphProvider` (Cypher queries)

```python
from agentbase.core.graph import InMemoryGraphProvider, Entity, Relation

provider = InMemoryGraphProvider()
provider.add_entity(Entity(name="Python", label="Language"))
provider.add_relation(Relation(source_entity="e_0", target_entity="e_1", relation_type="has_framework"))
results = provider.search_entities("Python")
```

RRF (Reciprocal Rank Fusion) is available via `fuse_results_rrf()` to merge
vector search and graph search results.

## 9. Workspace Manager

Structured file management with three-tier directory separation:

```
workspace/
├── workspace/     # persistent, shared across sessions
├── uploads/       # per-session user uploads
└── outputs/       # per-session agent outputs
```

Path traversal protection is enforced — no file can escape its directory.

## 10. Evaluation Framework

Run test cases against an agent function and compute metrics:

```python
from agentbase.core.evaluation import EvaluationRunner, EvalCase

runner = EvaluationRunner()
cases = [EvalCase(query="What is 2+2?", expected="4", expected_keywords=["4"])]
report = runner.evaluate(cases, lambda q: "The answer is 4")
print(report.pass_rate)  # 1.0
```

Built-in metrics: `KeywordMatchMetric`, `ExactMatchMetric`, `SubstringMatchMetric`, `LLMJudgeMetric`, `BLEUMetric`, `ROUGEMetric`.

## Summary Table

| Service | Default | Pluggable | Config Key |
|---------|---------|-----------|------------|
| Storage | sqlite | `SQLiteBackend` / `PostgresBackend` / `MySQLBackend` / `MongoDBBackend` | `storage` |
| Parsers | txt, md, pdf, docx, html, xlsx, pptx, LLM, OCR | `@register_parser()` | — (code) |
| Embeddings | none | `@register_embedding_provider()` | `embedding` |
| Web Search | none | `@register_search_provider()` | `web_search` |
| MCP | none | `@register_mcp_client()` | `mcp` |
| Queue | none (sync) | `@register_queue_provider()` — memory / redis / celery | `queue` |
| Tracer | null | `@register_tracer_provider()` | `tracer` |
| Graph | null | `@register_graph_provider()` | — (code) |
| Checkpointer | sqlite | `MemorySaver` / `SqliteSaver` / `PostgresSaver` / `MySQLSaver` | `checkpointer` |
| Skills | files | No (always file-based) | — |
| Memory | sqlite (via storage) | Via storage backend | `storage` |
| Knowledge | sqlite (via storage) | Via storage + parsers + embeddings | `storage` + `embedding` |
| Workspace | filesystem | `WorkspaceManager` | — |
| Evaluation | built-in | `Metric` protocol | — |
| Model Manager | null | `@register_model_provider()` | `model_manager` |
| Prompt Manager | null | `@register_prompt_provider()` | `prompt_manager` |
| User Manager | null | `@register_user_provider()` | `user_manager` |
| API Key Manager | null | `@register_apikey_provider()` | `apikey_manager` |
| Audit Log | null | `@register_audit_provider()` | `audit` |
| Migration | Alembic | No (uses Alembic internally) | `migration` |
| OAuth2 | Google / GitHub | No (built-in providers) | `oauth2` |
| Usage Tracking | In-memory | `@register_usage_provider()` | `usage` |
| Webhook | In-memory | `@register_webhook_provider()` | `webhook` |
| Feedback | In-memory | `@register_feedback_provider()` | `feedback` |
| Notification | In-memory | `@register_notification_provider()` | `notification` |
| Conversation | In-memory | `@register_conversation_provider()` | `conversation` |
