"""FastAPI service layer for agentbase.

Exposes agent invocation, streaming, resume, queue management, and health
checks over HTTP.  Built directly on top of ``RuntimeContext``.

Security features:
- API Key authentication (via ``Authorization: Bearer <key>`` or ``X-API-Key``)
- JWT authentication with RBAC roles (via ``Authorization: Bearer <jwt>``)
- CORS middleware (configurable origins)
- Rate limiting (per-IP token bucket, configurable)
- Global exception handler (structured error responses)
- Request ID correlation (``X-Request-ID`` header)

Usage::

    # Start the server
    uvicorn agentbase.api:app --reload --port 8000

    # Or programmatically
    import uvicorn
    from agentbase.api import create_app
    uvicorn.run(create_app(), host="0.0.0.0", port=8000)

Authentication::

    # API Key mode (default)
    export AGENTBASE_API_KEY="your-secret-key"

    # JWT mode
    # In config:
    #   auth:
    #     type: jwt
    #     secret: "your-jwt-secret"

Endpoints::

    GET    /health                      — health check (no auth)
    GET    /metrics                     — Prometheus metrics (no auth)
    GET    /agents                      — list available agents (paginated)
    GET    /agents/{name}               — get agent config
    GET    /agents/{name}/configurable  — get configurable items

    POST   /agents/{name}/invoke        — invoke agent (sync)
    POST   /agents/{name}/stream        — stream agent (SSE)
    POST   /agents/{name}/resume        — resume interrupted agent

    POST   /queue/submit                — submit async task
    GET    /queue/{task_id}             — get task status
    GET    /queue                       — list tasks (paginated, filterable)
    DELETE /queue/{task_id}             — cancel task
    POST   /queue/process              — batch process pending tasks

    POST   /documents/upload            — upload file to knowledge base
    GET    /documents                   — list documents (paginated)
    GET    /documents/{id}             — get document detail
    DELETE /documents/{id}             — delete document
    POST   /documents/search           — search knowledge base

    WS     /ws/agents/{name}           — real-time agent communication
"""
from __future__ import annotations

import json
import os
import time
import uuid
from collections import defaultdict
from typing import Any

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from agentbase.bootstrap import build_runtime
from agentbase.runtime.errors import (
    AgentbaseError,
    AuthError,
    ErrorCode,
    NotFoundError,
    QueueError,
    RateLimitError,
    RuntimeExecutionError,
    http_status_for_code,
)

# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #

_PUBLIC_PATHS = {"/health", "/docs", "/redoc", "/openapi.json", "/", "/metrics"}

# Maximum file upload size (bytes): 100 MB
_MAX_UPLOAD_SIZE = 100 * 1024 * 1024

# WebSocket heartbeat interval (seconds)
_WS_HEARTBEAT_INTERVAL = 30.0


# --------------------------------------------------------------------------- #
# Security: API Key + JWT auth                                                #
# --------------------------------------------------------------------------- #


def _get_api_key() -> str | None:
    """Get the configured API key from environment."""
    return os.environ.get("AGENTBASE_API_KEY") or None


def _is_auth_enabled() -> bool:
    """Check if API key authentication is enabled."""
    key = _get_api_key()
    return key is not None and key != ""


def _verify_api_key(request: Request) -> bool:
    """Verify the API key from the Authorization header or X-API-Key."""
    if not _is_auth_enabled():
        return True
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        # If JWT auth is configured, the token may be a JWT
        # The JWT verification is handled separately
        return token == _get_api_key()
    api_key_header = request.headers.get("X-API-Key", "")
    if api_key_header:
        return api_key_header == _get_api_key()
    return False


def _get_jwt_auth(app_config: Any) -> Any | None:
    """Get JWTAuth instance if JWT auth is configured."""
    auth_cfg = getattr(app_config, "auth", None)
    if auth_cfg is None or auth_cfg.type != "jwt":
        return None
    from agentbase.extensions.auth import JWTAuth, DEFAULT_ROLE_PERMISSIONS

    role_perms = auth_cfg.role_permissions or DEFAULT_ROLE_PERMISSIONS
    return JWTAuth(
        secret=auth_cfg.secret,
        token_expiry_hours=auth_cfg.token_expiry_hours,
        role_permissions=role_perms,
    )


def _verify_auth(request: Request, app_config: Any) -> tuple[bool, dict[str, Any] | None]:
    """Verify authentication — returns (success, payload).

    For API Key mode: returns (True, None) if key is valid.
    For JWT mode: returns (True, payload_dict) if token is valid.
    For no-auth mode: returns (True, None).
    """
    jwt_auth = _get_jwt_auth(app_config)
    if jwt_auth is not None:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            payload = jwt_auth.verify_token(token)
            if payload is not None:
                return True, payload
            return False, None
        # Also check X-API-Key as fallback
        api_key = request.headers.get("X-API-Key", "")
        if api_key and api_key == _get_api_key():
            return True, None
        return False, None

    # API Key mode
    if not _is_auth_enabled():
        return True, None
    if _verify_api_key(request):
        return True, None
    return False, None


