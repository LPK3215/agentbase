"""Structured error system for agentbase.

Error codes follow the pattern ``AGENTBASE_<DOMAIN>_<NNN>`` where:

- ``CONFIG``       — configuration loading / validation (001–020)
- ``REG``          — registry lookup / registration (001–020)
- ``FACTORY``      — component assembly (001–020)
- ``RT``           — runtime execution (001–020)
- ``AUTH``         — authentication / authorization (001–020)
- ``RATE``         — rate limiting (001–020)
- ``QUEUE``        — queue operations (001–020)
- ``KB``           — knowledge base operations (001–020)
- ``WS``           — WebSocket operations (001–020)
- ``UPLOAD``       — file upload operations (001–020)
- ``MIGRATION``    — database migration operations (001–020)
- ``USAGE``        — usage tracking operations (001–020)
- ``WEBHOOK``      — webhook notification operations (001–020)
- ``FEEDBACK``     — user feedback operations (001–020)
- ``NOTIFICATION`` — notification center operations (001–020)
- ``CONVERSATION`` — conversation history operations (001–020)
- ``SCHEDULE``     — scheduled task operations (001–020)
- ``CALENDAR``     — calendar event operations (001–020)
- ``SYSCONFIG``    — system config operations (001–020)
- ``RBAC``         — role/permission operations (001–020)
- ``ALERT``        — alert rule/event operations (001–020)

Every error carries:
- ``code``: stable machine-readable string
- ``message``: human-readable description
- ``http_status``: suggested HTTP status code (default 500)
- ``detail``: optional structured payload for debugging
"""
from __future__ import annotations

from typing import Any


class AgentbaseError(Exception):
    """Base error for the harness.

    Attributes:
        code: Stable error code for programmatic handling.
        http_status: Suggested HTTP status code for API responses.
        detail: Optional structured debugging payload.
    """

    code: str = "AGENTBASE_RT_999"
    http_status: int = 500
    default_message: str = "An unexpected error occurred"

    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        http_status: int | None = None,
        detail: Any | None = None,
    ) -> None:
        super().__init__(message or self.default_message)
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        """Serialise the error for API JSON responses."""
        result = {
            "error": str(self),
            "code": self.code,
            "http_status": self.http_status,
            "detail": self.detail,
        }
        # Include request_id if available (set by API middleware)
        request_id = getattr(self, "request_id", None)
        if request_id:
            result["request_id"] = request_id
        return result

    def with_request_id(self, request_id: str) -> AgentbaseError:
        """Attach a request_id to this error for tracing.

        Returns ``self`` for method chaining.
        """
        self.request_id = request_id
        return self


class ConfigError(AgentbaseError):
    """Raised when configuration is missing or invalid."""

    code = "AGENTBASE_CONFIG_001"
    http_status = 500
    default_message = "Configuration error"


class RegistryError(AgentbaseError):
    """Raised when a registry lookup or registration fails."""

    code = "AGENTBASE_REG_001"
    http_status = 500
    default_message = "Registry error"


class FactoryError(AgentbaseError):
    """Raised when a component cannot be assembled."""

    code = "AGENTBASE_FACTORY_001"
    http_status = 500
    default_message = "Component assembly failed"


class RuntimeExecutionError(AgentbaseError):
    """Raised when invoke/stream/resume fails."""

    code = "AGENTBASE_RT_999"
    http_status = 500
    default_message = "Runtime execution failed"


class AuthError(AgentbaseError):
    """Raised when authentication or authorization fails."""

    code = "AGENTBASE_AUTH_001"
    http_status = 401
    default_message = "Authentication failed"


class RateLimitError(AgentbaseError):
    """Raised when rate limit is exceeded."""

    code = "AGENTBASE_RATE_001"
    http_status = 429
    default_message = "Rate limit exceeded"


class QueueError(AgentbaseError):
    """Raised when queue operations fail."""

    code = "AGENTBASE_QUEUE_001"
    http_status = 400
    default_message = "Queue operation failed"


class KnowledgeBaseError(AgentbaseError):
    """Raised when knowledge base operations fail."""

    code = "AGENTBASE_KB_001"
    http_status = 500
    default_message = "Knowledge base operation failed"


class UploadError(AgentbaseError):
    """Raised when file upload operations fail."""

    code = "AGENTBASE_UPLOAD_001"
    http_status = 400
    default_message = "File upload failed"


class NotFoundError(AgentbaseError):
    """Raised when a requested resource is not found."""

    code = "AGENTBASE_RT_002"
    http_status = 404
    default_message = "Resource not found"


class ValidationError(AgentbaseError):
    """Raised when input validation fails."""

    code = "AGENTBASE_CONFIG_002"
    http_status = 422
    default_message = "Validation error"


# --------------------------------------------------------------------------- #
# Error code constants — single source of truth for programmatic handling.    #
# --------------------------------------------------------------------------- #

