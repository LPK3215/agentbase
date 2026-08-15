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

GET    /audit/events               — query audit log (paginated, filterable)
GET    /audit/events/count         — count audit events matching filter
GET    /audit/events/export        — export audit events (JSON/CSV/YAML, filterable)

    GET    /experiments                — list experiments
    POST   /experiments                — create experiment
    GET    /experiments/{name}         — get experiment detail
    DELETE /experiments/{name}         — delete experiment
    POST   /experiments/{name}/assign  — assign a request to a variant
    POST   /experiments/{name}/results — record a result
     GET    /experiments/{name}/stats   — get experiment statistics
     GET    /admin/rate-limit            — get rate limiter stats
     POST   /admin/rate-limit/quotas/{role} — set per-role quota
     DELETE /admin/rate-limit/buckets    — reset all buckets

     GET    /models                      — list all registered models
     POST   /models                      — register a new model config
     GET    /models/{name}               — get model config detail
     PATCH  /models/{name}               — update model config fields
     DELETE /models/{name}               — delete a model config
     POST   /models/{name}/test          — test model connectivity

     GET    /prompts                      — list all prompt templates
     POST   /prompts                      — register a prompt template
     GET    /prompts/{name}               — get prompt template detail
     PATCH  /prompts/{name}               — update prompt template fields
     DELETE /prompts/{name}               — delete a prompt template
     POST   /prompts/{name}/render        — render a prompt template with variables

     GET    /users                       — list all registered users
     POST   /users                       — register a new user
     GET    /users/{username}            — get user detail
     PATCH  /users/{username}            — update user fields
     DELETE /users/{username}            — delete a user
     POST   /auth/register               — register a new user (sign-up flow)
     POST   /auth/login                 — authenticate user and return user info
     GET    /auth/oauth2/{provider}/authorize  — redirect to OAuth2 provider
     GET    /auth/oauth2/{provider}/callback   — handle OAuth2 callback (issues JWT)

     GET    /apikeys                     — list all API keys
     POST   /apikeys                     — create a new API key (returns raw key)
     GET    /apikeys/{key_id}            — get API key detail
     PATCH  /apikeys/{key_id}            — update API key fields
     DELETE /apikeys/{key_id}            — delete an API key
     POST   /apikeys/{key_id}/revoke     — revoke (disable) an API key
     POST   /apikeys/verify             — verify an API key (returns status)

     GET    /sessions                    — list all sessions (filterable)
     GET    /sessions/stats              — session counts by status
     GET    /sessions/{thread_id}        — get session details
     DELETE /sessions/{thread_id}        — cancel a session
     POST   /sessions/cleanup            — clean up expired/stale/completed sessions

GET    /usage/stats                 — aggregated usage statistics (tokens, costs)
GET    /usage/records               — list usage records (paginated, filterable)
GET    /usage/summary               — high-level usage summary (totals)
DELETE /usage/records               — clear all usage records

GET    /webhooks                    — list all webhook endpoints
POST   /webhooks                    — register a webhook endpoint
GET    /webhooks/{endpoint_id}      — get webhook endpoint detail
PATCH  /webhooks/{endpoint_id}      — update webhook endpoint fields
DELETE /webhooks/{endpoint_id}      — delete a webhook endpoint
POST   /webhooks/{endpoint_id}/test — send a test event to an endpoint
GET    /webhooks/deliveries         — list delivery records (paginated, filterable)
GET    /webhooks/stats              — aggregate webhook delivery statistics

GET    /feedback                    — list feedback records (paginated, filterable)
POST   /feedback                    — submit user feedback (rating, comment, tags)
GET    /feedback/{record_id}        — get feedback record detail
PATCH  /feedback/{record_id}        — update feedback (rating, comment, tags)
DELETE /feedback/{record_id}        — delete a feedback record
GET    /feedback/stats              — aggregate feedback statistics

     GET    /notifications               — list notifications (paginated, filterable)
     POST   /notifications               — create a notification
     GET    /notifications/stats         — aggregate notification statistics
     GET    /notifications/unread-count  — unread count for a user
     POST   /notifications/broadcast     — broadcast to all users
     GET    /notifications/{id}          — get notification detail
     PATCH  /notifications/{id}          — update notification fields
     POST   /notifications/{id}/read     — mark notification as read
     POST   /notifications/{id}/unread   — mark notification as unread
     POST   /notifications/read-all      — mark all as read for a user
     DELETE /notifications/{id}          — delete a notification


     WS     /ws/agents/{name}           — real-time agent communication
"""
from __future__ import annotations

import hmac
import json
import os
import threading
import time
import uuid
from collections import defaultdict
from typing import Any

from fastapi import (
    Body,
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
    ErrorCode,
    NotFoundError,
    QueueError,
    RuntimeExecutionError,
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
        # Use constant-time comparison to prevent timing side-channel
        expected = _get_api_key()
        if expected is not None:
            return hmac.compare_digest(token, expected)
        return False
    api_key_header = request.headers.get("X-API-Key", "")
    if api_key_header:
        expected = _get_api_key()
        if expected is not None:
            return hmac.compare_digest(api_key_header, expected)
        return False
    return False


def _get_jwt_auth(app_config: Any) -> Any | None:
    """Get JWTAuth instance if JWT auth is configured.

    Raises ``ConfigError`` if ``auth.type`` is ``jwt`` but ``secret``
    is empty — this is a fail-fast guard so the server never starts
    with an unforgeable but ephemeral secret that would silently
    invalidate all tokens on restart.
    """
    auth_cfg = getattr(app_config, "auth", None)
    if auth_cfg is None or auth_cfg.type != "jwt":
        return None
    from agentbase.extensions.auth import JWTAuth, DEFAULT_ROLE_PERMISSIONS
    from agentbase.runtime.errors import ConfigError

    if not auth_cfg.secret:
        raise ConfigError(
            "JWT auth type is 'jwt' but no secret is configured. "
            "Set AGENTBASE_AUTH__SECRET to a strong random value.",
            code="AGENTBASE_CONFIG_002",
            detail={"field": "auth.secret"},
        )
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

    When API Key management (``apikey_manager.enabled``) is active,
    Bearer tokens and ``X-API-Key`` headers are also checked against
    the managed key store.  This allows per-user/per-app keys with
    independent roles and revocation — independent of the global
    ``AGENTBASE_API_KEY``.
    """
    # First: try API Key manager (if enabled)
    _apikey_mgr = _get_apikey_manager()
    if _apikey_mgr is not None and _apikey_mgr.enabled:
        # Check Bearer token
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            entry = _apikey_mgr.verify(token)
            if entry is not None:
                # Build a synthetic JWT-like payload from the key's roles
                payload = {
                    "sub": entry.user_id or entry.key_id,
                    "roles": entry.roles,
                    "key_id": entry.key_id,
                    "source": "apikey_manager",
                }
                return True, payload
        # Check X-API-Key header
        api_key_header = request.headers.get("X-API-Key", "")
        if api_key_header:
            entry = _apikey_mgr.verify(api_key_header)
            if entry is not None:
                payload = {
                    "sub": entry.user_id or entry.key_id,
                    "roles": entry.roles,
                    "key_id": entry.key_id,
                    "source": "apikey_manager",
                }
                return True, payload

    # Second: JWT auth (if configured)
    jwt_auth = _get_jwt_auth(app_config)
    if jwt_auth is not None:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            payload = jwt_auth.verify_token(token)
            if payload is not None:
                return True, payload
            # If API key manager is also enabled, fall through to return False
            if _apikey_mgr is not None and _apikey_mgr.enabled:
                return False, None
            return False, None
        # Also check X-API-Key as fallback (constant-time comparison)
        api_key = request.headers.get("X-API-Key", "")
        expected = _get_api_key()
        if api_key and expected is not None and hmac.compare_digest(api_key, expected):
            return True, None
        return False, None

    # Third: global API Key mode
    if not _is_auth_enabled():
        return True, None
    if _verify_api_key(request):
        return True, None
    return False, None


def _check_rbac(request: Request, payload: dict[str, Any] | None, app_config: Any) -> bool:
    """Check RBAC permissions for the request path.

    When JWT auth is configured, uses JWTAuth's path-permission mapping.
    When only API Key manager is active (no JWT), builds a JWTAuth
    instance from default role permissions to perform the same check.
    Returns ``True`` if no RBAC is configured at all.
    """
    jwt_auth = _get_jwt_auth(app_config)
    if jwt_auth is None:
        # If API key manager is enabled, still do RBAC with defaults
        _apikey_mgr = _get_apikey_manager()
        if _apikey_mgr is not None and _apikey_mgr.enabled and payload is not None:
            from agentbase.extensions.auth import JWTAuth, DEFAULT_ROLE_PERMISSIONS

            jwt_auth = JWTAuth(
                secret="rbac-only-defaults",
                role_permissions=DEFAULT_ROLE_PERMISSIONS,
            )
        else:
            return True  # No RBAC configured or no payload
    if payload is None:
        return True  # No payload = no RBAC check (global API key mode)
    return jwt_auth.check_path_permission(payload, request.method, request.url.path)


# --------------------------------------------------------------------------- #
# Rate Limiter                                                                #
# --------------------------------------------------------------------------- #


