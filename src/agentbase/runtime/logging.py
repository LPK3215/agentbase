from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line with stable required fields.

    Required fields:
    - ``timestamp``: ISO 8601 format
    - ``level``: log level name
    - ``event``: event type or message
    - ``thread_id``: agent thread identifier (optional)
    - ``agent``: agent name (optional)
    - ``duration_ms``: execution duration (optional)
    - ``request_id``: request correlation ID (optional)
    - ``logger``: logger name (always included)
    """

    REQUIRED_FIELDS = ("timestamp", "level", "event", "thread_id", "agent", "duration_ms", "request_id")

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "event": getattr(record, "event", None) or record.getMessage(),
            "thread_id": getattr(record, "thread_id", None),
            "agent": getattr(record, "agent", None),
            "duration_ms": getattr(record, "duration_ms", None),
            "request_id": getattr(record, "request_id", None),
        }
        name = getattr(record, "name", None)
        if name:
            payload["logger"] = name
        # Include any extra fields from the record
        for key in dir(record):
            if key.startswith("_") or key in {
                "args", "asctime", "created", "exc_info", "exc_text",
                "filename", "funcName", "levelname", "levelno", "lineno",
                "message", "module", "msecs", "msg", "name", "pathname",
                "process", "processName", "relativeCreated", "stack_info",
                "thread", "threadName", "taskName", "event", "thread_id",
                "agent", "duration_ms", "request_id", "timestamp", "level",
                "logger",
            }:
                continue
            value = getattr(record, key, None)
            if value is not None and not callable(value):
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


class SecretRedactionFilter(logging.Filter):
    """Redact API keys and DSN password segments from log messages."""

    _KEY_PATTERN = re.compile(
        r"(?i)((?:[A-Za-z0-9_]*API_KEY|SECRET|TOKEN|PASSWORD|DSN)['\"]?\s*[:=]\s*['\"]?)([^'\"\s,]+)"
    )
    _DSN_PATTERN = re.compile(r"(://[^:/@\s]+:)([^@/\s]+)(@)")

    def filter(self, record: logging.LogRecord) -> bool:
        # Skip non-string messages (e.g. exception objects passed as msg).
        if not isinstance(record.msg, str):
            return True
        msg = record.getMessage()
        redacted = self._KEY_PATTERN.sub(r"\1***", msg)
        redacted = self._DSN_PATTERN.sub(r"\1***\3", redacted)
        record.msg = redacted
        record.args = None
        return True


def configure_logging(level: str = "INFO") -> None:
    """Configure root logger with JSON formatter and secret redaction.

    Idempotent — calling multiple times updates the level without
    adding duplicate handlers.
    """
    root = logging.getLogger()
    redaction = SecretRedactionFilter()
    for handler in root.handlers:
        handler.addFilter(redaction)
    if root.handlers:
        root.setLevel(level.upper())
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(redaction)
    root.addHandler(handler)
    root.setLevel(level.upper())


def set_log_level(level: str) -> None:
    """Dynamically change the log level at runtime.

    Args:
        level: Log level name ("DEBUG", "INFO", "WARNING", "ERROR").
    """
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in root.handlers:
        handler.setLevel(level.upper())


def get_log_level() -> str:
    """Return the current log level name."""
    root = logging.getLogger()
    return logging.getLevelName(root.level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