class ErrorCode:
    """Centralised error code constants.

    Using these constants instead of raw strings ensures consistency
    across the codebase and makes refactoring safe.
    """

    # Config errors (001–020)
    CONFIG_NOT_FOUND = "AGENTBASE_CONFIG_001"
    CONFIG_INVALID = "AGENTBASE_CONFIG_002"
    CONFIG_ENV_MISSING = "AGENTBASE_CONFIG_003"

    # Registry errors (001–020)
    REG_NOT_FOUND = "AGENTBASE_REG_001"
    REG_DUPLICATE = "AGENTBASE_REG_002"
    REG_EMPTY_NAME = "AGENTBASE_REG_003"

    # Factory errors (001–020)
    FACTORY_ASSEMBLY = "AGENTBASE_FACTORY_001"
    FACTORY_DEPENDENCY_MISSING = "AGENTBASE_FACTORY_002"
    FACTORY_AGENT_NOT_FOUND = "AGENTBASE_FACTORY_003"
    FACTORY_MODEL_INIT = "AGENTBASE_FACTORY_004"

    # Runtime errors (001–020)
    RT_TIMEOUT = "AGENTBASE_RT_001"
    RT_NOT_FOUND = "AGENTBASE_RT_002"
    RT_INVOKE_FAILED = "AGENTBASE_RT_003"
    RT_STREAM_FAILED = "AGENTBASE_RT_004"
    RT_RESUME_FAILED = "AGENTBASE_RT_005"
    RT_RECURSION_LIMIT = "AGENTBASE_RT_006"
    RT_UNKNOWN = "AGENTBASE_RT_999"

    # Auth errors (001–020)
    AUTH_MISSING_KEY = "AGENTBASE_AUTH_001"
    AUTH_INVALID_TOKEN = "AGENTBASE_AUTH_002"
    AUTH_EXPIRED_TOKEN = "AGENTBASE_AUTH_003"
    AUTH_FORBIDDEN = "AGENTBASE_AUTH_004"

    # Rate limit errors (001–020)
    RATE_EXCEEDED = "AGENTBASE_RATE_001"

    # Queue errors (001–020)
    QUEUE_NOT_INITIALIZED = "AGENTBASE_QUEUE_001"
    QUEUE_TASK_NOT_FOUND = "AGENTBASE_QUEUE_002"
    QUEUE_CANCEL_FAILED = "AGENTBASE_QUEUE_003"

    # Knowledge base errors (001–020)
    KB_PARSE_FAILED = "AGENTBASE_KB_001"
    KB_SEARCH_FAILED = "AGENTBASE_KB_002"
    KB_DOC_NOT_FOUND = "AGENTBASE_KB_003"

    # Upload errors (001–020)
    UPLOAD_TOO_LARGE = "AGENTBASE_UPLOAD_001"
    UPLOAD_UNSUPPORTED_TYPE = "AGENTBASE_UPLOAD_002"
    UPLOAD_FAILED = "AGENTBASE_UPLOAD_003"

    # WebSocket errors (001–020)
    WS_AGENT_NOT_FOUND = "AGENTBASE_WS_001"
    WS_EMPTY_MESSAGE = "AGENTBASE_WS_002"

    # Migration errors (001–020)
    MIGRATION_FAILED = "AGENTBASE_MIGRATION_001"
    MIGRATION_SCRIPTS_MISSING = "AGENTBASE_MIGRATION_002"

    # Usage tracking errors (001–020)
    USAGE_RECORD_FAILED = "AGENTBASE_USAGE_001"
    USAGE_QUERY_FAILED = "AGENTBASE_USAGE_002"
    USAGE_NOT_INITIALIZED = "AGENTBASE_USAGE_003"

    # Webhook errors (001–020)
    WEBHOOK_DELIVERY_FAILED = "AGENTBASE_WEBHOOK_001"
    WEBHOOK_ENDPOINT_NOT_FOUND = "AGENTBASE_WEBHOOK_002"
    WEBHOOK_NOT_INITIALIZED = "AGENTBASE_WEBHOOK_003"

    # Feedback errors (001–020)
    FEEDBACK_RECORD_FAILED = "AGENTBASE_FEEDBACK_001"
    FEEDBACK_NOT_FOUND = "AGENTBASE_FEEDBACK_002"
    FEEDBACK_NOT_INITIALIZED = "AGENTBASE_FEEDBACK_003"

    # Notification errors (001–020)
    NOTIFICATION_CREATE_FAILED = "AGENTBASE_NOTIFICATION_001"
    NOTIFICATION_NOT_FOUND = "AGENTBASE_NOTIFICATION_002"
    NOTIFICATION_NOT_INITIALIZED = "AGENTBASE_NOTIFICATION_003"

    # Conversation errors (001–020)
    CONVERSATION_RECORD_FAILED = "AGENTBASE_CONVERSATION_001"
    CONVERSATION_NOT_FOUND = "AGENTBASE_CONVERSATION_002"
    CONVERSATION_NOT_INITIALIZED = "AGENTBASE_CONVERSATION_003"

    # Scheduled task errors (001–020)
    SCHEDULE_TASK_FAILED = "AGENTBASE_SCHEDULE_001"
    SCHEDULE_TASK_NOT_FOUND = "AGENTBASE_SCHEDULE_002"
    SCHEDULE_NOT_INITIALIZED = "AGENTBASE_SCHEDULE_003"
    SCHEDULE_INVALID_SPEC = "AGENTBASE_SCHEDULE_004"

    # Calendar errors (001–020)
    CALENDAR_EVENT_FAILED = "AGENTBASE_CALENDAR_001"
    CALENDAR_EVENT_NOT_FOUND = "AGENTBASE_CALENDAR_002"
    CALENDAR_NOT_INITIALIZED = "AGENTBASE_CALENDAR_003"
    CALENDAR_INVALID_SPEC = "AGENTBASE_CALENDAR_004"

    # System config errors (001–020)
    SYSCONFIG_SET_FAILED = "AGENTBASE_SYSCONFIG_001"
    SYSCONFIG_NOT_FOUND = "AGENTBASE_SYSCONFIG_002"
    SYSCONFIG_NOT_INITIALIZED = "AGENTBASE_SYSCONFIG_003"
    SYSCONFIG_INVALID_SPEC = "AGENTBASE_SYSCONFIG_004"

    # RBAC errors (001–020)
    RBAC_ROLE_FAILED = "AGENTBASE_RBAC_001"
    RBAC_ROLE_NOT_FOUND = "AGENTBASE_RBAC_002"
    RBAC_NOT_INITIALIZED = "AGENTBASE_RBAC_003"
    RBAC_INVALID_SPEC = "AGENTBASE_RBAC_004"

    # Alert errors (001–020)
    ALERT_RULE_FAILED = "AGENTBASE_ALERT_001"
    ALERT_RULE_NOT_FOUND = "AGENTBASE_ALERT_002"
    ALERT_NOT_INITIALIZED = "AGENTBASE_ALERT_003"
    ALERT_INVALID_SPEC = "AGENTBASE_ALERT_004"