def _check_rbac(request: Request, payload: dict[str, Any] | None, app_config: Any) -> bool:
    """Check RBAC permissions for the request path."""
    jwt_auth = _get_jwt_auth(app_config)
    if jwt_auth is None or payload is None:
        return True  # No RBAC configured or no JWT payload
    return jwt_auth.check_path_permission(payload, request.method, request.url.path)


# --------------------------------------------------------------------------- #
# Rate Limiter                                                                #
# --------------------------------------------------------------------------- #


class RateLimiter:
    """Per-IP sliding-window rate limiter with burst support.

    Uses a token bucket variant: each IP gets a bucket that refills
    at ``max_requests / window_seconds`` tokens per second, up to
    ``max_requests + burst`` capacity.
    """

    def __init__(
        self,
        max_requests: int = 60,
        window_seconds: int = 60,
        burst: int = 10,
    ) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self.burst = burst
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def check(self, client_ip: str) -> bool:
        """Returns True if request is allowed, False if rate limited."""
        now = time.time()
        bucket = self._buckets[client_ip]
        # Sliding window: remove timestamps outside the window
        self._buckets[client_ip] = [t for t in bucket if now - t < self.window]
        if len(self._buckets[client_ip]) >= self.max_requests + self.burst:
            return False
        self._buckets[client_ip].append(now)
        return True

    def reset(self) -> None:
        """Clear all buckets (for testing)."""
        self._buckets.clear()

    def get_remaining(self, client_ip: str) -> int:
        """Get remaining requests for an IP."""
        now = time.time()
        bucket = self._buckets.get(client_ip, [])
        recent = [t for t in bucket if now - t < self.window]
        return max(0, self.max_requests + self.burst - len(recent))


# --------------------------------------------------------------------------- #
# Metrics Collector                                                          #
# --------------------------------------------------------------------------- #