class RateLimiter:
    """Per-IP sliding-window rate limiter with burst and per-role quota support.

    Uses a token bucket variant: each IP gets a bucket that refills
    at ``max_requests / window_seconds`` tokens per second, up to
    ``max_requests + burst`` capacity.

    When per-role quotas are configured (via ``RateLimitConfig.quotas``),
    the limiter uses role-specific limits instead of the global default.
    Each (role, IP) pair gets its own bucket.
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
        self._role_quotas: dict[str, tuple[int, int, int]] = {}

    def set_role_quota(self, role: str, max_requests: int, window_seconds: int, burst: int) -> None:
        """Set a custom quota for a specific role."""
        self._role_quotas[role] = (max_requests, window_seconds, burst)

    def get_role_quota(self, role: str) -> tuple[int, int, int]:
        """Get (max_requests, window_seconds, burst) for a role.

        Falls back to global defaults if no role-specific quota is set.
        """
        return self._role_quotas.get(role, (self.max_requests, self.window, self.burst))

    def check(self, client_ip: str, role: str = "user") -> bool:
        """Returns True if request is allowed, False if rate limited.

        Uses role-specific quota if configured, otherwise global default.
        """
        max_req, window, burst = self.get_role_quota(role)
        key = f"{role}:{client_ip}"
        now = time.time()
        bucket = self._buckets[key]
        # Sliding window: remove timestamps outside the window
        self._buckets[key] = [t for t in bucket if now - t < window]
        if len(self._buckets[key]) >= max_req + burst:
            return False
        self._buckets[key].append(now)
        return True

    def reset(self) -> None:
        """Clear all buckets (for testing)."""
        self._buckets.clear()

    def get_remaining(self, client_ip: str, role: str = "user") -> int:
        """Get remaining requests for an IP + role."""
        max_req, window, burst = self.get_role_quota(role)
        key = f"{role}:{client_ip}"
        now = time.time()
        bucket = self._buckets.get(key, [])
        recent = [t for t in bucket if now - t < window]
        return max(0, max_req + burst - len(recent))

    @property
    def stats(self) -> dict[str, Any]:
        """Return rate limiter statistics including per-role quotas."""
        now = time.time()
        per_key: dict[str, int] = {}
        for key, bucket in self._buckets.items():
            # Extract role from key
            role = key.split(":")[0] if ":" in key else "default"
            _, window, _ = self.get_role_quota(role)
            recent = [t for t in bucket if now - t < window]
            per_key[key] = len(recent)
        return {
            "max_requests": self.max_requests,
            "window_seconds": self.window,
            "burst": self.burst,
            "capacity": self.max_requests + self.burst,
            "role_quotas": {
                role: {"max_requests": q[0], "window_seconds": q[1], "burst": q[2]}
                for role, q in self._role_quotas.items()
            },
            "active_keys": len(per_key),
            "per_key": per_key,
        }


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


class VariantModel(BaseModel):
    name: str
    weight: int = 1
    model_override: dict[str, Any] | None = None
    system_prompt_override: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateExperimentRequest(BaseModel):
    name: str
    description: str = ""
    strategy: str = "round_robin"
    variants: list[VariantModel] = Field(default_factory=list)


class AssignRequest(BaseModel):
    request_id: str | None = None


class RecordResultRequest(BaseModel):
    variant_name: str
    success: bool = True
    duration_ms: float = 0.0
    output_text: str = ""
    error: str = ""
    request_id: str | None = None


class RegisterModelRequest(BaseModel):
    """Request body for registering a model configuration."""
    name: str
    provider: str = "openai"
    model_name: str = ""
    temperature: float = 0.0
    max_tokens: int | None = None
    timeout_seconds: int = 120
    base_url: str | None = None
    api_key_env: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)


class UpdateModelRequest(BaseModel):
    """Request body for updating a model configuration."""
    provider: str | None = None
    model_name: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: int | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    extra: dict[str, Any] | None = None
    description: str | None = None
    enabled: bool | None = None
    tags: list[str] | None = None


class TestModelRequest(BaseModel):
    """Request body for testing a model configuration."""
    prompt: str = "Say hello in one word."


class RegisterPromptRequest(BaseModel):
    """Request body for registering a prompt template."""
    name: str
    content: str = ""
    variables: list[str] = Field(default_factory=list)
    description: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    version: str = "1.0.0"
    enabled: bool = True


class UpdatePromptRequest(BaseModel):
    """Request body for updating a prompt template."""
    content: str | None = None
    variables: list[str] | None = None
    description: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    version: str | None = None
    enabled: bool | None = None


class RenderPromptRequest(BaseModel):
    """Request body for rendering a prompt template."""
    variables: dict[str, Any] = Field(default_factory=dict)


class RegisterUserRequest(BaseModel):
    """Request body for registering a user."""
    username: str
    email: str = ""
    password: str = ""
    roles: list[str] = Field(default_factory=lambda: ["user"])
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateUserRequest(BaseModel):
    """Request body for updating a user."""
    email: str | None = None
    password: str | None = None
    roles: list[str] | None = None
    enabled: bool | None = None
    metadata: dict[str, Any] | None = None


class LoginRequest(BaseModel):
    """Request body for user login."""
    username: str
    password: str


class CreateApiKeyRequest(BaseModel):
    """Request body for creating an API key."""
    name: str = ""
    roles: list[str] = Field(default_factory=lambda: ["user"])
    user_id: str = ""
    description: str = ""
    expires_at: str = ""  # ISO 8601 UTC timestamp, empty = never
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateApiKeyRequest(BaseModel):
    """Request body for updating an API key."""
    name: str | None = None
    roles: list[str] | None = None
    description: str | None = None
    enabled: bool | None = None
    expires_at: str | None = None
    metadata: dict[str, Any] | None = None


class VerifyApiKeyRequest(BaseModel):
    """Request body for verifying an API key."""
    key: str


class AgentInfo(BaseModel):
    name: str
    description: str
    tools: list[str] = Field(default_factory=list)
    middleware: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)


class ComponentHealth(BaseModel):
    """Health status of a single dependency component."""

    name: str
    healthy: bool
    detail: str = ""


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.4.0"
    agents: list[str] = Field(default_factory=list)
    default_agent: str = ""
    auth_enabled: bool = False
    auth_type: str = "api_key"
    storage_connected: bool = True
    queue_connected: bool = True
    embedding_connected: bool = True
    search_connected: bool = True
    tracer_connected: bool = True
    components: list[ComponentHealth] = Field(default_factory=list)


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


# Model manager singleton (lazily built from app config)
_model_manager: Any = None
_model_manager_lock = threading.Lock()


def _get_model_manager() -> Any:
    """Get or create the ModelManager singleton from app config."""
    global _model_manager
    if _model_manager is None:
        with _model_manager_lock:
            if _model_manager is None:
                from agentbase.core.model_manager import ModelManager, set_model_manager

                rt = get_runtime()
                cfg = rt.app_config.model_manager
                mgr = ModelManager(
                    provider=cfg.provider,
                    enabled=cfg.enabled,
                    **cfg.options,
                )
                set_model_manager(mgr)
                _model_manager = mgr
    return _model_manager


def _reset_model_manager() -> None:
    """Reset model manager singleton (for testing)."""
    global _model_manager
    _model_manager = None


# Prompt manager singleton (lazily built from app config)
_prompt_manager: Any = None
_prompt_manager_lock = threading.Lock()


def _get_prompt_manager() -> Any:
    """Get or create the PromptManager singleton from app config."""
    global _prompt_manager
    if _prompt_manager is None:
        with _prompt_manager_lock:
            if _prompt_manager is None:
                from agentbase.core.prompt import PromptManager, set_prompt_manager

                rt = get_runtime()
                cfg = rt.app_config.prompt_manager
                mgr = PromptManager(
                    provider=cfg.provider,
                    enabled=cfg.enabled,
                    **cfg.options,
                )
                set_prompt_manager(mgr)
                _prompt_manager = mgr
    return _prompt_manager


def _reset_prompt_manager() -> None:
    """Reset prompt manager singleton (for testing)."""
    global _prompt_manager
    _prompt_manager = None


# User manager singleton (lazily built from app config)
_user_manager: Any = None
_user_manager_lock = threading.Lock()


def _get_user_manager() -> Any:
    """Get or create the UserManager singleton from app config."""
    global _user_manager
    if _user_manager is None:
        with _user_manager_lock:
            if _user_manager is None:
                from agentbase.core.user_manager import UserManager, set_user_manager

                rt = get_runtime()
                cfg = rt.app_config.user_manager
                mgr = UserManager(
                    provider=cfg.provider,
                    enabled=cfg.enabled,
                    **cfg.options,
                )
                set_user_manager(mgr)
                _user_manager = mgr
    return _user_manager


def _reset_user_manager() -> None:
    """Reset user manager singleton (for testing)."""
    global _user_manager
    _user_manager = None


# API key manager singleton (lazily built from app config)
_apikey_manager: Any = None
_apikey_manager_lock = threading.Lock()


def _get_apikey_manager() -> Any:
    """Get or create the ApiKeyManager singleton from app config."""
    global _apikey_manager
    if _apikey_manager is None:
        with _apikey_manager_lock:
            if _apikey_manager is None:
                from agentbase.core.apikey_manager import (
                    ApiKeyManager,
                    set_apikey_manager,
                )

                rt = get_runtime()
                cfg = rt.app_config.apikey_manager
                mgr = ApiKeyManager(
                    provider=cfg.provider,
                    enabled=cfg.enabled,
                    **cfg.options,
                )
                set_apikey_manager(mgr)
                _apikey_manager = mgr
    return _apikey_manager


def _reset_apikey_manager() -> None:
    """Reset API key manager singleton (for testing)."""
    global _apikey_manager
    _apikey_manager = None


# Usage manager singleton (lazily built from app config)
_usage_manager: Any = None
_usage_manager_lock = threading.Lock()


def _get_usage_manager() -> Any:
    """Get or create the UsageManager singleton from app config."""
    global _usage_manager
    if _usage_manager is None:
        with _usage_manager_lock:
            if _usage_manager is None:
                from agentbase.core.usage import UsageManager, set_usage_manager

                rt = get_runtime()
                cfg = rt.app_config.usage
                mgr = UsageManager(
                    provider=cfg.provider,
                    enabled=cfg.enabled,
                    pricing=cfg.pricing or None,
                    max_records=cfg.max_records,
                    **cfg.options,
                )
                set_usage_manager(mgr)
                _usage_manager = mgr
    return _usage_manager


def _reset_usage_manager() -> None:
    """Reset usage manager singleton (for testing)."""
    global _usage_manager
    _usage_manager = None


# Webhook manager singleton (lazily built from app config)
_webhook_manager: Any = None
_webhook_manager_lock = threading.Lock()


def _get_webhook_manager() -> Any:
    """Get or create the WebhookManager singleton from app config."""
    global _webhook_manager
    if _webhook_manager is None:
        with _webhook_manager_lock:
            if _webhook_manager is None:
                from agentbase.core.webhook import WebhookManager, set_webhook_manager

                rt = get_runtime()
                cfg = rt.app_config.webhook
                mgr = WebhookManager(
                    provider=cfg.provider,
                    enabled=cfg.enabled,
                    timeout_seconds=cfg.timeout_seconds,
                    max_retries=cfg.max_retries,
                    retry_backoff=cfg.retry_backoff,
                    **cfg.options,
                )
                set_webhook_manager(mgr)
                _webhook_manager = mgr
    return _webhook_manager


def _reset_webhook_manager() -> None:
    """Reset webhook manager singleton (for testing)."""
    global _webhook_manager
    _webhook_manager = None


# Feedback manager singleton (lazily built from app config)
_feedback_manager: Any = None
_feedback_manager_lock = threading.Lock()


def _get_feedback_manager() -> Any:
    """Get or create the FeedbackManager singleton from app config."""
    global _feedback_manager
    if _feedback_manager is None:
        with _feedback_manager_lock:
            if _feedback_manager is None:
                from agentbase.core.feedback import FeedbackManager, set_feedback_manager

                rt = get_runtime()
                cfg = rt.app_config.feedback
                mgr = FeedbackManager(
                    provider=cfg.provider,
                    enabled=cfg.enabled,
                    max_records=cfg.max_records,
                    **cfg.options,
                )
                set_feedback_manager(mgr)
                _feedback_manager = mgr
    return _feedback_manager


def _reset_feedback_manager() -> None:
    """Reset feedback manager singleton (for testing)."""
    global _feedback_manager
    _feedback_manager = None


# Notification manager singleton (lazily built from app config)
_notification_manager: Any = None
_notification_manager_lock = threading.Lock()


def _get_notification_manager() -> Any:
    """Get or create the NotificationManager singleton from app config."""
    global _notification_manager
    if _notification_manager is None:
        with _notification_manager_lock:
            if _notification_manager is None:
                from agentbase.core.notification import (
                    NotificationManager,
                    set_notification_manager,
                )

                rt = get_runtime()
                cfg = rt.app_config.notification
                mgr = NotificationManager(
                    provider=cfg.provider,
                    enabled=cfg.enabled,
                    max_records=cfg.max_records,
                    **cfg.options,
                )
                set_notification_manager(mgr)
                _notification_manager = mgr
    return _notification_manager


def _reset_notification_manager() -> None:
    """Reset notification manager singleton (for testing)."""
    global _notification_manager
    _notification_manager = None


# Conversation manager singleton (lazily built from app config)
_conversation_manager: Any = None
_conversation_manager_lock = threading.Lock()


def _get_conversation_manager() -> Any:
    """Get or create the ConversationManager singleton from app config."""
    global _conversation_manager
    if _conversation_manager is None:
        with _conversation_manager_lock:
            if _conversation_manager is None:
                from agentbase.core.conversation import (
                    ConversationManager,
                    set_conversation_manager,
                )

                rt = get_runtime()
                cfg = rt.app_config.conversation
                mgr = ConversationManager(
                    provider=cfg.provider,
                    enabled=cfg.enabled,
                    max_conversations=cfg.max_conversations,
                    **cfg.options,
                )
                set_conversation_manager(mgr)
                _conversation_manager = mgr
    return _conversation_manager


def _reset_conversation_manager() -> None:
    """Reset conversation manager singleton (for testing)."""
    global _conversation_manager
    _conversation_manager = None


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


# --------------------------------------------------------------------------- #
# Health-check helpers                                                        #
# --------------------------------------------------------------------------- #

def _check_storage(factory: Any) -> tuple[bool, str]:
    """Probe storage backend connectivity."""
    try:
        storage = factory.storage
        if hasattr(storage, "health_check"):
            ok = storage.health_check()
            return (bool(ok), "ok" if ok else "health_check returned False")
        # Fallback: if no health_check method, just accessing it is enough
        return (True, "ok (no health_check method)")
    except Exception as exc:
        return (False, f"storage error: {exc}")


def _check_queue(factory: Any) -> tuple[bool, str]:
    """Probe queue provider connectivity."""
    try:
        q = factory.queue
        if q is None:
            return (True, "ok (queue not configured)")
        # For Redis-backed queues, verify the client connects
        if hasattr(q, "_get_client"):
            _ = q._get_client()
            return (True, "ok")
        # For MemoryRequestQueue, call stats() as a liveness probe
        if hasattr(q, "stats"):
            _ = q.stats()
            return (True, "ok")
        return (True, "ok (no probe method)")
    except Exception as exc:
        return (False, f"queue error: {exc}")


def _check_embedding(factory: Any, app_config: Any) -> tuple[bool, str]:
    """Probe embedding provider availability."""
    try:
        emb_cfg = app_config.embedding
        if not emb_cfg.provider or emb_cfg.provider == "none":
            return (True, "ok (embedding not configured)")
        from agentbase.core.embeddings import embedding_registry
        if not embedding_registry.has(emb_cfg.provider):
            return (False, f"embedding provider '{emb_cfg.provider}' not registered")
        provider = embedding_registry.get(emb_cfg.provider)
        # Lightweight probe: check dimension property (doesn't make API calls)
        _ = provider.dimension
        return (True, f"ok (provider={emb_cfg.provider})")
    except Exception as exc:
        return (False, f"embedding error: {exc}")


def _check_search(factory: Any, app_config: Any) -> tuple[bool, str]:
    """Probe search provider availability."""
    try:
        search_cfg = app_config.web_search
        if not search_cfg.provider or search_cfg.provider == "none":
            return (True, "ok (search not configured)")
        from agentbase.core.search import search_registry
        if not search_registry.has(search_cfg.provider):
            return (False, f"search provider '{search_cfg.provider}' not registered")
        # Accessing the provider instance verifies it's available
        _ = search_registry.get(search_cfg.provider)
        return (True, f"ok (provider={search_cfg.provider})")
    except Exception as exc:
        return (False, f"search error: {exc}")


def _check_tracer(factory: Any) -> tuple[bool, str]:
    """Probe tracer provider connectivity."""
    try:
        tracer = factory.tracer
        if tracer is None:
            return (True, "ok (tracer not configured)")
        # NullTracer is always healthy
        return (True, "ok")
    except Exception as exc:
        return (False, f"tracer error: {exc}")


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
            {"name": "audit", "description": "Audit log query (read-only)"},
            {"name": "experiments", "description": "A/B testing experiment management"},
{"name": "models", "description": "Model configuration CRUD and connectivity testing"},
{"name": "prompts", "description": "Prompt template CRUD and rendering"},
{"name": "users", "description": "User management CRUD and authentication"},
{"name": "oauth2", "description": "OAuth2 third-party login (Google/GitHub)"},
{"name": "apikeys", "description": "API Key management CRUD, verification, and revocation"},
{"name": "sessions", "description": "Session management and conversation history"},
{"name": "admin", "description": "Admin operations (rate-limit quota management)"},
{"name": "usage", "description": "Token usage tracking and cost statistics"},
{"name": "webhooks", "description": "Webhook endpoint management and delivery records"},
{"name": "feedback", "description": "User feedback collection (ratings, comments, tags)"},
            {"name": "notifications", "description": "In-app notification center (create, query, mark-read)"},
            {"name": "conversations", "description": "Conversation history management (query, update, delete)"},
            {"name": "websocket", "description": "WebSocket real-time communication"},
        ],
    )

    # ------------------------------------------------------------------ #
    # CORS middleware                                                    #
    # ------------------------------------------------------------------ #
    cors_origins = os.environ.get("AGENTBASE_CORS_ORIGINS", "*").split(",")
    cors_origins = [o.strip() for o in cors_origins if o.strip()]
    # Per CORS spec, credentials are not allowed with wildcard origin.
    # When origins is ["*"], force allow_credentials=False to prevent
    # the browser from reflecting arbitrary Origin headers.
    allow_credentials = "*" not in cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=allow_credentials,
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

        # Rate limiting (with per-role quota support)
        rl_cfg = rt.app_config.rate_limit
        if rl_cfg.enabled:
            limiter = _get_rate_limiter()
            client_ip = request.client.host if request.client else "unknown"
            # Extract role from auth payload for per-role quotas
            role = "user"
            if isinstance(payload, dict):
                role = payload.get("role", "user")
            # Sync role quotas from config to limiter (if not already set)
            for quota_role, quota_cfg in rl_cfg.quotas.items():
                if quota_role not in limiter._role_quotas:
                    limiter.set_role_quota(
                        quota_role,
                        quota_cfg.get("max_requests", rl_cfg.max_requests),
                        quota_cfg.get("window_seconds", rl_cfg.window_seconds),
                        quota_cfg.get("burst", rl_cfg.burst),
                    )
            if not limiter.check(client_ip, role=role):
                _metrics.record_error(ErrorCode.RATE_EXCEEDED)
                max_req, window, burst = limiter.get_role_quota(role)
                return _make_error_response(
                    error="Rate limit exceeded",
                    code=ErrorCode.RATE_EXCEEDED,
                    http_status=status.HTTP_429_TOO_MANY_REQUESTS,
                    request_id=getattr(request.state, "request_id", None),
                    detail={
                        "retry_after": window,
                        "role": role,
                        "max_requests": max_req,
                        "burst": burst,
                        "remaining": limiter.get_remaining(client_ip, role=role),
                    },
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

        hc_cfg = rt.app_config.health_check
        components: list[ComponentHealth] = []

        # --- Storage ---
        storage_ok = True
        if hc_cfg.check_storage:
            storage_ok, detail = _check_storage(rt.factory)
            components.append(ComponentHealth(
                name="storage", healthy=storage_ok, detail=detail,
            ))

        # --- Queue ---
        queue_ok = True
        if hc_cfg.check_queue:
            queue_ok, detail = _check_queue(rt.factory)
            components.append(ComponentHealth(
                name="queue", healthy=queue_ok, detail=detail,
            ))

        # --- Embedding ---
        embedding_ok = True
        if hc_cfg.check_embedding:
            embedding_ok, detail = _check_embedding(rt.factory, rt.app_config)
            components.append(ComponentHealth(
                name="embedding", healthy=embedding_ok, detail=detail,
            ))

        # --- Search ---
        search_ok = True
        if hc_cfg.check_search:
            search_ok, detail = _check_search(rt.factory, rt.app_config)
            components.append(ComponentHealth(
                name="search", healthy=search_ok, detail=detail,
            ))

        # --- Tracer ---
        tracer_ok = True
        if hc_cfg.check_tracer:
            tracer_ok, detail = _check_tracer(rt.factory)
            components.append(ComponentHealth(
                name="tracer", healthy=tracer_ok, detail=detail,
            ))

        # Aggregate status
        checks = [c.healthy for c in components] if components else [True]
        healthy_count = sum(checks)
        if healthy_count == len(checks):
            overall = "ok"
        elif healthy_count == 0:
            overall = "unhealthy"
        else:
            overall = "degraded"

        auth_type = "none"
        if hasattr(rt.app_config, "auth"):
            auth_type = rt.app_config.auth.type

        return HealthResponse(
            status=overall,
            version=rt.app_config.app.version,
            agents=agents,
            default_agent=rt.app_config.runtime.default_agent,
            auth_enabled=_is_auth_enabled() or auth_type == "jwt",
            auth_type=auth_type,
            storage_connected=storage_ok,
            queue_connected=queue_ok,
            embedding_connected=embedding_ok,
            search_connected=search_ok,
            tracer_connected=tracer_ok,
            components=components,
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
    # Audit — read-only audit log query                                  #
    # ------------------------------------------------------------------ #
    @app.get("/audit/events", tags=["audit"])
    def list_audit_events(
        actor: str | None = Query(None, description="Filter by actor (user/agent)"),
        action: str | None = Query(None, description="Filter by action type (e.g. agent.invoke)"),
        resource: str | None = Query(None, description="Filter by resource identifier"),
        result: str | None = Query(None, description="Filter by result (success/failure/denied)"),
        since: str | None = Query(None, description="ISO timestamp, inclusive lower bound"),
        until: str | None = Query(None, description="ISO timestamp, exclusive upper bound"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ):
        """Query audit log events with filtering and pagination.

        Requires authentication. Returns events in descending timestamp order.
        When audit logging is disabled (``audit.enabled=false``), returns
        an empty list.
        """
        rt = get_runtime()
        manager = rt.factory.audit_manager

        from agentbase.core.audit import AuditFilter

        page_size_clamped = min(page_size, 100)
        offset = (page - 1) * page_size_clamped

        flt = AuditFilter(
            actor=actor,
            action=action,
            resource=resource,
            result=result,
            since=since,
            until=until,
            limit=page_size_clamped,
            offset=offset,
        )

        events = manager.query_events(flt)
        total = manager.count_events(flt)

        return {
            "items": [e.to_dict() for e in events],
            "total": total,
            "page": page,
            "page_size": page_size_clamped,
            "has_next": offset + page_size_clamped < total,
        }

    @app.get("/audit/events/count", tags=["audit"])
    def count_audit_events(
        actor: str | None = Query(None),
        action: str | None = Query(None),
        resource: str | None = Query(None),
        result: str | None = Query(None),
        since: str | None = Query(None),
        until: str | None = Query(None),
    ):
        """Count audit events matching the filter (without pagination).

        Requires authentication. Returns 0 when audit logging is disabled.
        """
        rt = get_runtime()
        manager = rt.factory.audit_manager

        from agentbase.core.audit import AuditFilter

        flt = AuditFilter(
            actor=actor,
            action=action,
            resource=resource,
            result=result,
            since=since,
            until=until,
        )

        return {"count": manager.count_events(flt)}

    @app.get("/audit/events/export", tags=["audit"])
    def export_audit_events(
        format: str = Query("json", description="Export format: json, csv, or yaml"),
        actor: str | None = Query(None, description="Filter by actor"),
        action: str | None = Query(None, description="Filter by action type"),
        resource: str | None = Query(None, description="Filter by resource"),
        result: str | None = Query(None, description="Filter by result"),
        since: str | None = Query(None, description="ISO timestamp, inclusive lower bound"),
        until: str | None = Query(None, description="ISO timestamp, exclusive upper bound"),
    ):
        """Export audit log events as a downloadable file.

        Supports JSON, CSV, and YAML formats. All filter parameters
        are optional — when omitted, all events (up to 10,000) are
        exported.

        The response includes a ``Content-Disposition`` header with a
        suggested filename (``audit_export.<ext>``).

        When audit logging is disabled (``audit.enabled=false``), returns
        an empty file with the appropriate format headers.
        """
        rt = get_runtime()
        manager = rt.factory.audit_manager

        from agentbase.core.audit import AuditFilter

        flt = AuditFilter(
            actor=actor,
            action=action,
            resource=resource,
            result=result,
            since=since,
            until=until,
            limit=10000,
        )

        content, events = manager.export_events_stream(format=format, filter=flt)

        # Determine media type and file extension
        if format == "csv":
            media_type = "text/csv"
            ext = "csv"
        elif format == "yaml":
            media_type = "application/x-yaml"
            ext = "yaml"
        else:
            media_type = "application/json"
            ext = "json"

        filename = f"audit_export.{ext}"

        return PlainTextResponse(
            content=content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Export-Count": str(len(events)),
            },
        )

    # ------------------------------------------------------------------ #
    # Experiments — A/B testing framework                                 #
    # ------------------------------------------------------------------ #
    @app.get("/experiments", tags=["experiments"])
    def list_experiments():
        """List all A/B testing experiments.

        Requires authentication. Returns empty list when experiments
        are disabled (``experiment.enabled=false``).
        """
        rt = get_runtime()
        manager = rt.factory.experiment_manager
        experiments = manager.list_experiments()
        return {
            "items": [e.to_dict() for e in experiments],
            "total": len(experiments),
            "enabled": manager.enabled,
        }

    @app.post("/experiments", tags=["experiments"])
    def create_experiment(req: CreateExperimentRequest):
        """Create a new A/B testing experiment.

        Requires authentication.
        """
        rt = get_runtime()
        manager = rt.factory.experiment_manager
        from agentbase.core.experiment import Variant

        if not req.variants:
            raise HTTPException(status_code=400, detail="At least one variant is required")

        variants = [
            Variant(
                name=v.name,
                weight=v.weight,
                model_override=v.model_override,
                system_prompt_override=v.system_prompt_override,
                metadata=v.metadata,
            )
            for v in req.variants
        ]

        exp = manager.create_experiment(
            name=req.name,
            description=req.description,
            variants=variants,
            strategy=req.strategy,
        )
        return exp.to_dict()

    @app.get("/experiments/{name}", tags=["experiments"])
    def get_experiment(name: str):
        """Get experiment details by name."""
        rt = get_runtime()
        manager = rt.factory.experiment_manager
        exp = manager.get_experiment(name)
        if exp is None:
            raise HTTPException(status_code=404, detail=f"Experiment not found: {name}")
        return exp.to_dict()

    @app.delete("/experiments/{name}", tags=["experiments"])
    def delete_experiment(name: str):
        """Delete an experiment and all its results."""
        rt = get_runtime()
        manager = rt.factory.experiment_manager
        deleted = manager.delete_experiment(name)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Experiment not found: {name}")
        return {"deleted": True, "name": name}

    @app.post("/experiments/{name}/assign", tags=["experiments"])
    def assign_variant(name: str, req: AssignRequest | None = None):
        """Assign a request to a variant in the experiment.
        """
        rt = get_runtime()
        manager = rt.factory.experiment_manager
        request_id = req.request_id if req else None
        assignment = manager.assign(name, request_id=request_id)
        return assignment.to_dict()

    @app.post("/experiments/{name}/results", tags=["experiments"])
    def record_experiment_result(name: str, req: RecordResultRequest):
        """Record a result for a variant in the experiment.
        """
        rt = get_runtime()
        manager = rt.factory.experiment_manager
        result = manager.record_result(
            experiment_name=name,
            variant_name=req.variant_name,
            success=req.success,
            duration_ms=req.duration_ms,
            output_text=req.output_text,
            error=req.error,
            request_id=req.request_id,
        )
        return result.to_dict()

    @app.get("/experiments/{name}/stats", tags=["experiments"])
    def get_experiment_stats(name: str):
        """Get aggregate statistics for an experiment."""
        rt = get_runtime()
        manager = rt.factory.experiment_manager
        stats = manager.get_stats(name)
        return stats.to_dict()

    # ------------------------------------------------------------------ #
    # Models — multi-model CRUD and testing                             #
    # ------------------------------------------------------------------ #
    @app.get("/models", tags=["models"])
    def list_models():
        """List all registered model configurations.

        Returns empty list when model management is disabled
        (``model_manager.enabled=false``).
        """
        mgr = _get_model_manager()
        if not mgr.enabled:
            return {"enabled": False, "items": [], "total": 0}
        models = mgr.list()
        return {
            "enabled": True,
            "items": [m.to_dict() for m in models],
            "total": len(models),
        }

    @app.post("/models", tags=["models"])
    def register_model(req: RegisterModelRequest):
        """Register a new model configuration or replace an existing one.

        If a model with the same name already exists, it will be updated.
        """
        mgr = _get_model_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="Model management is disabled. Set model_manager.enabled=true to enable.",
            )
        from agentbase.core.model_manager import ModelEntry

        entry = ModelEntry(
            name=req.name,
            provider=req.provider,
            model_name=req.model_name,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            timeout_seconds=req.timeout_seconds,
            base_url=req.base_url,
            api_key_env=req.api_key_env,
            extra=req.extra,
            description=req.description,
            enabled=req.enabled,
            tags=req.tags,
        )
        try:
            stored = mgr.register(entry)
            return stored.to_dict()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/models/{name}", tags=["models"])
    def get_model(name: str):
        """Get a model configuration by name."""
        mgr = _get_model_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="Model management is disabled.",
            )
        entry = mgr.get(name)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Model '{name}' not found")
        return entry.to_dict()

    @app.patch("/models/{name}", tags=["models"])
    def update_model(name: str, req: UpdateModelRequest):
        """Update fields on an existing model configuration.

        Only non-None fields in the request body are applied.
        """
        mgr = _get_model_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="Model management is disabled.",
            )
        changes = {k: v for k, v in req.model_dump().items() if v is not None}
        if not changes:
            raise HTTPException(status_code=400, detail="No fields to update")
        updated = mgr.update(name, changes)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"Model '{name}' not found")
        return updated.to_dict()

    @app.delete("/models/{name}", tags=["models"])
    def delete_model(name: str):
        """Delete a model configuration by name."""
        mgr = _get_model_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="Model management is disabled.",
            )
        deleted = mgr.delete(name)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Model '{name}' not found")
        return {"deleted": True, "name": name}

    @app.post("/models/{name}/test", tags=["models"])
    def test_model(name: str, req: TestModelRequest):
        """Test a model's connectivity by sending a simple prompt.

        Builds a LangChain model instance from the registered configuration
        and sends a test message. Returns response text and timing.
        """
        mgr = _get_model_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="Model management is disabled.",
            )
        result = mgr.test(name, prompt=req.prompt)
        return result.to_dict()

    # ------------------------------------------------------------------ #
    # Prompts — prompt template CRUD and rendering                       #
    # ------------------------------------------------------------------ #
    @app.get("/prompts", tags=["prompts"])
    def list_prompts(category: str | None = None):
        """List all registered prompt templates.

        Optional ``category`` query param filters by category.
        Returns empty list when prompt management is disabled.
        """
        mgr = _get_prompt_manager()
        if not mgr.enabled:
            return {"enabled": False, "items": [], "total": 0}
        templates = mgr.list()
        if category:
            templates = [t for t in templates if t.category == category]
        return {
            "enabled": True,
            "items": [t.to_dict() for t in templates],
            "total": len(templates),
        }

    @app.post("/prompts", tags=["prompts"])
    def register_prompt(req: RegisterPromptRequest):
        """Register a new prompt template or replace an existing one.

        If a template with the same name already exists, it will be updated.
        """
        mgr = _get_prompt_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="Prompt management is disabled. Set prompt_manager.enabled=true to enable.",
            )
        from agentbase.core.prompt import PromptTemplate

        template = PromptTemplate(
            name=req.name,
            content=req.content,
            variables=req.variables,
            description=req.description,
            category=req.category,
            tags=req.tags,
            version=req.version,
            enabled=req.enabled,
        )
        try:
            stored = mgr.register(template)
            return stored.to_dict()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/prompts/{name}", tags=["prompts"])
    def get_prompt(name: str):
        """Get a prompt template by name."""
        mgr = _get_prompt_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="Prompt management is disabled.",
            )
        template = mgr.get(name)
        if template is None:
            raise HTTPException(status_code=404, detail=f"Prompt template '{name}' not found")
        return template.to_dict()

    @app.patch("/prompts/{name}", tags=["prompts"])
    def update_prompt(name: str, req: UpdatePromptRequest):
        """Update fields on an existing prompt template.

        Only non-None fields in the request body are applied.
        """
        mgr = _get_prompt_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="Prompt management is disabled.",
            )
        changes = {k: v for k, v in req.model_dump().items() if v is not None}
        if not changes:
            raise HTTPException(status_code=400, detail="No fields to update")
        updated = mgr.update(name, changes)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"Prompt template '{name}' not found")
        return updated.to_dict()

    @app.delete("/prompts/{name}", tags=["prompts"])
    def delete_prompt(name: str):
        """Delete a prompt template by name."""
        mgr = _get_prompt_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="Prompt management is disabled.",
            )
        deleted = mgr.delete(name)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Prompt template '{name}' not found")
        return {"deleted": True, "name": name}

    @app.post("/prompts/{name}/render", tags=["prompts"])
    def render_prompt(name: str, req: RenderPromptRequest):
        """Render a prompt template by substituting variables.

        Uses ``str.format()`` for ``{variable}`` substitution.
        Returns the rendered prompt string.
        """
        mgr = _get_prompt_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="Prompt management is disabled.",
            )
        try:
            rendered = mgr.render(name, **req.variables)
            return {"name": name, "rendered": rendered}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ------------------------------------------------------------------ #
    # Users — user CRUD and authentication                               #
    # ------------------------------------------------------------------ #
    @app.get("/users", tags=["users"])
    def list_users():
        """List all registered users.

        Returns empty list when user management is disabled.
        Password hashes are never included in the response.
        """
        mgr = _get_user_manager()
        if not mgr.enabled:
            return {"enabled": False, "items": [], "total": 0}
        users = mgr.list()
        return {
            "enabled": True,
            "items": [u.to_dict() for u in users],
            "total": len(users),
        }

    @app.post("/users", tags=["users"])
    def register_user(req: RegisterUserRequest):
        """Register a new user or replace an existing one.

        If a user with the same username already exists, it will be updated.
        The password is hashed before storage.
        """
        mgr = _get_user_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="User management is disabled. Set user_manager.enabled=true to enable.",
            )
        try:
            stored = mgr.register(
                username=req.username,
                email=req.email,
                password=req.password,
                roles=req.roles,
                metadata=req.metadata,
            )
            return stored.to_dict()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/users/{username}", tags=["users"])
    def get_user(username: str):
        """Get a user by username."""
        mgr = _get_user_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="User management is disabled.",
            )
        user = mgr.get(username)
        if user is None:
            raise HTTPException(status_code=404, detail=f"User '{username}' not found")
        return user.to_dict()

    @app.patch("/users/{username}", tags=["users"])
    def update_user(username: str, req: UpdateUserRequest):
        """Update fields on an existing user.

        Only non-None fields in the request body are applied.
        If password is provided, it is hashed before storage.
        """
        mgr = _get_user_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="User management is disabled.",
            )
        changes = {k: v for k, v in req.model_dump().items() if v is not None}
        if not changes:
            raise HTTPException(status_code=400, detail="No fields to update")
        updated = mgr.update(username, changes)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"User '{username}' not found")
        return updated.to_dict()

    @app.delete("/users/{username}", tags=["users"])
    def delete_user(username: str):
        """Delete a user by username."""
        mgr = _get_user_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="User management is disabled.",
            )
        deleted = mgr.delete(username)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"User '{username}' not found")
        return {"deleted": True, "username": username}

    @app.post("/auth/register", tags=["users"])
    def auth_register(req: RegisterUserRequest):
        """Register a new user account (public endpoint for sign-up).

        This is functionally equivalent to ``POST /users`` but is provided
        as a separate endpoint for client-side clarity (sign-up flow).
        """
        mgr = _get_user_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="User management is disabled.",
            )
        try:
            stored = mgr.register(
                username=req.username,
                email=req.email,
                password=req.password,
                roles=req.roles,
                metadata=req.metadata,
            )
            return stored.to_dict()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/auth/login", tags=["users"])
    def auth_login(req: LoginRequest):
        """Authenticate a user and return user info.

        On success, returns the user entry (without password hash).
        On failure, returns 401.

        If JWT auth is configured, this endpoint can be extended to also
        return a JWT token. Currently it returns the user entry only.
        """
        mgr = _get_user_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="User management is disabled.",
            )
        user = mgr.authenticate(req.username, req.password)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return user.to_dict()

    # ------------------------------------------------------------------ #
    # OAuth2 — third-party login (Google / GitHub)                        #
    # ------------------------------------------------------------------ #
    _oauth2_mgr: Any = None
    _oauth2_lock = threading.Lock()

    def _get_oauth2_mgr() -> Any:
        """Get or create the OAuth2 manager singleton from app config."""
        nonlocal _oauth2_mgr
        if _oauth2_mgr is None:
            with _oauth2_lock:
                if _oauth2_mgr is None:
                    from agentbase.core.oauth2 import (
        OAuth2Manager,
        OAuth2ProviderConfig,
        set_oauth2_manager,
    )

                    rt = get_runtime()
                    cfg = rt.app_config.oauth2
                    providers = {}
                    for name, pcfg in cfg.providers.items():
                        providers[name] = OAuth2ProviderConfig(
                            name=name,
                            client_id=pcfg.client_id,
                            client_secret=pcfg.client_secret,
                            redirect_uri=pcfg.redirect_uri,
                            scopes=pcfg.scopes,
                            default_roles=pcfg.default_roles,
                        )
                    mgr = OAuth2Manager(
                        providers=providers,
                        enabled=cfg.enabled,
                    )
                    set_oauth2_manager(mgr)
                    _oauth2_mgr = mgr
        return _oauth2_mgr

    def _reset_oauth2_mgr() -> None:
        """Reset OAuth2 manager singleton (for testing)."""
        nonlocal _oauth2_mgr
        from agentbase.core.oauth2 import reset_oauth2_manager as _reset

        _oauth2_mgr = None
        _reset()

    @app.get("/auth/oauth2/{provider}/authorize", tags=["oauth2"])
    def oauth2_authorize(provider: str):
        """Redirect to the OAuth2 provider's authorization page.

        This is a public endpoint (no auth required) — it generates a
        random state token for CSRF protection and redirects the user's
        browser to the provider's consent screen.

        Path parameter:
            provider: ``google`` or ``github``
        """
        mgr = _get_oauth2_mgr()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="OAuth2 login is disabled. Set oauth2.enabled=true to enable.",
            )
        if not mgr.has_provider(provider):
            raise HTTPException(
                status_code=404,
                detail=f"OAuth2 provider '{provider}' is not configured.",
            )

        state = mgr.generate_state()
        url = mgr.get_authorize_url(provider, state=state)
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url=url, status_code=302)

    @app.get("/auth/oauth2/{provider}/callback", tags=["oauth2"])
    def oauth2_callback(
        provider: str,
        code: str = Query(..., description="Authorization code from provider"),
        state: str = Query(..., description="State token for CSRF verification"),
    ):
        """Handle the OAuth2 callback — exchange code, get user info, issue JWT.

        This endpoint:
        1. Validates the state token (CSRF protection)
        2. Exchanges the authorization code for an access token
        3. Fetches user info from the provider
        4. Auto-registers or matches the user in UserManager
        5. Issues a JWT token for subsequent API authentication

        Returns JSON with ``token`` (JWT), ``user`` (user info), and
        ``provider`` (provider name).
        """
        mgr = _get_oauth2_mgr()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="OAuth2 login is disabled.",
            )
        if not mgr.has_provider(provider):
            raise HTTPException(
                status_code=404,
                detail=f"OAuth2 provider '{provider}' is not configured.",
            )

        # 1. Validate state
        if not mgr.validate_state(state):
            raise HTTPException(
                status_code=400,
                detail="Invalid or expired state token (possible CSRF attack).",
            )

        # 2. Exchange code for access token
        try:
            token_data = mgr.exchange_code(provider, code=code)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"OAuth2 token exchange failed: {exc}",
            ) from exc

        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(
                status_code=502,
                detail=f"OAuth2 provider did not return an access token: {token_data}",
            )

        # 3. Fetch user info
        try:
            user_info = mgr.get_user_info(provider, access_token=access_token)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"OAuth2 user info fetch failed: {exc}",
            ) from exc

        # 4. Auto-register or match user in UserManager
        user_mgr = _get_user_manager()
        user_entry = None
        if user_mgr.enabled and user_info.email:
            # Try to find existing user by email
            existing = user_mgr.provider.get_by_email(user_info.email)
            if existing is not None:
                # Update metadata with OAuth2 info
                meta = dict(existing.metadata or {})
                meta["oauth2_provider"] = user_info.provider
                meta["oauth2_provider_user_id"] = user_info.provider_user_id
                if user_info.avatar_url:
                    meta["avatar_url"] = user_info.avatar_url
                if user_info.name:
                    meta["full_name"] = user_info.name
                user_entry = user_mgr.update(
                    existing.username, {"metadata": meta}
                )
            else:
                # Auto-register a new user
                cfg = mgr.get_provider_config(provider)
                username = f"{user_info.provider}_{user_info.provider_user_id}"
                user_entry = user_mgr.register(
                    username=username,
                    email=user_info.email,
                    password="",  # OAuth2 users don't have a password
                    roles=cfg.default_roles,
                    metadata={
                        "oauth2_provider": user_info.provider,
                        "oauth2_provider_user_id": user_info.provider_user_id,
                        "full_name": user_info.name,
                        "avatar_url": user_info.avatar_url,
                    },
                )

        # 5. Issue JWT token (if JWT auth is configured)
        jwt_token = None
        jwt_auth = _get_jwt_auth(get_runtime().app_config)
        if jwt_auth is not None and user_entry is not None:
            jwt_token = jwt_auth.create_token(
                user_id=user_entry.username,
                roles=user_entry.roles,
                extra_claims={
                    "oauth2_provider": user_info.provider,
                    "oauth2_user_id": user_info.provider_user_id,
                },
            )

        return {
            "provider": user_info.provider,
            "token": jwt_token,
            "user": user_entry.to_dict() if user_entry else None,
            "user_info": user_info.to_dict(),
        }

    @app.get("/auth/oauth2/providers", tags=["oauth2"])
    def list_oauth2_providers():
        """List configured OAuth2 providers.

        Returns a list of provider names and their configuration (without
        secrets).
        """
        mgr = _get_oauth2_mgr()
        if not mgr.enabled:
            return {"enabled": False, "providers": []}
        names = mgr.list_providers()
        return {
            "enabled": True,
            "providers": [
                mgr.get_provider_config(name).to_dict()
                for name in names
            ],
        }

    # ------------------------------------------------------------------ #
    # API Keys — multi-key CRUD, verification, and revocation             #
    # ------------------------------------------------------------------ #
    @app.get("/apikeys", tags=["apikeys"])
    def list_apikeys():
        """List all registered API keys.

        Returns empty list when API key management is disabled.
        Key hashes are never included in the response — only the
        key prefix (first 12 chars) for identification.
        """
        mgr = _get_apikey_manager()
        if not mgr.enabled:
            return {"enabled": False, "items": [], "total": 0}
        keys = mgr.list()
        return {
            "enabled": True,
            "items": [k.to_dict() for k in keys],
            "total": len(keys),
        }

    @app.post("/apikeys", tags=["apikeys"])
    def create_apikey(req: CreateApiKeyRequest):
        """Create a new API key.

        Returns the key entry and the **raw key string**.  The raw key
        is only visible once at creation time — it is hashed before
        storage and cannot be recovered.
        """
        mgr = _get_apikey_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="API key management is disabled. Set apikey_manager.enabled=true to enable.",
            )
        try:
            entry, raw_key = mgr.create(
                name=req.name,
                roles=req.roles,
                user_id=req.user_id,
                description=req.description,
                expires_at=req.expires_at,
                metadata=req.metadata,
            )
            return {
                **entry.to_dict(),
                "raw_key": raw_key,
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/apikeys/{key_id}", tags=["apikeys"])
    def get_apikey(key_id: str):
        """Get an API key by ID."""
        mgr = _get_apikey_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="API key management is disabled.",
            )
        entry = mgr.get(key_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"API key '{key_id}' not found")
        return entry.to_dict()

    @app.patch("/apikeys/{key_id}", tags=["apikeys"])
    def update_apikey(key_id: str, req: UpdateApiKeyRequest):
        """Update fields on an existing API key.

        Only non-None fields in the request body are applied.
        Updatable fields: name, roles, description, enabled, expires_at, metadata.
        """
        mgr = _get_apikey_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="API key management is disabled.",
            )
        changes = {k: v for k, v in req.model_dump().items() if v is not None}
        if not changes:
            raise HTTPException(status_code=400, detail="No fields to update")
        updated = mgr.update(key_id, changes)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"API key '{key_id}' not found")
        return updated.to_dict()

    @app.delete("/apikeys/{key_id}", tags=["apikeys"])
    def delete_apikey(key_id: str):
        """Delete an API key permanently."""
        mgr = _get_apikey_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="API key management is disabled.",
            )
        deleted = mgr.delete(key_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"API key '{key_id}' not found")
        return {"deleted": True, "key_id": key_id}

    @app.post("/apikeys/{key_id}/revoke", tags=["apikeys"])
    def revoke_apikey(key_id: str):
        """Revoke an API key by disabling it (without deleting).

        The key remains in storage but will fail verification.
        """
        mgr = _get_apikey_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="API key management is disabled.",
            )
        revoked = mgr.revoke(key_id)
        if revoked is None:
            raise HTTPException(status_code=404, detail=f"API key '{key_id}' not found")
        return {"revoked": True, "key_id": key_id}

    @app.post("/apikeys/verify", tags=["apikeys"])
    def verify_apikey(req: VerifyApiKeyRequest):
        """Verify an API key (check if it's valid, enabled, and not expired).

        Returns the key entry (without hash) on success, or an error
        on failure.  Updates usage stats on successful verification.
        """
        mgr = _get_apikey_manager()
        if not mgr.enabled:
            raise HTTPException(
                status_code=503,
                detail="API key management is disabled.",
            )
        entry = mgr.verify(req.key)
        if entry is None:
            return {"valid": False, "reason": "invalid or revoked"}
        return {"valid": True, "key": entry.to_dict()}

    # ------------------------------------------------------------------ #
    # Sessions — session management and conversation history           #
    # ------------------------------------------------------------------ #
    @app.get("/sessions", tags=["sessions"])
    def list_sessions(
        agent: str | None = None,
        status: str | None = None,
    ):
        """List all sessions, optionally filtered by agent name or status.

        Returns session metadata (thread_id, agent_name, status, timestamps).
        Does not include conversation messages — use the checkpoint API or
        the agent's ``/resume`` endpoint to access message history.

        Query params:
            agent: Filter by agent name.
            status: Filter by session status (pending/running/completed/failed/cancelled).
        """
        from agentbase.runtime.session import get_session_registry

        registry = get_session_registry()
        if status:
            # Filter by status
            from agentbase.runtime.session import SessionStatus

            try:
                status_enum = SessionStatus(status)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid status '{status}'. Valid values: {', '.join(s.value for s in SessionStatus)}",
                ) from None
            # Get all sessions and filter
            all_sessions = registry.list_by_agent(agent) if agent else [
                s for s in registry._sessions.values()
            ]
            sessions = [s for s in all_sessions if s.status == status_enum]
        elif agent:
            sessions = registry.list_by_agent(agent)
        else:
            # Return all sessions
            with registry._lock:
                sessions = list(registry._sessions.values())
        return {
            "items": [s.to_dict() for s in sessions],
            "total": len(sessions),
        }

    @app.get("/sessions/stats", tags=["sessions"])
    def get_session_stats():
        """Get session statistics — counts by status.

        Returns a dict with counts for each status (pending, running,
        completed, failed, cancelled) and a total.
        """
        from agentbase.runtime.session import get_session_registry

        registry = get_session_registry()
        return registry.count_by_status()

    @app.get("/sessions/{thread_id}", tags=["sessions"])
    def get_session(thread_id: str):
        """Get session details by thread ID.

        Returns the session metadata (status, timestamps, agent_name, etc.).
        Does not include conversation messages — use the agent's ``/resume``
        endpoint to resume and access message history.
        """
        from agentbase.runtime.session import get_session_registry

        registry = get_session_registry()
        session = registry.get(thread_id)
        if session is None:
            raise HTTPException(
                status_code=404,
                detail=f"Session '{thread_id}' not found",
            )
        return session.to_dict()

    @app.delete("/sessions/{thread_id}", tags=["sessions"])
    def cancel_session(thread_id: str):
        """Cancel a session by marking it as cancelled.

        This does not interrupt a running agent invocation — it only updates
        the session status. Use the queue's ``DELETE /queue/{task_id}`` to
        cancel async tasks.
        """
        from agentbase.runtime.session import SessionStatus, get_session_registry

        registry = get_session_registry()
        session = registry.get(thread_id)
        if session is None:
            raise HTTPException(
                status_code=404,
                detail=f"Session '{thread_id}' not found",
            )
        if session.status in {SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED}:
            raise HTTPException(
                status_code=409,
                detail=f"Session already in terminal state: {session.status.value}",
            )
        session.mark_cancelled()
        return {"cancelled": True, "thread_id": thread_id}

    @app.post("/sessions/cleanup", tags=["sessions"])
    def cleanup_sessions(
        mode: str = "expired",
        timeout_seconds: int = 300,
    ):
        """Clean up sessions based on the specified mode.

        Modes:
            expired: Remove sessions that have expired based on their TTL.
            stale: Mark sessions running longer than ``timeout_seconds`` as failed.
            completed: Remove all completed/failed/cancelled sessions.

        Returns counts of affected sessions.
        """
        from agentbase.runtime.session import get_session_registry

        registry = get_session_registry()
        if mode == "expired":
            cleaned = registry.cleanup_expired()
            return {"mode": "expired", "cleaned": cleaned}
        elif mode == "stale":
            cleaned = registry.cleanup_stale(timeout_seconds=timeout_seconds)
            return {"mode": "stale", "cleaned": cleaned, "timeout_seconds": timeout_seconds}
        elif mode == "completed":
            removed = registry.clear(keep_active=True)
            return {"mode": "completed", "removed": removed}
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid mode '{mode}'. Valid values: expired, stale, completed",
            )

    # ------------------------------------------------------------------ #
    # Usage tracking — token & cost statistics                           #
    # ------------------------------------------------------------------ #
    @app.get("/usage/stats", tags=["usage"])
    def get_usage_stats(
        agent: str | None = Query(None),
        model: str | None = Query(None),
        user: str | None = Query(None),
        thread_id: str | None = Query(None),
        since: str | None = Query(None),
        until: str | None = Query(None),
    ):
        """Get aggregated usage statistics (token counts, costs, breakdowns).

        Requires authentication. Returns 0-values when usage tracking is
        disabled (``usage.enabled=false``).

        Query params:
            agent: Filter by agent name.
            model: Filter by model name.
            user: Filter by user identifier.
            thread_id: Filter by thread ID.
            since: ISO timestamp, inclusive.
            until: ISO timestamp, exclusive.
        """
        from agentbase.core.usage import UsageFilter

        mgr = _get_usage_manager()
        flt = UsageFilter(
            agent=agent,
            model=model,
            user=user,
            thread_id=thread_id,
            since=since,
            until=until,
            limit=0,  # no limit for stats
        )
        stats = mgr.get_stats(flt)
        return stats.to_dict()

    @app.get("/usage/records", tags=["usage"])
    def list_usage_records(
        agent: str | None = Query(None),
        model: str | None = Query(None),
        user: str | None = Query(None),
        thread_id: str | None = Query(None),
        since: str | None = Query(None),
        until: str | None = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ):
        """List usage records with filtering and pagination.

        Requires authentication. Returns records in descending timestamp
        order. Returns empty list when usage tracking is disabled.
        """
        from agentbase.core.usage import UsageFilter

        mgr = _get_usage_manager()
        page_size_clamped = min(page_size, 100)
        offset = (page - 1) * page_size_clamped
        flt = UsageFilter(
            agent=agent,
            model=model,
            user=user,
            thread_id=thread_id,
            since=since,
            until=until,
            limit=page_size_clamped,
            offset=offset,
        )
        records = mgr.query_records(flt)
        total = mgr.count_records(UsageFilter(
            agent=agent,
            model=model,
            user=user,
            thread_id=thread_id,
            since=since,
            until=until,
            limit=0,
            offset=0,
        ))
        return {
            "items": [r.to_dict() for r in reversed(records)],  # newest first
            "total": total,
            "page": page,
            "page_size": page_size_clamped,
        }

    @app.get("/usage/summary", tags=["usage"])
    def get_usage_summary():
        """Get a high-level usage summary (totals only, no breakdowns).

        Requires authentication. Returns 0-values when disabled.
        """
        mgr = _get_usage_manager()
        stats = mgr.get_stats()
        return {
            "enabled": mgr.enabled,
            "total_calls": stats.total_calls,
            "total_prompt_tokens": stats.total_prompt_tokens,
            "total_completion_tokens": stats.total_completion_tokens,
            "total_tokens": stats.total_tokens,
            "total_cost_usd": round(stats.total_cost_usd, 6),
            "avg_duration_ms": round(stats.avg_duration_ms, 2),
        }

    @app.delete("/usage/records", tags=["usage"])
    def clear_usage_records():
        """Clear all usage records. Requires admin role.

        Returns the count of deleted records.
        """
        mgr = _get_usage_manager()
        deleted = mgr.clear_records()
        return {"deleted": deleted}

    # ------------------------------------------------------------------ #
    # Webhook management — endpoint CRUD, delivery records, test         #
    # ------------------------------------------------------------------ #
    @app.get("/webhooks", tags=["webhooks"])
    def list_webhooks(active_only: bool = Query(False)):
        """List all registered webhook endpoints.

        Requires authentication. Returns empty list when webhook
        notifications are disabled.
        """
        mgr = _get_webhook_manager()
        endpoints = mgr.list_endpoints(active_only=active_only)
        return {"items": [e.to_dict() for e in endpoints], "total": len(endpoints)}

    @app.post("/webhooks", tags=["webhooks"])
    def create_webhook(
        url: str = Body(..., embed=True),
        events: list[str] = Body(default_factory=lambda: ["*"], embed=True),
        secret: str = Body("", embed=True),
        description: str = Body("", embed=True),
        active: bool = Body(True, embed=True),
    ):
        """Register a new webhook endpoint.

        Requires authentication. The ``url`` must be a valid HTTP(S) URL.
        ``events`` is a list of event types to subscribe to (``["*"]`` = all).
        """
        mgr = _get_webhook_manager()
        try:
            endpoint = mgr.register_endpoint(
                url=url,
                events=events,
                secret=secret,
                description=description,
                active=active,
            )
            return endpoint.to_dict()
        except Exception as exc:
            from agentbase.runtime.errors import RegistryError
            if isinstance(exc, RegistryError):
                raise HTTPException(status_code=400, detail=str(exc))
            raise

    @app.get("/webhooks/deliveries", tags=["webhooks"])
    def list_webhook_deliveries(
        endpoint_id: str | None = Query(None),
        event: str | None = Query(None),
        status: str | None = Query(None),
        since: str | None = Query(None),
        until: str | None = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ):
        """List webhook delivery records with filtering and pagination.

        Requires authentication. Returns records in descending timestamp order.
        """
        from agentbase.core.webhook import WebhookDeliveryFilter

        mgr = _get_webhook_manager()
        offset = (page - 1) * page_size
        flt = WebhookDeliveryFilter(
            endpoint_id=endpoint_id,
            event=event,
            status=status,
            since=since,
            until=until,
            limit=page_size,
            offset=offset,
        )
        records = mgr.query_deliveries(flt)
        return {
            "items": [r.to_dict() for r in reversed(records)],
            "total": len(records),
            "page": page,
            "page_size": page_size,
        }

    @app.get("/webhooks/stats", tags=["webhooks"])
    def get_webhook_stats():
        """Get aggregate webhook delivery statistics.

        Requires authentication. Returns 0-values when disabled.
        """
        mgr = _get_webhook_manager()
        stats = mgr.get_stats()
        return stats.to_dict()

    @app.get("/webhooks/{endpoint_id}", tags=["webhooks"])
    def get_webhook(endpoint_id: str):
        """Get webhook endpoint details.

        Requires authentication. Returns 404 if not found.
        """
        mgr = _get_webhook_manager()
        endpoint = mgr.get_endpoint(endpoint_id)
        if endpoint is None:
            raise HTTPException(status_code=404, detail=f"Webhook endpoint not found: {endpoint_id}")
        return endpoint.to_dict()

    @app.patch("/webhooks/{endpoint_id}", tags=["webhooks"])
    def update_webhook(
        endpoint_id: str,
        url: str | None = Body(None, embed=True),
        events: list[str] | None = Body(None, embed=True),
        secret: str | None = Body(None, embed=True),
        description: str | None = Body(None, embed=True),
        active: bool | None = Body(None, embed=True),
    ):
        """Update webhook endpoint fields.

        Requires authentication. Only provided fields are updated.
        """
        mgr = _get_webhook_manager()
        result = mgr.update_endpoint(
            endpoint_id,
            url=url,
            events=events,
            secret=secret,
            description=description,
            active=active,
        )
        if result is None:
            raise HTTPException(status_code=404, detail=f"Webhook endpoint not found: {endpoint_id}")
        return result.to_dict()

    @app.delete("/webhooks/{endpoint_id}", tags=["webhooks"])
    def delete_webhook(endpoint_id: str):
        """Delete a webhook endpoint.

        Requires authentication. Returns 404 if not found.
        """
        mgr = _get_webhook_manager()
        deleted = mgr.delete_endpoint(endpoint_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Webhook endpoint not found: {endpoint_id}")
        return {"deleted": True, "endpoint_id": endpoint_id}

    @app.post("/webhooks/{endpoint_id}/test", tags=["webhooks"])
    def test_webhook(endpoint_id: str):
        """Send a test event to a webhook endpoint.

        Requires authentication. Delivery is synchronous (not background)
        so the caller gets immediate feedback. Returns the delivery record.
        """
        mgr = _get_webhook_manager()
        if not mgr.enabled:
            return {"status": "disabled", "message": "Webhook notifications are disabled"}
        delivery = mgr.test_endpoint(endpoint_id)
        if delivery is None:
            raise HTTPException(status_code=404, detail=f"Webhook endpoint not found: {endpoint_id}")
        return delivery.to_dict()

    # Note: /webhooks/deliveries and /webhooks/stats routes are declared
    # above (before /webhooks/{endpoint_id}) to avoid path parameter capture.

    # ------------------------------------------------------------------ #
    # Feedback management — user ratings, comments, and tags            #
    # ------------------------------------------------------------------ #
    @app.get("/feedback", tags=["feedback"])
    def list_feedback(
        thread_id: str | None = Query(None),
        message_id: str | None = Query(None),
        user_id: str | None = Query(None),
        agent_name: str | None = Query(None),
        sentiment: str | None = Query(None),
        min_rating: float | None = Query(None),
        max_rating: float | None = Query(None),
        since: str | None = Query(None),
        until: str | None = Query(None),
        tags: str | None = Query(None, description="Comma-separated tags to filter by"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ):
        """List user feedback records with filtering and pagination.

        Requires authentication. Returns records in descending timestamp
        order. Returns empty list when feedback collection is disabled.
        """
        mgr = _get_feedback_manager()
        tag_list = [t.strip() for t in tags.split(",")] if tags else None
        offset = (page - 1) * page_size
        records = mgr.list_feedback(
            thread_id=thread_id,
            message_id=message_id,
            user_id=user_id,
            agent_name=agent_name,
            sentiment=sentiment,
            min_rating=min_rating,
            max_rating=max_rating,
            since=since,
            until=until,
            tags=tag_list,
            limit=page_size,
            offset=offset,
        )
        return {
            "items": [r.to_dict() for r in records],
            "total": len(records),
            "page": page,
            "page_size": page_size,
        }

    @app.post("/feedback", tags=["feedback"])
    def create_feedback(
        thread_id: str = Body(..., embed=True),
        message_id: str = Body("", embed=True),
        rating: float | None = Body(None, embed=True),
        comment: str = Body("", embed=True),
        user_id: str = Body("", embed=True),
        agent_name: str = Body("", embed=True),
        tags: list[str] = Body(default_factory=list, embed=True),
        metadata: dict[str, Any] = Body(default_factory=dict, embed=True),
    ):
        """Submit user feedback for an agent response.

        Requires authentication. ``thread_id`` is required — it links the
        feedback to a specific conversation thread.

        ``rating`` can be:
        - 1-5 for star ratings (5 = best)
        - -1/+1 for thumbs down/up
        - ``null`` for comment-only feedback

        ``comment`` is optional free-text feedback.
        ``tags`` is an optional list of categorisation tags.
        """
        mgr = _get_feedback_manager()
        try:
            record = mgr.create_feedback(
                thread_id=thread_id,
                message_id=message_id,
                rating=rating,
                comment=comment,
                user_id=user_id,
                agent_name=agent_name,
                tags=tags,
                metadata=metadata,
            )
            return record.to_dict()
        except Exception as exc:
            from agentbase.runtime.errors import RegistryError
            if isinstance(exc, RegistryError):
                raise HTTPException(status_code=400, detail=str(exc))
            raise

    @app.get("/feedback/stats", tags=["feedback"])
    def get_feedback_stats(
        agent_name: str | None = Query(None),
        thread_id: str | None = Query(None),
        since: str | None = Query(None),
        until: str | None = Query(None),
    ):
        """Get aggregate feedback statistics.

        Requires authentication. Returns 0-values when disabled or
        when no records match the filters.
        """
        mgr = _get_feedback_manager()
        stats = mgr.get_stats(
            agent_name=agent_name,
            thread_id=thread_id,
            since=since,
            until=until,
        )
        return stats.to_dict()

    @app.get("/feedback/{record_id}", tags=["feedback"])
    def get_feedback(record_id: str):
        """Get a feedback record by ID.

        Requires authentication. Returns 404 if not found.
        """
        mgr = _get_feedback_manager()
        record = mgr.get_feedback(record_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Feedback record not found: {record_id}")
        return record.to_dict()

    @app.patch("/feedback/{record_id}", tags=["feedback"])
    def update_feedback(
        record_id: str,
        rating: float | None = Body(None, embed=True),
        comment: str | None = Body(None, embed=True),
        tags: list[str] | None = Body(None, embed=True),
        metadata: dict[str, Any] | None = Body(None, embed=True),
    ):
        """Update a feedback record.

        Requires authentication. Only provided fields are updated.
        Returns 404 if the record doesn't exist.
        """
        mgr = _get_feedback_manager()
        result = mgr.update_feedback(
            record_id,
            rating=rating,
            comment=comment,
            tags=tags,
            metadata=metadata,
        )
        if result is None:
            raise HTTPException(status_code=404, detail=f"Feedback record not found: {record_id}")
        return result.to_dict()

    @app.delete("/feedback/{record_id}", tags=["feedback"])
    def delete_feedback(record_id: str):
        """Delete a feedback record.

        Requires authentication. Returns 404 if not found.
        """
        mgr = _get_feedback_manager()
        deleted = mgr.delete_feedback(record_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Feedback record not found: {record_id}")
        return {"deleted": True, "record_id": record_id}

    # Note: /feedback/stats route is declared before /feedback/{record_id}
    # to avoid path parameter capture.

    # ------------------------------------------------------------------ #
    # Notification center — in-app notifications                        #
    # ------------------------------------------------------------------ #
    @app.get("/notifications", tags=["notifications"])
    def list_notifications(
        user_id: str | None = Query(None),
        category: str | None = Query(None),
        severity: str | None = Query(None),
        unread_only: bool = Query(False),
        since: str | None = Query(None),
        until: str | None = Query(None),
        include_broadcast: bool = Query(True),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ):
        """List notifications with filtering and pagination.

        Requires authentication. Returns notifications in descending timestamp
        order. Returns empty list when notifications are disabled.
        """
        mgr = _get_notification_manager()
        offset = (page - 1) * page_size
        records = mgr.list_notifications(
            user_id=user_id,
            category=category,
            severity=severity,
            unread_only=unread_only,
            since=since,
            until=until,
            include_broadcast=include_broadcast,
            limit=page_size,
            offset=offset,
        )
        return {
            "items": [n.to_dict() for n in records],
            "total": len(records),
            "page": page,
            "page_size": page_size,
        }

    @app.post("/notifications", tags=["notifications"])
    def create_notification(
        user_id: str = Body(..., embed=True),
        title: str = Body(..., embed=True),
        message: str = Body("", embed=True),
        category: str = Body("system", embed=True),
        severity: str = Body("info", embed=True),
        action_url: str = Body("", embed=True),
        action_label: str = Body("", embed=True),
        metadata: dict[str, Any] = Body(default_factory=dict, embed=True),
        expires_at: str = Body("", embed=True),
    ):
        """Create a new notification for a specific user.

        Requires authentication. ``user_id`` and ``title`` are required.
        Use ``"*"`` as ``user_id`` to broadcast to all users.
        """
        mgr = _get_notification_manager()
        try:
            record = mgr.create_notification(
                user_id=user_id,
                title=title,
                message=message,
                category=category,
                severity=severity,
                action_url=action_url,
                action_label=action_label,
                metadata=metadata,
                expires_at=expires_at,
            )
            return record.to_dict()
        except Exception as exc:
            from agentbase.runtime.errors import RegistryError
            if isinstance(exc, RegistryError):
                raise HTTPException(status_code=400, detail=str(exc))
            raise

    @app.get("/notifications/stats", tags=["notifications"])
    def get_notification_stats(
        user_id: str | None = Query(None),
        category: str | None = Query(None),
        since: str | None = Query(None),
        until: str | None = Query(None),
    ):
        """Get aggregate notification statistics.

        Requires authentication. Returns 0-values when disabled or when
        no records match the filters.
        """
        mgr = _get_notification_manager()
        stats = mgr.get_stats(
            user_id=user_id,
            category=category,
            since=since,
            until=until,
        )
        return stats.to_dict()

    @app.get("/notifications/unread-count", tags=["notifications"])
    def get_unread_count(user_id: str = Query(...)):
        """Get the unread notification count for a specific user.

        Requires authentication. Includes broadcast notifications that
        haven't been marked as read.
        """
        mgr = _get_notification_manager()
        count = mgr.get_unread_count(user_id)
        return {"user_id": user_id, "unread_count": count}

    @app.post("/notifications/broadcast", tags=["notifications"])
    def broadcast_notification(
        title: str = Body(..., embed=True),
        message: str = Body("", embed=True),
        category: str = Body("system", embed=True),
        severity: str = Body("info", embed=True),
        action_url: str = Body("", embed=True),
        action_label: str = Body("", embed=True),
        metadata: dict[str, Any] = Body(default_factory=dict, embed=True),
        expires_at: str = Body("", embed=True),
    ):
        """Broadcast a notification to all users.

        Requires authentication. Creates a notification with ``user_id="*"``
        which is included in all users' notification lists.
        """
        mgr = _get_notification_manager()
        try:
            record = mgr.broadcast(
                title=title,
                message=message,
                category=category,
                severity=severity,
                action_url=action_url,
                action_label=action_label,
                metadata=metadata,
                expires_at=expires_at,
            )
            return record.to_dict()
        except Exception as exc:
            from agentbase.runtime.errors import RegistryError
            if isinstance(exc, RegistryError):
                raise HTTPException(status_code=400, detail=str(exc))
            raise

    @app.post("/notifications/read-all", tags=["notifications"])
    def mark_all_read(user_id: str = Body(..., embed=True)):
        """Mark all notifications for a user as read.

        Requires authentication. Also marks broadcast notifications as read
        for this user. Returns the number of notifications marked as read.
        """
        mgr = _get_notification_manager()
        count = mgr.mark_all_read(user_id)
        return {"user_id": user_id, "marked_read": count}

    @app.get("/notifications/{notification_id}", tags=["notifications"])
    def get_notification(notification_id: str):
        """Get a notification by ID.

        Requires authentication. Returns 404 if not found.
        """
        mgr = _get_notification_manager()
        record = mgr.get_notification(notification_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Notification not found: {notification_id}")
        return record.to_dict()

    @app.patch("/notifications/{notification_id}", tags=["notifications"])
    def update_notification(
        notification_id: str,
        title: str | None = Body(None, embed=True),
        message: str | None = Body(None, embed=True),
        category: str | None = Body(None, embed=True),
        severity: str | None = Body(None, embed=True),
        action_url: str | None = Body(None, embed=True),
        action_label: str | None = Body(None, embed=True),
        metadata: dict[str, Any] | None = Body(None, embed=True),
        expires_at: str | None = Body(None, embed=True),
    ):
        """Update a notification.

        Requires authentication. Only provided fields are updated.
        Returns 404 if not found.
        """
        mgr = _get_notification_manager()
        result = mgr.update_notification(
            notification_id,
            title=title,
            message=message,
            category=category,
            severity=severity,
            action_url=action_url,
            action_label=action_label,
            metadata=metadata,
            expires_at=expires_at,
        )
        if result is None:
            raise HTTPException(status_code=404, detail=f"Notification not found: {notification_id}")
        return result.to_dict()

    @app.post("/notifications/{notification_id}/read", tags=["notifications"])
    def mark_notification_read(notification_id: str):
        """Mark a notification as read.

        Requires authentication. Returns 404 if not found.
        """
        mgr = _get_notification_manager()
        result = mgr.mark_read(notification_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Notification not found: {notification_id}")
        return result.to_dict()

    @app.post("/notifications/{notification_id}/unread", tags=["notifications"])
    def mark_notification_unread(notification_id: str):
        """Mark a notification as unread.

        Requires authentication. Returns 404 if not found.
        """
        mgr = _get_notification_manager()
        result = mgr.mark_unread(notification_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Notification not found: {notification_id}")
        return result.to_dict()

    @app.delete("/notifications/{notification_id}", tags=["notifications"])
    def delete_notification(notification_id: str):
        """Delete a notification.

        Requires authentication. Returns 404 if not found.
        """
        mgr = _get_notification_manager()
        deleted = mgr.delete_notification(notification_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Notification not found: {notification_id}")
        return {"deleted": True, "notification_id": notification_id}

    # Note: /notifications/stats, /notifications/unread-count,
    # /notifications/broadcast, /notifications/read-all routes are declared
    # before /notifications/{notification_id} to avoid path parameter capture.

    # ------------------------------------------------------------------ #
    # Conversations — conversation history management                   #
    # ------------------------------------------------------------------ #
    @app.get("/conversations", tags=["conversations"])
    def list_conversations(
        user_id: str | None = None,
        agent_name: str | None = None,
        archived: bool | None = None,
        tag: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
    ):
        """List conversations with optional filtering and pagination.

        Query params:
            user_id: Filter by user ID.
            agent_name: Filter by agent name.
            archived: Filter by archived status (true/false).
            tag: Filter by tag.
            start_time: Filter by creation time (ISO 8601, inclusive).
            end_time: Filter by creation time (ISO 8601, inclusive).
            limit: Maximum results (default 100, capped at 500).
            offset: Pagination offset.
            sort_by: Sort field — updated_at/created_at/message_count.
            sort_order: Sort order — asc/desc.
        """
        mgr = _get_conversation_manager()
        limit = min(max(limit, 1), 500)
        offset = max(offset, 0)
        convs = mgr.list_conversations(
            user_id=user_id,
            agent_name=agent_name,
            archived=archived,
            tag=tag,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        total = mgr.count(
            user_id=user_id,
            agent_name=agent_name,
        )
        return {
            "items": [c.to_dict(include_messages=False) for c in convs],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/conversations/stats", tags=["conversations"])
    def get_conversation_stats(
        user_id: str | None = None,
        agent_name: str | None = None,
    ):
        """Get aggregate statistics for conversations.

        Query params:
            user_id: Limit stats to a specific user.
            agent_name: Limit stats to a specific agent.
        """
        mgr = _get_conversation_manager()
        stats = mgr.get_stats(user_id=user_id, agent_name=agent_name)
        return stats.to_dict()

    @app.get("/conversations/{thread_id}", tags=["conversations"])
    def get_conversation_history(
        thread_id: str,
        include_messages: bool = True,
    ):
        """Get conversation history by thread ID.

        Returns the full conversation including messages.

        Query params:
            include_messages: Whether to include messages (default true).

        Returns 404 if the conversation is not found.
        """
        mgr = _get_conversation_manager()
        conv = mgr.get_history(thread_id=thread_id, include_messages=include_messages)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"Conversation not found: {thread_id}")
        return conv.to_dict(include_messages=include_messages)

    @app.patch("/conversations/{thread_id}", tags=["conversations"])
    def update_conversation(thread_id: str, body: dict[str, Any]):
        """Update conversation metadata (title, tags, archived, metadata).

        All fields are optional — only provided fields are updated.

        Returns 404 if the conversation is not found.
        """
        mgr = _get_conversation_manager()
        try:
            conv = mgr.update_conversation(
                thread_id=thread_id,
                title=body.get("title"),
                tags=body.get("tags"),
                archived=body.get("archived"),
                metadata=body.get("metadata"),
            )
        except Exception as exc:
            if "not found" in str(exc).lower() or "not_found" in str(exc.code).lower():
                raise HTTPException(status_code=404, detail=f"Conversation not found: {thread_id}") from exc
            raise
        return conv.to_dict(include_messages=False)

    @app.delete("/conversations/{thread_id}", tags=["conversations"])
    def delete_conversation(thread_id: str):
        """Delete a conversation and all its messages.

        Returns 404 if the conversation is not found.
        """
        mgr = _get_conversation_manager()
        deleted = mgr.delete_conversation(thread_id=thread_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Conversation not found: {thread_id}")
        return {"deleted": True, "thread_id": thread_id}

    # ------------------------------------------------------------------ #
    # Rate-limit admin — quota management                                #
    # ------------------------------------------------------------------ #
    @app.get("/admin/rate-limit", tags=["admin"])
    def get_rate_limit_stats():
        """Get rate limiter statistics including per-role quotas and active buckets.

        Requires admin role.
        """
        rt = get_runtime()
        rl_cfg = rt.app_config.rate_limit
        if not rl_cfg.enabled:
            return {"enabled": False, "message": "Rate limiting is disabled"}
        limiter = _get_rate_limiter()
        return limiter.stats

    @app.post("/admin/rate-limit/quotas/{role}", tags=["admin"])
    def set_role_quota(role: str, max_requests: int = 60, window_seconds: int = 60, burst: int = 10):
        """Dynamically set a per-role rate limit quota.

        Requires admin role. Changes are applied in-memory and reset
        on server restart (persist in config for permanent changes).
        """
        rt = get_runtime()
        rl_cfg = rt.app_config.rate_limit
        if not rl_cfg.enabled:
            return {"enabled": False, "message": "Rate limiting is disabled"}
        limiter = _get_rate_limiter()
        limiter.set_role_quota(role, max_requests, window_seconds, burst)
        return {
            "role": role,
            "max_requests": max_requests,
            "window_seconds": window_seconds,
            "burst": burst,
            "capacity": max_requests + burst,
        }

    @app.delete("/admin/rate-limit/buckets", tags=["admin"])
    def reset_rate_limit_buckets():
        """Reset all rate limit buckets (clear all counters).

        Requires admin role. Useful for testing or after configuration changes.
        """
        rt = get_runtime()
        rl_cfg = rt.app_config.rate_limit
        if not rl_cfg.enabled:
            return {"enabled": False, "message": "Rate limiting is disabled"}
        limiter = _get_rate_limiter()
        limiter.reset()
        return {"reset": True}

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