# HTTP status code mapping for known error codes
_CODE_TO_HTTP: dict[str, int] = {
    ErrorCode.CONFIG_NOT_FOUND: 500,
    ErrorCode.CONFIG_INVALID: 500,
    ErrorCode.CONFIG_ENV_MISSING: 500,
    ErrorCode.REG_NOT_FOUND: 500,
    ErrorCode.REG_DUPLICATE: 500,
    ErrorCode.REG_EMPTY_NAME: 500,
    ErrorCode.FACTORY_ASSEMBLY: 500,
    ErrorCode.FACTORY_DEPENDENCY_MISSING: 500,
    ErrorCode.FACTORY_AGENT_NOT_FOUND: 404,
    ErrorCode.FACTORY_MODEL_INIT: 503,
    ErrorCode.RT_TIMEOUT: 504,
    ErrorCode.RT_NOT_FOUND: 404,
    ErrorCode.RT_INVOKE_FAILED: 500,
    ErrorCode.RT_STREAM_FAILED: 500,
    ErrorCode.RT_RESUME_FAILED: 500,
    ErrorCode.RT_RECURSION_LIMIT: 500,
    ErrorCode.RT_UNKNOWN: 500,
    ErrorCode.AUTH_MISSING_KEY: 401,
    ErrorCode.AUTH_INVALID_TOKEN: 401,
    ErrorCode.AUTH_EXPIRED_TOKEN: 401,
    ErrorCode.AUTH_FORBIDDEN: 403,
    ErrorCode.RATE_EXCEEDED: 429,
    ErrorCode.QUEUE_NOT_INITIALIZED: 503,
    ErrorCode.QUEUE_TASK_NOT_FOUND: 404,
    ErrorCode.QUEUE_CANCEL_FAILED: 400,
    ErrorCode.KB_PARSE_FAILED: 422,
    ErrorCode.KB_SEARCH_FAILED: 500,
    ErrorCode.KB_DOC_NOT_FOUND: 404,
    ErrorCode.UPLOAD_TOO_LARGE: 413,
    ErrorCode.UPLOAD_UNSUPPORTED_TYPE: 415,
    ErrorCode.UPLOAD_FAILED: 500,
    ErrorCode.WS_AGENT_NOT_FOUND: 404,
    ErrorCode.WS_EMPTY_MESSAGE: 400,
    ErrorCode.MIGRATION_FAILED: 500,
    ErrorCode.MIGRATION_SCRIPTS_MISSING: 500,
    ErrorCode.USAGE_RECORD_FAILED: 500,
    ErrorCode.USAGE_QUERY_FAILED: 500,
    ErrorCode.USAGE_NOT_INITIALIZED: 503,
    ErrorCode.WEBHOOK_DELIVERY_FAILED: 502,
    ErrorCode.WEBHOOK_ENDPOINT_NOT_FOUND: 404,
    ErrorCode.WEBHOOK_NOT_INITIALIZED: 503,
    ErrorCode.FEEDBACK_RECORD_FAILED: 500,
    ErrorCode.FEEDBACK_NOT_FOUND: 404,
    ErrorCode.FEEDBACK_NOT_INITIALIZED: 503,
    ErrorCode.NOTIFICATION_CREATE_FAILED: 500,
    ErrorCode.NOTIFICATION_NOT_FOUND: 404,
    ErrorCode.NOTIFICATION_NOT_INITIALIZED: 503,
    ErrorCode.CONVERSATION_RECORD_FAILED: 500,
    ErrorCode.CONVERSATION_NOT_FOUND: 404,
    ErrorCode.CONVERSATION_NOT_INITIALIZED: 503,
    ErrorCode.SCHEDULE_TASK_FAILED: 500,
    ErrorCode.SCHEDULE_TASK_NOT_FOUND: 404,
    ErrorCode.SCHEDULE_NOT_INITIALIZED: 503,
    ErrorCode.SCHEDULE_INVALID_SPEC: 400,
    ErrorCode.CALENDAR_EVENT_FAILED: 500,
    ErrorCode.CALENDAR_EVENT_NOT_FOUND: 404,
    ErrorCode.CALENDAR_NOT_INITIALIZED: 503,
    ErrorCode.CALENDAR_INVALID_SPEC: 400,
    ErrorCode.SYSCONFIG_SET_FAILED: 500,
    ErrorCode.SYSCONFIG_NOT_FOUND: 404,
    ErrorCode.SYSCONFIG_NOT_INITIALIZED: 503,
    ErrorCode.SYSCONFIG_INVALID_SPEC: 400,
    ErrorCode.RBAC_ROLE_FAILED: 500,
    ErrorCode.RBAC_ROLE_NOT_FOUND: 404,
    ErrorCode.RBAC_NOT_INITIALIZED: 503,
    ErrorCode.RBAC_INVALID_SPEC: 400,
    ErrorCode.ALERT_RULE_FAILED: 500,
    ErrorCode.ALERT_RULE_NOT_FOUND: 404,
    ErrorCode.ALERT_NOT_INITIALIZED: 503,
    ErrorCode.ALERT_INVALID_SPEC: 400,
}


