# Extension Development Guide

> **AgentBase** — a configuration-driven AI Agent backend / LLM agent framework / 智能体脚手架. This document explains how to build tools, middleware, subagents, and parsers.

**Documentation index:** [README](../README.md) · [Quick Start](quickstart.md) · [Configuration](configuration.md) · [Core Services](core-services.md) · [Error Codes](error-codes.md) · [Backend Boundaries](backend-boundaries.md) · [Project Positioning](project-positioning.md)

`agentbase` supports four extension types: **tools**, **middleware**, **subagents**, and **parsers**. All extensions are registered via decorators and automatically discovered from configured modules.

## Extension Types

| Type | Registry | Decorator | Description |
|------|----------|-----------|-------------|
| Tool | `tool_registry` | `@register_tool` | LangChain tool callable by agents |
| Middleware | `middleware_registry` | `@register_middleware` | Model call wrapper |
| Subagent | `subagent_registry` | `@register_subagent` | Delegated sub-agent spec |
| Parser | `parser_registry` | `@register_parser` | Document file-to-text parser |

## ExtensionMeta

Every extension should declare metadata via `ExtensionMeta`:

```python
from agentbase.extensions._meta import ExtensionMeta

meta = ExtensionMeta(
    name="my_tool",
    kind="tool",
    description="Does something useful.",  # <= 80 chars
    requires_context=["workspace_dir"],    # context keys needed
    default_enabled=False,
)
```

## Creating a Tool

```python
from __future__ import annotations
from typing import Any
from langchain_core.tools import tool
from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.tools import register_tool

_META = ExtensionMeta(
    name="read_file", kind="tool",
    description="Read a file within workspace.",
    requires_context=["workspace_dir"],
)

@register_tool("read_file", meta=_META)
def build_read_file_tool(context: dict[str, Any] | None = None):
    workspace_path = context["workspace_dir"]

    @tool
    def read_file(path: str) -> str:
        """Read the contents of a file within the workspace."""
        target = (workspace_path / path).resolve()
        return target.read_text(encoding="utf-8")

    return read_file
```

## Creating Middleware

```python
from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.middleware import register_middleware

_META = ExtensionMeta(
    name="retry", kind="middleware",
    description="Retry model calls up to max_attempts.",
    requires_context=["agent_config"],
)

@register_middleware("retry", meta=_META)
def build_retry(context: dict[str, Any] | None = None):
    # Return a list of middleware callables or []
    return []
```

## Built-in Middleware (9)

| Middleware | Description |
|------------|-------------|
| `request_logger` | Log model call requests and responses with duration |
| `retry` | Retry failed model calls with exponential backoff + jitter |
| `timeout` | Enforce timeout on model calls |
| `summary` | L1/L2 conversation compaction (compresses history when threshold exceeded) |
| `cache` | Cache identical model calls (TTL + LRU eviction) |
| `redact_output` | Redact PII/secrets from model response content (default disabled) |
| `rate_limit` | Per-agent/global model call rate limiting (default disabled) |
| `model_router` | Multi-model routing: round_robin/weighted/random/failover (default disabled) |
| `audit_log` | Audit event recording for model calls (default disabled) |

## Context Keys

The `context` dict passed to builders contains:

| Key | Type | Description |
|-----|------|-------------|
| `root_dir` | `Path` | Project root directory |
| `app_config` | `AppConfig` | Application configuration |
| `agent_config` | `AgentConfig` | Current agent configuration |
| `workspace_dir` | `Path` | Workspace directory path |
| `skill_manager` | `SkillManager` | Skill CRUD manager |
| `memory_manager` | `MemoryManager` | Memory persistence manager |
| `knowledge_base` | `KnowledgeBase` | Knowledge base with chunking & search |
| `search_provider` | `SearchProvider \| None` | Web search provider (if configured) |
| `mcp_manager` | `MCPManager \| None` | MCP server manager (if configured) |
| `workspace_manager` | `WorkspaceManager` | Structured file management |
| `tracer` | `TracerProvider` | Tracing provider |
| `queue` | `RequestQueue \| None` | Async request queue (if configured) |

## Auto-Discovery

Extensions are auto-discovered from modules listed in `configs/default.yaml`:

```yaml
extensions:
  autodiscover:
    - agentbase.extensions.tools
    - agentbase.extensions.middleware
    - agentbase.extensions.subagents
  extra_modules:
    - my_custom_extensions.tools
```

Document parsers (`agentbase.extensions.parsers`) are loaded during bootstrap automatically.

## Built-in Tools (37)