class MetricsCollector:
    """Collects Prometheus-format metrics for the API.

    Tracks:
    - Total requests (counter)
    - Requests by path (counter, labelled)
    - Requests by status code (counter, labelled)
    - Request latency (histogram with bucket ranges)
    - Agent invocations (counter, labelled by agent name)
    - Documents uploaded (counter)
    - Errors (counter, labelled by error code)
    - Active WebSocket connections (gauge)
    - Queue tasks submitted/completed/failed (counter)
    - Active sessions (gauge)

    Thread-safe via ``threading.Lock`` — all ``record_*`` methods
    are safe to call from concurrent request handlers.
    """

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self._requests_total = 0
        self._requests_by_path: dict[str, int] = defaultdict(int)
        self._requests_by_status: dict[int, int] = defaultdict(int)
        self._agent_invocations: dict[str, int] = defaultdict(int)
        self._documents_uploaded_total = 0
        self._errors_total = 0
        self._errors_by_code: dict[str, int] = defaultdict(int)
        self._ws_active_connections = 0
        self._queue_submitted = 0
        self._queue_completed = 0
        self._queue_failed = 0
        self._active_sessions = 0
        # Latency histogram buckets (in milliseconds)
        self._latency_buckets = [5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000]
        self._latency_counts: dict[float, int] = {b: 0 for b in self._latency_buckets}
        self._latency_sum = 0.0
        self._latency_count = 0

    def record_request(self, path: str, status_code: int, latency_ms: float) -> None:
        with self._lock:
            self._requests_total += 1
            self._requests_by_path[path] += 1
            self._requests_by_status[status_code] += 1
            self._latency_sum += latency_ms
            self._latency_count += 1
            # Fix: increment ALL buckets that are >= the latency value
            # (not just the first/break after one)
            for bucket in self._latency_buckets:
                if latency_ms <= bucket:
                    self._latency_counts[bucket] += 1

    def record_agent_invocation(self, agent_name: str) -> None:
        with self._lock:
            self._agent_invocations[agent_name] += 1

    def record_upload(self) -> None:
        with self._lock:
            self._documents_uploaded_total += 1

    def record_error(self, error_code: str) -> None:
        with self._lock:
            self._errors_total += 1
            self._errors_by_code[error_code] += 1

    def record_queue_submit(self) -> None:
        with self._lock:
            self._queue_submitted += 1

    def record_queue_complete(self) -> None:
        with self._lock:
            self._queue_completed += 1

    def record_queue_fail(self) -> None:
        with self._lock:
            self._queue_failed += 1

    def ws_connect(self) -> None:
        with self._lock:
            self._ws_active_connections += 1

    def ws_disconnect(self) -> None:
        with self._lock:
            self._ws_active_connections = max(0, self._ws_active_connections - 1)

    def session_start(self) -> None:
        with self._lock:
            self._active_sessions += 1

    def session_end(self) -> None:
        with self._lock:
            self._active_sessions = max(0, self._active_sessions - 1)

    def reset(self) -> None:
        """Reset all metrics. Useful for testing."""
        with self._lock:
            self._requests_total = 0
            self._requests_by_path.clear()
            self._requests_by_status.clear()
            self._agent_invocations.clear()
            self._documents_uploaded_total = 0
            self._errors_total = 0
            self._errors_by_code.clear()
            self._ws_active_connections = 0
            self._queue_submitted = 0
            self._queue_completed = 0
            self._queue_failed = 0
            self._active_sessions = 0
            self._latency_counts = {b: 0 for b in self._latency_buckets}
            self._latency_sum = 0.0
            self._latency_count = 0

    def to_prometheus(self) -> str:
        lines: list[str] = []

        # Requests total
        lines.append("# HELP agentbase_requests_total Total number of HTTP requests")
        lines.append("# TYPE agentbase_requests_total counter")
        lines.append(f"agentbase_requests_total {self._requests_total}")
        lines.append("")

        # Requests by path
        lines.append("# HELP agentbase_requests_by_path Requests by path")
        lines.append("# TYPE agentbase_requests_by_path counter")
        for path, count in sorted(self._requests_by_path.items()):
            lines.append(f'agentbase_requests_by_path{{path="{path}"}} {count}')
        lines.append("")

        # Requests by status
        lines.append("# HELP agentbase_requests_by_status Requests by status code")
        lines.append("# TYPE agentbase_requests_by_status counter")
        for code, count in sorted(self._requests_by_status.items()):
            lines.append(f'agentbase_requests_by_status{{status="{code}"}} {count}')
        lines.append("")

        # Latency histogram
        lines.append("# HELP agentbase_request_latency_ms Request latency in milliseconds")
        lines.append("# TYPE agentbase_request_latency_ms histogram")
        cumulative = 0
        for bucket in self._latency_buckets:
            cumulative = self._latency_counts[bucket]
            lines.append(f'agentbase_request_latency_ms_bucket{{le="{bucket}"}} {cumulative}')
        lines.append(f'agentbase_request_latency_ms_bucket{{le="+Inf"}} {self._latency_count}')
        lines.append(f"agentbase_request_latency_ms_sum {self._latency_sum}")
        lines.append(f"agentbase_request_latency_ms_count {self._latency_count}")
        lines.append("")

        # Agent invocations
        lines.append("# HELP agentbase_agent_invocations_total Total agent invocations by agent")
        lines.append("# TYPE agentbase_agent_invocations_total counter")
        for agent, count in sorted(self._agent_invocations.items()):
            lines.append(f'agentbase_agent_invocations_total{{agent="{agent}"}} {count}')
        if not self._agent_invocations:
            lines.append('agentbase_agent_invocations_total{agent="none"} 0')
        lines.append("")

        # Documents uploaded
        lines.append("# HELP agentbase_documents_uploaded_total Total documents uploaded")
        lines.append("# TYPE agentbase_documents_uploaded_total counter")
        lines.append(f"agentbase_documents_uploaded_total {self._documents_uploaded_total}")
        lines.append("")

        # Errors
        lines.append("# HELP agentbase_errors_total Total server errors")
        lines.append("# TYPE agentbase_errors_total counter")
        lines.append(f"agentbase_errors_total {self._errors_total}")
        lines.append("")

        # Errors by code
        lines.append("# HELP agentbase_errors_by_code Errors by error code")
        lines.append("# TYPE agentbase_errors_by_code counter")
        for code, count in sorted(self._errors_by_code.items()):
            lines.append(f'agentbase_errors_by_code{{code="{code}"}} {count}')
        lines.append("")

        # WebSocket active connections
        lines.append("# HELP agentbase_ws_active_connections Active WebSocket connections")
        lines.append("# TYPE agentbase_ws_active_connections gauge")
        lines.append(f"agentbase_ws_active_connections {self._ws_active_connections}")
        lines.append("")

        # Queue metrics
        lines.append("# HELP agentbase_queue_tasks_submitted_total Total queue tasks submitted")
        lines.append("# TYPE agentbase_queue_tasks_submitted_total counter")
        lines.append(f"agentbase_queue_tasks_submitted_total {self._queue_submitted}")
        lines.append("")
        lines.append("# HELP agentbase_queue_tasks_completed_total Total queue tasks completed")
        lines.append("# TYPE agentbase_queue_tasks_completed_total counter")
        lines.append(f"agentbase_queue_tasks_completed_total {self._queue_completed}")
        lines.append("")
        lines.append("# HELP agentbase_queue_tasks_failed_total Total queue tasks failed")
        lines.append("# TYPE agentbase_queue_tasks_failed_total counter")
        lines.append(f"agentbase_queue_tasks_failed_total {self._queue_failed}")
        lines.append("")

        # Active sessions
        lines.append("# HELP agentbase_active_sessions Active agent sessions")
        lines.append("# TYPE agentbase_active_sessions gauge")
        lines.append(f"agentbase_active_sessions {self._active_sessions}")
        lines.append("")

        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Request / Response models                                                   #