def http_status_for_code(code: str) -> int:
    """Return the recommended HTTP status code for an error code."""
    return _CODE_TO_HTTP.get(code, 500)


def _classify_error(exc: BaseException) -> str:
    """Map an exception to a stable error code ``AGENTBASE_<domain>_<nnn>``."""
    if isinstance(exc, AgentbaseError):
        return exc.code
    if isinstance(exc, TimeoutError):
        return ErrorCode.RT_TIMEOUT
    if isinstance(exc, (KeyError,)):
        return ErrorCode.RT_NOT_FOUND
    if isinstance(exc, (ConnectionError, ConnectionRefusedError, ConnectionResetError, BrokenPipeError)):
        return ErrorCode.RT_INVOKE_FAILED
    if isinstance(exc, RecursionError):
        return ErrorCode.RT_RECURSION_LIMIT
    if isinstance(exc, PermissionError):
        return ErrorCode.AUTH_FORBIDDEN
    if isinstance(exc, FileNotFoundError):
        return ErrorCode.RT_NOT_FOUND
    return ErrorCode.RT_UNKNOWN


# Retriable error codes — these can be safely retried by the caller
_RETRIABLE_CODES: frozenset[str] = frozenset({
    ErrorCode.RT_TIMEOUT,
    ErrorCode.RT_INVOKE_FAILED,
    ErrorCode.RT_STREAM_FAILED,
    ErrorCode.RT_RESUME_FAILED,
    ErrorCode.FACTORY_MODEL_INIT,
    ErrorCode.QUEUE_NOT_INITIALIZED,
    ErrorCode.RATE_EXCEEDED,
})


def is_retriable(exc: BaseException) -> bool:
    """Check if an error is safe to retry.

    Returns ``True`` for transient errors like timeouts, connection
    failures, and rate limit exceeded. Returns ``False`` for permanent
    errors like not found, validation, or auth failures.
    """
    if isinstance(exc, (TimeoutError, ConnectionError, ConnectionRefusedError)):
        return True
    if isinstance(exc, AgentbaseError):
        return exc.code in _RETRIABLE_CODES
    return False