| Tool | Description |
|------|-------------|
| `echo` | Echo text back |
| `get_time` / `now_local` | Current UTC/local timestamp |
| `read_file` / `write_file` / `grep` / `list_workspace` | File operations |
| `skill_list` / `skill_get` / `skill_create` / `skill_update` / `skill_delete` / `skill_search` | Skill CRUD |
| `memory_save` / `memory_get` / `memory_list` / `memory_search` / `memory_delete` / `memory_count` / `memory_batch_save` | Memory CRUD + count + batch save |
| `kb_add` / `kb_get` / `kb_list` / `kb_search` / `kb_update` / `kb_delete` / `kb_ingest` / `kb_batch_ingest` | Knowledge base |
| `web_search` / `web_fetch` | Web search and fetch |
| `http_request` | Make HTTP requests (GET/POST/PUT/PATCH/DELETE) with timeout, redirect limits, and structured response |
| `db_query` | Execute read-only SELECT queries with table whitelist, row limit, and timeout (default disabled) |
| `mcp_list_tools` / `mcp_call_tool` | MCP server tools |
| `code_execute` | Execute Python code in a sandboxed subprocess |
| `transcribe` | Transcribe audio/video to text (Whisper API/local) |
| `email_sender` | Send SMTP email (text/HTML, multi-recipient, SSL/TLS) |

## Built-in Document Parsers (9)

| Parser | Extensions | Dependency |
|--------|-----------|------------|
| `TextParser` | .txt, .csv, .json, .yaml, .py, .js, .ts, .sql, .sh... | None |
| `MarkdownParser` | .md, .markdown, .rst | None |
| `PdfParser` | .pdf | `pymupdf` (`pip install agentbase[rag]`) |
| `DocxParser` | .docx | `python-docx` (`pip install agentbase[rag]`) |
| `HtmlParser` | .html, .htm | `beautifulsoup4` |
| `ExcelParser` | .xlsx, .xls | `openpyxl` |
| `PptxParser` | .pptx, .ppt | `python-pptx` (`pip install agentbase[rag]`) |
| `LLMDocumentParser` | any (virtual) | `openai` — LLM API converts to structured Markdown |
| `OCRParser` | any (virtual) | `pytesseract` + `pillow` + `pdf2image` (`pip install agentbase[ocr]`) |

## Pluggable Provider Registries (22)

Beyond tools/middleware/subagents, `agentbase` provides 22 pluggable provider registries:

| Registry | Default | How to Replace |
|----------|---------|----------------|
| `parser_registry` | TextParser, MarkdownParser | `@register_parser(".pdf")` |
| `embedding_registry` | NoneEmbeddingProvider (disabled) / HashEmbedding (testing) | `@register_embedding_provider("openai")` |
| `search_registry` | None (disabled) / DuckDuckGoSearch | `@register_search_provider("tavily")` |
| `mcp_registry` | None (disabled) / MemoryMCPClient | `@register_mcp_client("my_server")` |
| `queue_registry` | None (sync) / MemoryRequestQueue | `@register_queue_provider("redis")` |
| `tracer_registry` | NullTracer | `@register_tracer_provider("langfuse")` |
| `graph_registry` | NullGraphProvider | `@register_graph_provider("neo4j")` |
| StorageBackend | SQLite / PostgreSQL / MySQL / MongoDB | Config: `storage.type` |
| Checkpointer | Memory / SQLite / PostgreSQL / MySQL | Config: `checkpointer.type` |
| `model_registry` | InMemoryModelProvider / NullModelProvider | `@register_model_provider("name")` |
| `prompt_registry` | InMemoryPromptProvider / NullPromptProvider | `@register_prompt_provider("name")` |
| `user_registry` | InMemoryUserProvider / NullUserProvider | `@register_user_provider("name")` |
| `apikey_registry` | InMemoryApiKeyProvider / NullApiKeyProvider | `@register_apikey_provider("name")` |
| `usage_registry` | InMemoryUsageProvider / NullUsageProvider | `@register_usage_provider("name")` |
| `webhook_registry` | InMemoryWebhookProvider / NullWebhookProvider | `@register_webhook_provider("name")` |
| `feedback_registry` | InMemoryFeedbackProvider / NullFeedbackProvider | `@register_feedback_provider("name")` |
| `notification_registry` | InMemoryNotificationProvider / NullNotificationProvider | `@register_notification_provider("name")` |
| `conversation_registry` | InMemoryConversationProvider / NullConversationProvider | `@register_conversation_provider("name")` |
| `audit_registry` | SQLiteAuditProvider / NullAuditProvider | `@register_audit_provider("name")` |
| `experiment_registry` | InMemoryExperimentProvider / NullExperimentProvider | `@register_experiment_provider("name")` |
| `redaction_registry` | RuleRedactionProvider / NullRedactionProvider | `@register_redaction_provider("name")` |
| `secrets_registry` | FernetSecretsProvider / NullSecretsProvider | `@register_secrets_provider("name")` |

## Interrupt and Resume

```yaml
# configs/agents/interrupt_demo.yaml
interrupt_on:
  tool_call:
    - write_file
```

```bash
agentbase resume --thread-id <id> --decision approve
agentbase resume --thread-id <id> --decision edit
```

Via API:

```bash
curl -X POST http://localhost:8000/agents/default/resume \
  -H "Content-Type: application/json" \
  -d '{"thread_id": "...", "decision": "approve"}'
```