# --------------------------------------------------------------------------- #


class InvokeRequest(BaseModel):
    message: str
    thread_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    show_raw: bool = False


class ResumeRequest(BaseModel):
    thread_id: str
    decision: str = "approve"
    decision_json: str | None = None


class InvokeResponse(BaseModel):
    thread_id: str
    agent: str
    output_text: str
    result: Any | None = None


class QueueSubmitRequest(BaseModel):
    agent_name: str
    message: str
    thread_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentInfo(BaseModel):
    name: str
    description: str
    tools: list[str] = Field(default_factory=list)
    middleware: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.4.0"
    agents: list[str] = Field(default_factory=list)
    default_agent: str = ""
    auth_enabled: bool = False
    auth_type: str = "api_key"
    storage_connected: bool = True
    queue_connected: bool = True


class ErrorResponse(BaseModel):
    error: str
    code: str
    http_status: int
    detail: Any | None = None
    request_id: str | None = None


class PaginatedResponse(BaseModel):
    """Generic paginated response wrapper."""
    items: list[Any]
    total: int
    page: int
    page_size: int
    has_next: bool = False


# --------------------------------------------------------------------------- #
# App factory                                                                 #
# --------------------------------------------------------------------------- #

_runtime = None
_rate_limiter: RateLimiter | None = None
_metrics = MetricsCollector()


def get_runtime():
    """Get or create the singleton RuntimeContext."""
    global _runtime
    if _runtime is None:
        _runtime = build_runtime()
    return _runtime


def reset_runtime():
    """Reset the runtime (useful for testing)."""
    global _runtime
    _runtime = None


def _get_rate_limiter() -> RateLimiter:
    """Get or create the rate limiter from app config."""
    global _rate_limiter
    if _rate_limiter is None:
        rt = get_runtime()
        cfg = rt.app_config.rate_limit
        _rate_limiter = RateLimiter(
            max_requests=cfg.max_requests,
            window_seconds=cfg.window_seconds,
            burst=cfg.burst,
        )
    return _rate_limiter


def _reset_rate_limiter() -> None:
    """Reset rate limiter (for testing)."""
    global _rate_limiter
    _rate_limiter = None


def _make_error_response(
    error: str,
    code: str,
    http_status: int,
    request_id: str | None = None,
    detail: Any | None = None,
) -> JSONResponse:
    """Create a structured JSON error response."""
    return JSONResponse(
        status_code=http_status,
        content=ErrorResponse(
            error=error,
            code=code,
            http_status=http_status,
            detail=detail,
            request_id=request_id,
        ).model_dump(),
    )


