from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class AppInfo(BaseModel):
    name: str = "agentbase"
    env: str = "dev"
    log_level: str = "INFO"
    version: str = "0.4.0"


class ModelConfig(BaseModel):
    provider: str = "openai"
    name: str = "gpt-4.1-mini"
    temperature: float = 0.0
    max_tokens: int | None = None
    timeout_seconds: int = 120
    base_url: str | None = None
    api_key_env: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("temperature")
    @classmethod
    def _clamp_temperature(cls, v: float) -> float:
        """Clamp temperature to [0.0, 2.0]."""
        if v < 0.0:
            return 0.0
        if v > 2.0:
            return 2.0
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def _validate_timeout(cls, v: int) -> int:
        """Ensure timeout is positive."""
        if v < 1:
            return 120
        return v

    @field_validator("max_tokens")
    @classmethod
    def _validate_max_tokens(cls, v: int | None) -> int | None:
        """Ensure max_tokens is positive if set."""
        if v is not None and v < 1:
            return None
        return v

    @property
    def model_string(self) -> str:
        if ":" in self.name and not self.provider:
            return self.name
        if ":" in self.name:
            return self.name
        return f"{self.provider}:{self.name}"


class BackendConfig(BaseModel):
    type: str = "filesystem"
    root_dir: str = "workspace"
    options: dict[str, Any] = Field(default_factory=dict)


class CheckpointerConfig(BaseModel):
    type: str = "sqlite"
    dsn: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class RuntimeConfig(BaseModel):
    default_agent: str = "default"
    config_dir: str = "configs"
    workspace_dir: str = "workspace"
    stream_modes: list[str] = Field(default_factory=lambda: ["messages", "updates"])
    recursion_limit: int = 50
    max_concurrency: int = 4

    @field_validator("recursion_limit")
    @classmethod
    def _validate_recursion_limit(cls, v: int) -> int:
        """Ensure recursion limit is positive and reasonable."""
        if v < 1:
            return 50
        if v > 200:
            return 200
        return v

    @field_validator("max_concurrency")
    @classmethod
    def _validate_max_concurrency(cls, v: int) -> int:
        """Ensure max concurrency is positive and reasonable."""
        if v < 1:
            return 4
        if v > 32:
            return 32
        return v


class StorageConfig(BaseModel):
    """Storage backend selection for MemoryManager and KnowledgeBase.

    - ``type = sqlite``  (default) → file-based, zero-config, dev/single-user
    - ``type = postgres``           → PostgreSQL, requires ``dsn``, prod/multi-user
    - ``type = mysql``              → MySQL, requires ``dsn``, prod/multi-user

    When ``type = sqlite``, ``db_dir`` controls where the .db files live.
    When ``type = postgres`` or ``mysql``, ``dsn`` is the connection string.
    """

    type: Literal["sqlite", "postgres", "mysql"] = "sqlite"
    db_dir: str = "data"
    dsn: str | None = None


class EmbeddingConfig(BaseModel):
    """Embedding provider for RAG vector search.

    - ``provider = hash``   (default) → zero-dependency hash embeddings (testing)
    - ``provider = openai``           → OpenAI text-embedding (requires API key)
    - ``provider = none``             → disable embeddings, use text search only

    Register custom providers with ``@register_embedding_provider("name")``.
    """

    provider: str = "none"
    options: dict[str, Any] = Field(default_factory=dict)


class WebSearchConfig(BaseModel):
    """Web search provider for internet search.

    - ``provider = duckduckgo`` (default) → no API key needed (rate-limited)
    - ``provider = none``                 → disable web search

    Register custom providers with ``@register_search_provider("name")``.
    """

    provider: str = "none"
    options: dict[str, Any] = Field(default_factory=dict)