def create_app(*, runtime=None) -> FastAPI:
    """Create a FastAPI app instance.

    Args:
        runtime: Optional pre-built RuntimeContext. If None, will be
            lazily created on first request via ``build_runtime()``.
    """
    global _runtime
    if runtime is not None:
        _runtime = runtime

    app = FastAPI(
        title="agentbase",
        description="Deep Agents backend harness — API layer",
        version="0.4.0",
        openapi_tags=[
            {"name": "health", "description": "Health check and metrics"},
            {"name": "agents", "description": "Agent listing and configuration"},
            {"name": "invoke", "description": "Agent invocation (sync, stream, resume)"},
            {"name": "queue", "description": "Async task queue management"},
            {"name": "documents", "description": "Knowledge base document management"},
            {"name": "websocket", "description": "WebSocket real-time communication"},
        ],
    )

    # ------------------------------------------------------------------ #
    # CORS middleware                                                    #
    # ------------------------------------------------------------------ #
    cors_origins = os.environ.get("AGENTBASE_CORS_ORIGINS", "*").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in cors_origins],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------ #
    # Request ID + Metrics middleware (runs before auth)                  #
    # ------------------------------------------------------------------ #
    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        # Generate or propagate request ID
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        # Store on request state for downstream access
        request.state.request_id = request_id

        start_time = time.time()
        response = await call_next(request)
        latency_ms = (time.time() - start_time) * 1000

        # Inject request ID into response
        response.headers["X-Request-ID"] = request_id

        # Record metrics
        _metrics.record_request(request.url.path, response.status_code, latency_ms)

        return response

    # ------------------------------------------------------------------ #
    # Auth + Rate limit middleware                                       #
    # ------------------------------------------------------------------ #
    @app.middleware("http")
    async def auth_and_rate_limit(request: Request, call_next):
        path = request.url.path
        # Skip auth for public paths
        if path in _PUBLIC_PATHS or path.startswith("/docs") or path.startswith("/redoc"):
            return await call_next(request)

        rt = get_runtime()

        # Authentication
        auth_ok, payload = _verify_auth(request, rt.app_config)
        if not auth_ok:
            _metrics.record_error(ErrorCode.AUTH_MISSING_KEY)
            return _make_error_response(
                error="Invalid or missing authentication credentials",
                code=ErrorCode.AUTH_MISSING_KEY,
                http_status=status.HTTP_401_UNAUTHORIZED,
                request_id=getattr(request.state, "request_id", None),
            )

        # RBAC permission check
        if not _check_rbac(request, payload, rt.app_config):
            _metrics.record_error(ErrorCode.AUTH_FORBIDDEN)
            return _make_error_response(
                error="Insufficient permissions for this operation",
                code=ErrorCode.AUTH_FORBIDDEN,
                http_status=status.HTTP_403_FORBIDDEN,
                request_id=getattr(request.state, "request_id", None),
            )

        # Rate limiting
        rl_cfg = rt.app_config.rate_limit
        if rl_cfg.enabled:
            limiter = _get_rate_limiter()
            client_ip = request.client.host if request.client else "unknown"
            if not limiter.check(client_ip):
                _metrics.record_error(ErrorCode.RATE_EXCEEDED)
                return _make_error_response(
                    error="Rate limit exceeded",
                    code=ErrorCode.RATE_EXCEEDED,
                    http_status=status.HTTP_429_TOO_MANY_REQUESTS,
                    request_id=getattr(request.state, "request_id", None),
                    detail={"retry_after": rl_cfg.window_seconds},
                )

        return await call_next(request)

    # ------------------------------------------------------------------ #
    # Global exception handler                                           #
    # ------------------------------------------------------------------ #
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", None)

        if isinstance(exc, HTTPException):
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": exc.detail, "code": None, "request_id": request_id},
            )

        if isinstance(exc, AgentbaseError):
            _metrics.record_error(exc.code)
            return _make_error_response(
                error=str(exc),
                code=exc.code,
                http_status=exc.http_status,
                request_id=request_id,
                detail=exc.detail,
            )

        # Unknown errors
        _metrics.record_error(ErrorCode.RT_UNKNOWN)
        return _make_error_response(
            error="Internal server error",
            code=ErrorCode.RT_UNKNOWN,
            http_status=500,
            request_id=request_id,
            detail=str(exc) if os.environ.get("AGENTBASE_ENV") != "prod" else None,
        )

    # ------------------------------------------------------------------ #
    # Health (public, no auth)                                          #
    # ------------------------------------------------------------------ #
    @app.get("/health", response_model=HealthResponse, tags=["health"])
    def health():
        rt = get_runtime()
        try:
            agents = rt.list_agents()
        except Exception:
            agents = []

        # Check storage connectivity
        storage_ok = True
        try:
            _ = rt.factory.storage
        except Exception:
            storage_ok = False

        # Check queue connectivity
        queue_ok = True
        try:
            q = rt.factory.queue
            if q is not None and hasattr(q, "_get_client"):
                _ = q._get_client()
        except Exception:
            queue_ok = False

        auth_type = "none"
        if hasattr(rt.app_config, "auth"):
            auth_type = rt.app_config.auth.type

        return HealthResponse(
            status="ok" if storage_ok else "degraded",
            version=rt.app_config.app.version,
            agents=agents,
            default_agent=rt.app_config.runtime.default_agent,
            auth_enabled=_is_auth_enabled() or auth_type == "jwt",
            auth_type=auth_type,
            storage_connected=storage_ok,
            queue_connected=queue_ok,
        )

    # ------------------------------------------------------------------ #
    # Agent management                                                   #
    # ------------------------------------------------------------------ #
    @app.get("/agents", response_model=list[AgentInfo], tags=["agents"])
    def list_agents():
        rt = get_runtime()
        result: list[AgentInfo] = []
        for name in rt.list_agents():
            cfg = rt.get_agent_config(name)
            result.append(AgentInfo(
                name=cfg.name,
                description=cfg.description,
                tools=cfg.tools,
                middleware=cfg.middleware,
                capabilities=cfg.capabilities,
            ))
        return result

    @app.get("/agents/{agent_name}", tags=["agents"])
    def get_agent(agent_name: str):
        rt = get_runtime()
        try:
            cfg = rt.get_agent_config(agent_name)
        except Exception:
            raise NotFoundError(f"Agent not found: {agent_name}")
        return cfg.model_dump()

    @app.get("/agents/{agent_name}/configurable", tags=["agents"])
    def get_configurable_items(agent_name: str):
        rt = get_runtime()
        try:
            cfg = rt.get_agent_config(agent_name)
        except Exception:
            raise NotFoundError(f"Agent not found: {agent_name}")
        return cfg.get_configurable_items()

    # ------------------------------------------------------------------ #
    # Agent invocation — sync                                            #
    # ------------------------------------------------------------------ #
    @app.post("/agents/{agent_name}/invoke", response_model=InvokeResponse, tags=["invoke"])
    def invoke_agent(agent_name: str, req: InvokeRequest):
        rt = get_runtime()
        try:
            agent = rt.get_agent(agent_name)
        except Exception as exc:
            raise NotFoundError(f"Agent not found: {agent_name}: {exc}")

        _metrics.record_agent_invocation(agent_name)
        try:
            result = rt.runner.invoke(
                agent=agent,
                agent_name=agent_name,
                message=req.message,
                thread_id=req.thread_id,
                metadata=req.metadata or None,
            )
            return InvokeResponse(
                thread_id=result["thread_id"],
                agent=result["agent"],
                output_text=result.get("output_text", ""),
                result=result.get("result") if req.show_raw else None,
            )
        except RuntimeExecutionError as exc:
            raise exc
        except AgentbaseError as exc:
            raise exc

    # ------------------------------------------------------------------ #
    # Agent invocation — streaming (SSE)                                #
    # ------------------------------------------------------------------ #
    @app.post("/agents/{agent_name}/stream", tags=["invoke"])
    def stream_agent(agent_name: str, req: InvokeRequest):
        rt = get_runtime()
        try:
            agent = rt.get_agent(agent_name)
        except Exception as exc:
            raise NotFoundError(f"Agent not found: {agent_name}: {exc}")

        _metrics.record_agent_invocation(agent_name)

        def event_stream():
            # Send initial keepalive to prevent proxy timeout
            yield ": keepalive\n\n"
            try:
                for event in rt.runner.stream(
                    agent=agent,
                    agent_name=agent_name,
                    message=req.message,
                    thread_id=req.thread_id,
                    metadata=req.metadata or None,
                ):
                    yield event.to_sse()
            except RuntimeExecutionError as exc:
                error_data = {"type": "run.error", "data": {"error": str(exc), "code": exc.code}}
                yield f"event: run.error\ndata: {json.dumps(error_data, ensure_ascii=False)}\n\n"

        # NOTE: X-Request-ID is set centrally by `request_context_middleware`
        # on every response, so it must not be repeated here.
        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ------------------------------------------------------------------ #
    # Resume interrupted agent                                           #
    # ------------------------------------------------------------------ #
    @app.post("/agents/{agent_name}/resume", response_model=InvokeResponse, tags=["invoke"])
    def resume_agent(agent_name: str, req: ResumeRequest):
        rt = get_runtime()
        try:
            agent = rt.get_agent(agent_name)
        except Exception as exc:
            raise NotFoundError(f"Agent not found: {agent_name}: {exc}")

        if req.decision_json:
            try:
                decision: Any = json.loads(req.decision_json)
            except json.JSONDecodeError:
                decision = req.decision
        else:
            decision = req.decision

        try:
            result = rt.runner.resume(
                agent=agent,
                agent_name=agent_name,
                thread_id=req.thread_id,
                decision=decision,
            )
            return InvokeResponse(
                thread_id=result["thread_id"],
                agent=result["agent"],
                output_text=result.get("output_text", ""),
            )
        except RuntimeExecutionError as exc:
            if "AGENTBASE_RT_002" in str(exc) or "Session not found" in str(exc):
                raise NotFoundError(f"Session not found: {req.thread_id}")
            raise exc

    # ------------------------------------------------------------------ #
    # Queue — async task management                                      #
    # ------------------------------------------------------------------ #
    def _get_queue():
        """Get the queue from factory, falling back to memory."""
        rt = get_runtime()
        queue = rt.factory.queue
        if queue is None:
            from agentbase.core.queue import MemoryRequestQueue
            queue = MemoryRequestQueue()
            # Cache it on the factory for reuse
            rt.factory._queue = queue
        return queue

    @app.post("/queue/submit", tags=["queue"])
    def submit_task(req: QueueSubmitRequest):
        queue = _get_queue()
        task = queue.submit(
            agent_name=req.agent_name,
            message=req.message,
            thread_id=req.thread_id,
            metadata=req.metadata,
        )
        return task.to_dict()

    @app.get("/queue/{task_id}", tags=["queue"])
    def get_task(task_id: str):
        queue = _get_queue()
        task = queue.get_task(task_id)
        if task is None:
            raise NotFoundError(f"Task not found: {task_id}")
        return task.to_dict()

    @app.get("/queue", tags=["queue"])
    def list_tasks(
        agent_name: str | None = Query(None),
        status: str | None = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ):
        from agentbase.core.queue import TaskStatus

        queue = _get_queue()
        status_enum = TaskStatus(status) if status else None
        tasks = queue.list_tasks(agent_name=agent_name, status=status_enum)
        total = len(tasks)
        start = (page - 1) * page_size
        end = start + page_size
        page_items = tasks[start:end]
        return {
            "items": [t.to_dict() for t in page_items],
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_next": end < total,
        }

    @app.delete("/queue/{task_id}", tags=["queue"])
    def cancel_task(task_id: str):
        queue = _get_queue()
        if not queue.cancel(task_id):
            raise QueueError(
                f"Cannot cancel task: {task_id}",
                code=ErrorCode.QUEUE_CANCEL_FAILED,
            )
        return {"status": "cancelled", "task_id": task_id}

    @app.post("/queue/process", tags=["queue"])
    def process_queue():
        """Process all pending tasks in the queue."""
        queue = _get_queue()
        rt = get_runtime()

        def handler(task):
            agent = rt.get_agent(task.agent_name)
            result = rt.runner.invoke(
                agent=agent,
                agent_name=task.agent_name,
                message=task.message,
                thread_id=task.thread_id,
            )
            return result

        results = queue.process_all(handler)
        return {"processed": len(results), "results": [r.to_dict() for r in results]}

    # ------------------------------------------------------------------ #
    # Document upload — ingest files into knowledge base                 #
    # ------------------------------------------------------------------ #
    @app.post("/documents/upload", response_model=None, tags=["documents"])
    async def upload_document_v2(
        file: UploadFile = File(...),
        title: str = Form(""),
        agent_name: str = Form("default"),
    ):
        """Upload a file to the knowledge base.

        Accepts multipart form data. The file is parsed using the registered
        parser for its extension, chunked, embedded, and stored in the KB.
        """
        rt = get_runtime()
        factory = rt.factory

        # Read file content with size check
        content_bytes = await file.read()
        if len(content_bytes) > _MAX_UPLOAD_SIZE:
            from agentbase.runtime.errors import UploadError
            raise UploadError(
                f"File too large: {len(content_bytes)} bytes (max {_MAX_UPLOAD_SIZE})",
                code=ErrorCode.UPLOAD_TOO_LARGE,
            )

        filename = file.filename or "upload.txt"

        # Save to temp file for parsing
        import tempfile
        from pathlib import Path as _Path

        with tempfile.NamedTemporaryFile(delete=False, suffix=_Path(filename).suffix) as tmp:
            tmp.write(content_bytes)
            tmp_path = _Path(tmp.name)

        try:
            kb = factory.knowledge_base

            from agentbase.core.parsers import parser_registry
            ext = _Path(filename).suffix.lower()
            if parser_registry.has(ext):
                parser = parser_registry.get(ext)
                text = parser.parse(tmp_path)
            else:
                text = content_bytes.decode("utf-8", errors="replace")

            doc = kb.add_document(
                source=filename,
                title=title or filename,
                content=text,
                metadata={"uploaded_via": "api", "agent": agent_name},
            )

            _metrics.record_upload()

            return {
                "status": "ok",
                "document_id": doc.id,
                "source": filename,
                "title": title or filename,
                "chunk_count": doc.chunk_count,
                "content_length": len(text),
            }
        except Exception as exc:
            raise exc
        finally:
            tmp_path.unlink(missing_ok=True)

    @app.get("/documents", tags=["documents"])
    def list_documents(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ):
        """List all documents in the knowledge base (paginated)."""
        rt = get_runtime()
        kb = rt.factory.knowledge_base
        docs = kb.list_documents()
        total = len(docs)
        start = (page - 1) * page_size
        end = start + page_size
        page_items = docs[start:end]
        return {
            "items": [
                {
                    "id": d.id,
                    "source": d.source,
                    "title": d.title,
                    "chunk_count": d.chunk_count,
                    "created_at": d.created_at,
                    "updated_at": d.updated_at,
                }
                for d in page_items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_next": end < total,
        }

    @app.get("/documents/{doc_id}", tags=["documents"])
    def get_document(doc_id: int):
        """Get a document by ID."""
        rt = get_runtime()
        kb = rt.factory.knowledge_base
        doc = kb.get_document(doc_id=doc_id)
        if doc is None:
            raise NotFoundError(f"Document not found: {doc_id}")
        return {
            "id": doc.id,
            "source": doc.source,
            "title": doc.title,
            "content": doc.content,
            "chunk_count": doc.chunk_count,
            "metadata": doc.metadata,
            "created_at": doc.created_at,
            "updated_at": doc.updated_at,
        }

    @app.delete("/documents/{doc_id}", tags=["documents"])
    def delete_document(doc_id: int):
        """Delete a document from the knowledge base."""
        rt = get_runtime()
        kb = rt.factory.knowledge_base
        if not kb.delete_document(doc_id=doc_id):
            raise NotFoundError(f"Document not found: {doc_id}")
        return {"status": "deleted", "document_id": doc_id}

    @app.post("/documents/search", tags=["documents"])
    def search_documents(query: str = "", top_k: int = 5):
        """Search documents in the knowledge base."""
        rt = get_runtime()
        kb = rt.factory.knowledge_base
        results = kb.search(query, top_k=top_k)
        return [
            {
                "document_id": r.document.id,
                "source": r.document.source,
                "title": r.document.title,
                "chunk_content": r.chunk.content,
                "chunk_index": r.chunk.chunk_index,
                "score": r.score,
            }
            for r in results
        ]

    # ------------------------------------------------------------------ #
    # WebSocket — real-time agent communication                          #
    # ------------------------------------------------------------------ #
    # NOTE: `FastAPI.websocket()` does not accept a `tags` keyword argument
    # (WebSocket routes have no OpenAPI tags), so it must be omitted.
    @app.websocket("/ws/agents/{agent_name}")
    async def ws_agent(websocket: WebSocket, agent_name: str):
        """WebSocket endpoint for real-time agent communication.

        Client sends: {"message": "...", "thread_id": "..."}
        Server responds with streaming events as JSON messages.

        Heartbeat: server sends {"type": "heartbeat"} every 30 seconds
        to keep the connection alive.
        """
        import asyncio

        # Auth check for WebSocket
        rt = get_runtime()
        auth_cfg = getattr(rt.app_config, "auth", None)
        if auth_cfg is not None and auth_cfg.type != "none":
            if _is_auth_enabled():
                token = websocket.query_params.get("token", "")
                if not token or token != _get_api_key():
                    await websocket.close(code=4001, reason="Unauthorized")
                    return

        await websocket.accept()
        _metrics.ws_connect()

        try:
            agent = rt.get_agent(agent_name)
        except Exception:
            await websocket.send_json({
                "type": "error",
                "data": {"error": f"Agent not found: {agent_name}", "code": ErrorCode.WS_AGENT_NOT_FOUND},
            })
            await websocket.close(code=4004)
            _metrics.ws_disconnect()
            return

        try:
            while True:
                # Wait for message with timeout for heartbeat
                try:
                    data = await asyncio.wait_for(
                        websocket.receive_json(),
                        timeout=_WS_HEARTBEAT_INTERVAL,
                    )
                except asyncio.TimeoutError:
                    # Send heartbeat
                    await websocket.send_json({"type": "heartbeat", "data": {"ts": time.time()}})
                    continue

                message = data.get("message", "")
                thread_id = data.get("thread_id")

                if not message:
                    await websocket.send_json({
                        "type": "error",
                        "data": {"error": "Empty message", "code": ErrorCode.WS_EMPTY_MESSAGE},
                    })
                    continue

                # Stream events back
                for event in rt.runner.stream(
                    agent=agent,
                    agent_name=agent_name,
                    message=message,
                    thread_id=thread_id,
                ):
                    await websocket.send_json(event.model_dump())

                await websocket.send_json({"type": "stream_end"})

        except WebSocketDisconnect:
            pass
        except Exception as exc:
            try:
                await websocket.send_json({
                    "type": "error",
                    "data": {"error": str(exc), "code": ErrorCode.RT_UNKNOWN},
                })
            except Exception:
                pass
        finally:
            _metrics.ws_disconnect()

    # ------------------------------------------------------------------ #
    # Metrics — Prometheus format                                        #
    # ------------------------------------------------------------------ #
    @app.get("/metrics", tags=["health"])
    def metrics():
        """Prometheus-format metrics endpoint (public, no auth)."""
        return PlainTextResponse(
            _metrics.to_prometheus(),
            media_type="text/plain",
        )

    return app


# Default app instance for ``uvicorn agentbase.api:app``
app = create_app()