class AuthConfig(BaseModel):
    """Authentication and authorization configuration.

    - ``type = api_key`` (default) → simple Bearer / X-API-Key auth
    - ``type = jwt``                → JWT with RBAC roles
    - ``type = none``               → auth disabled (dev mode)

    When ``type = jwt``, set ``secret`` and optional ``token_expiry_hours``.
    Role-permission mapping can be customised via ``role_permissions``.
    """

    type: Literal["api_key", "jwt", "none"] = "api_key"
    secret: str = "agentbase-default-secret"
    token_expiry_hours: int = 24
    role_permissions: dict[str, list[str]] = Field(default_factory=dict)
    api_key: str | None = None  # if set, overrides env-based API key


class RateLimitConfig(BaseModel):
    """Rate limiting configuration.

    - ``enabled = true`` (default) → per-IP token bucket
    - ``max_requests`` per ``window_seconds`` per IP
    - ``burst`` allows short bursts above the steady-state rate
    """

    enabled: bool = True
    max_requests: int = 60
    window_seconds: int = 60
    burst: int = 10

    @field_validator("max_requests")
    @classmethod
    def _validate_max_requests(cls, v: int) -> int:
        """Ensure max_requests is positive."""
        return max(v, 1)

    @field_validator("window_seconds")
    @classmethod
    def _validate_window(cls, v: int) -> int:
        """Ensure window_seconds is positive."""
        return max(v, 1)

    @field_validator("burst")
    @classmethod
    def _validate_burst(cls, v: int) -> int:
        """Ensure burst is non-negative."""
        return max(v, 0)


class CORSConfig(BaseModel):
    """CORS (Cross-Origin Resource Sharing) configuration.

    - ``allow_origins``: comma-separated list of allowed origins (default ``*``)
    - ``allow_methods``: list of allowed HTTP methods (default all)
    - ``allow_headers``: list of allowed headers (default all)
    - ``allow_credentials``: whether to send credentials (default true)
    """

    allow_origins: list[str] = Field(default_factory=lambda: ["*"])
    allow_methods: list[str] = Field(default_factory=lambda: ["*"])
    allow_headers: list[str] = Field(default_factory=lambda: ["*"])
    allow_credentials: bool = True


class MetricsConfig(BaseModel):
    """Prometheus metrics configuration.

    - ``enabled = true`` (default) → expose ``/metrics`` endpoint
    - ``path``: metrics endpoint path (default ``/metrics``)
    - ``collect_latency``: record request latency histogram
    - ``collect_agent_metrics``: record per-agent invocation counts
    """

    enabled: bool = True
    path: str = "/metrics"
    collect_latency: bool = True
    collect_agent_metrics: bool = True


class HealthCheckConfig(BaseModel):
    """Health check configuration.

    - ``check_storage``: verify storage backend connectivity
    - ``check_queue``: verify queue provider connectivity
    - ``check_tracer``: verify tracer provider connectivity
    """

    check_storage: bool = True
    check_queue: bool = True
    check_tracer: bool = False


class ExtensionsConfig(BaseModel):
    autodiscover: list[str] = Field(
        default_factory=lambda: [
            "agentbase.extensions.tools",
            "agentbase.extensions.middleware",
            "agentbase.extensions.subagents",
        ]
    )
    extra_modules: list[str] = Field(default_factory=list)


class AuditConfig(BaseModel):
    """Audit log configuration.

    - ``enabled = false`` (default) → audit logging disabled (NullAuditProvider)
    - ``enabled = true``             → records structured audit events
    - ``provider = sqlite`` (default) → SQLite audit table
    - ``db_dir``: directory for SQLite audit database (default ``data``)
    - ``dsn``: PostgreSQL/MySQL connection string (optional, overrides db_dir)
    - Register custom providers with ``@register_audit_provider``.
    """

    enabled: bool = False
    provider: str = "sqlite"
    db_dir: str = "data"
    dsn: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class RedactionConfig(BaseModel):
    """Sensitive information redaction configuration.

    - ``enabled = false`` (default) → redaction disabled (NullRedactionProvider)
    - ``enabled = true``             → masks PII/secrets in text
    - ``provider = regex`` (default) → pure-regex, zero-dependency provider
    - ``options``: extra kwargs passed to the provider factory
    - Register custom providers with ``@register_redaction_provider``.
    """

    enabled: bool = False
    provider: str = "regex"
    options: dict[str, Any] = Field(default_factory=dict)


class MCPConfig(BaseModel):
    """MCP (Model Context Protocol) server configuration.

    - ``provider = none`` (default) → MCP disabled
    - ``provider = memory``         → in-memory MCP client for testing
    - Register custom clients with ``@register_mcp_client``

    Each server in ``servers`` is a dict with ``name``, ``type``, and ``options``.
    """

    provider: str = "none"
    servers: list[dict[str, Any]] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)


class QueueConfig(BaseModel):
    """Async request queue configuration.

    - ``provider = none`` (default)   → sync mode, no queue
    - ``provider = memory``           → in-process queue
    - Register custom providers with ``@register_queue_provider``
    """

    provider: str = "none"
    options: dict[str, Any] = Field(default_factory=dict)


class TracerConfig(BaseModel):
    """Tracing / observability configuration.

    - ``provider = null`` (default)  → no-op tracer, zero overhead
    - ``provider = memory``          → in-memory tracer for testing
    - ``provider = langfuse``        → Langfuse (requires langfuse package)
    - Register custom providers with ``@register_tracer_provider``
    """

    provider: str = "null"
    options: dict[str, Any] = Field(default_factory=dict)


class AppConfig(BaseModel):
    app: AppInfo = Field(default_factory=AppInfo)
    model: ModelConfig = Field(default_factory=ModelConfig)
    backend: BackendConfig = Field(default_factory=BackendConfig)
    checkpointer: CheckpointerConfig = Field(default_factory=CheckpointerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    queue: QueueConfig = Field(default_factory=QueueConfig)
    tracer: TracerConfig = Field(default_factory=TracerConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    cors: CORSConfig = Field(default_factory=CORSConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    health_check: HealthCheckConfig = Field(default_factory=HealthCheckConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    extensions: ExtensionsConfig = Field(default_factory=ExtensionsConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    redaction: RedactionConfig = Field(default_factory=RedactionConfig)


class PermissionRule(BaseModel):
    operations: list[str]
    paths: list[str]
    mode: Literal["allow", "deny", "interrupt"] = "allow"


class AgentModelOverride(BaseModel):
    provider: str | None = None
    name: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: int | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    name: str
    description: str = ""
    model: AgentModelOverride | None = None
    system_prompt: str = "You are a helpful deep agent."
    memory: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    middleware: list[str] = Field(default_factory=list)
    subagents: list[str] = Field(default_factory=list)
    permissions: list[PermissionRule] = Field(default_factory=list)
    interrupt_on: dict[str, Any] = Field(default_factory=dict)
    response_format: Any | None = None
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tools", "middleware", "subagents", "memory", "skills", "capabilities", mode="before")
    @classmethod
    def _ensure_list(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return list(value)

    def get_configurable_items(self) -> list[dict[str, Any]]:
        """Return a list of configurable field descriptors.

        This enables automatic form generation from the schema —
        a single source of truth for both configuration and UI rendering.

        Each item has: ``name``, ``type``, ``default``, ``description``.
        """
        fields_info = [
            ("name", "str", "Agent name (unique identifier)"),
            ("description", "str", "Human-readable description"),
            ("system_prompt", "text", "System prompt for the agent"),
            ("tools", "list[str]", "Enabled tool names"),
            ("middleware", "list[str]", "Enabled middleware names"),
            ("subagents", "list[str]", "Enabled subagent names"),
            ("memory", "list[str]", "Memory file paths"),
            ("skills", "list[str]", "Skill directory paths"),
            ("capabilities", "list[str]", "Agent capability flags"),
            ("interrupt_on", "dict", "Interrupt configuration"),
            ("response_format", "any", "Response format spec"),
            ("metadata", "dict", "Arbitrary metadata (summary config, etc.)"),
        ]
        items: list[dict[str, Any]] = []
        for name, ftype, desc in fields_info:
            field_info = self.__class__.model_fields.get(name)
            default = field_info.get_default() if field_info else None
            items.append({
                "name": name,
                "type": ftype,
                "default": default,
                "description": desc,
            })
        return items
