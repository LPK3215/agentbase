"""Tests for runtime logging — covers JsonFormatter, SecretRedactionFilter, configure_logging, set/get log level.

Tests verify:
1. JsonFormatter — required fields, optional fields, extra fields, JSON output
2. SecretRedactionFilter — API key, token, password, DSN redaction, non-string messages
3. configure_logging — idempotent, level setting, handler setup
4. set_log_level / get_log_level — dynamic level changes
5. get_logger — returns named logger
"""
from __future__ import annotations

import json
import logging

# ---------------------------------------------------------------------------
# JsonFormatter
# ---------------------------------------------------------------------------


class TestJsonFormatter:
    def test_required_fields_present(self):
        from agentbase.runtime.logging import JsonFormatter

        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="test message",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        payload = json.loads(output)
        assert "timestamp" in payload
        assert "level" in payload
        assert "event" in payload
        assert payload["level"] == "INFO"
        assert payload["event"] == "test message"
        assert payload["logger"] == "test.logger"

    def test_optional_fields_default_none(self):
        from agentbase.runtime.logging import JsonFormatter

        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="msg",
            args=None,
            exc_info=None,
        )
        payload = json.loads(formatter.format(record))
        assert payload["thread_id"] is None
        assert payload["agent"] is None
        assert payload["duration_ms"] is None
        assert payload["request_id"] is None

    def test_custom_fields_from_record(self):
        from agentbase.runtime.logging import JsonFormatter

        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="msg",
            args=None,
            exc_info=None,
        )
        record.thread_id = "t-123"
        record.agent = "my_agent"
        record.duration_ms = 42
        record.request_id = "req-456"
        payload = json.loads(formatter.format(record))
        assert payload["thread_id"] == "t-123"
        assert payload["agent"] == "my_agent"
        assert payload["duration_ms"] == 42
        assert payload["request_id"] == "req-456"

    def test_event_from_attr_overrides_message(self):
        from agentbase.runtime.logging import JsonFormatter

        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="raw message",
            args=None,
            exc_info=None,
        )
        record.event = "custom_event"
        payload = json.loads(formatter.format(record))
        assert payload["event"] == "custom_event"

    def test_extra_fields_included(self):
        from agentbase.runtime.logging import JsonFormatter

        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="msg",
            args=None,
            exc_info=None,
        )
        record.custom_field = "custom_value"
        payload = json.loads(formatter.format(record))
        assert payload["custom_field"] == "custom_value"

    def test_json_valid_output(self):
        from agentbase.runtime.logging import JsonFormatter

        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=42,
            msg="error with unicode: 你好",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        # Should be valid JSON
        payload = json.loads(output)
        assert "你好" in payload["event"]

    def test_no_logger_name(self):
        from agentbase.runtime.logging import JsonFormatter

        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="msg",
            args=None,
            exc_info=None,
        )
        payload = json.loads(formatter.format(record))
        assert "logger" not in payload


# ---------------------------------------------------------------------------
# SecretRedactionFilter
# ---------------------------------------------------------------------------


class TestSecretRedactionFilter:
    def _make_record(self, msg: str) -> logging.LogRecord:
        return logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg=msg,
            args=None,
            exc_info=None,
        )

    def test_redact_api_key(self):
        from agentbase.runtime.logging import SecretRedactionFilter

        f = SecretRedactionFilter()
        record = self._make_record("API_KEY=sk-1234567890abcdef")
        f.filter(record)
        assert "sk-1234567890abcdef" not in record.msg
        assert "***" in record.msg

    def test_redact_token(self):
        from agentbase.runtime.logging import SecretRedactionFilter

        f = SecretRedactionFilter()
        record = self._make_record('TOKEN="abc-123-xyz"')
        f.filter(record)
        assert "abc-123-xyz" not in record.msg
        assert "***" in record.msg

    def test_redact_password(self):
        from agentbase.runtime.logging import SecretRedactionFilter

        f = SecretRedactionFilter()
        record = self._make_record("PASSWORD=secret123")
        f.filter(record)
        assert "secret123" not in record.msg
        assert "***" in record.msg

    def test_redact_secret(self):
        from agentbase.runtime.logging import SecretRedactionFilter

        f = SecretRedactionFilter()
        record = self._make_record("SECRET=my_secret_value")
        f.filter(record)
        assert "my_secret_value" not in record.msg

    def test_redact_dsn_password(self):
        from agentbase.runtime.logging import SecretRedactionFilter

        f = SecretRedactionFilter()
        record = self._make_record("postgres://user:password123@host:5432/db")
        f.filter(record)
        assert "password123" not in record.msg
        assert "***" in record.msg

    def test_non_string_message_passes(self):
        from agentbase.runtime.logging import SecretRedactionFilter

        f = SecretRedactionFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg={"key": "value"},  # Non-string
            args=None,
            exc_info=None,
        )
        assert f.filter(record) is True

    def test_no_secrets_unchanged(self):
        from agentbase.runtime.logging import SecretRedactionFilter

        f = SecretRedactionFilter()
        record = self._make_record("This is a normal log message")
        f.filter(record)
        assert record.msg == "This is a normal log message"

    def test_case_insensitive_redaction(self):
        from agentbase.runtime.logging import SecretRedactionFilter

        f = SecretRedactionFilter()
        record = self._make_record("api_key=sk-123456")
        f.filter(record)
        assert "sk-123456" not in record.msg

    def test_filter_returns_true(self):
        from agentbase.runtime.logging import SecretRedactionFilter

        f = SecretRedactionFilter()
        record = self._make_record("normal message")
        assert f.filter(record) is True

    def test_args_cleared_after_redaction(self):
        from agentbase.runtime.logging import SecretRedactionFilter

        f = SecretRedactionFilter()
        record = self._make_record("API_KEY=secret")
        f.filter(record)
        assert record.args is None


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------


class TestConfigureLogging:
    def test_configure_adds_handler(self):
        from agentbase.runtime.logging import configure_logging

        # Save and restore root logger state
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        original_level = root.level
        try:
            root.handlers.clear()
            configure_logging("INFO")
            assert len(root.handlers) == 1
            assert root.level == logging.INFO
        finally:
            root.handlers.clear()
            root.handlers.extend(original_handlers)
            root.setLevel(original_level)

    def test_configure_is_idempotent(self):
        from agentbase.runtime.logging import configure_logging

        root = logging.getLogger()
        original_handlers = root.handlers[:]
        original_level = root.level
        try:
            root.handlers.clear()
            configure_logging("INFO")
            initial_count = len(root.handlers)
            configure_logging("DEBUG")
            assert len(root.handlers) == initial_count  # No duplicate handlers
            assert root.level == logging.DEBUG
        finally:
            root.handlers.clear()
            root.handlers.extend(original_handlers)
            root.setLevel(original_level)

    def test_configure_with_different_levels(self):
        from agentbase.runtime.logging import configure_logging

        root = logging.getLogger()
        original_handlers = root.handlers[:]
        original_level = root.level
        try:
            root.handlers.clear()
            for level in ["DEBUG", "INFO", "WARNING", "ERROR"]:
                configure_logging(level)
                assert root.level == getattr(logging, level)
        finally:
            root.handlers.clear()
            root.handlers.extend(original_handlers)
            root.setLevel(original_level)


# ---------------------------------------------------------------------------
# set_log_level / get_log_level
# ---------------------------------------------------------------------------


class TestSetGetLogLevel:
    def test_set_and_get_level(self):
        from agentbase.runtime.logging import get_log_level, set_log_level

        root = logging.getLogger()
        original_level = root.level
        try:
            set_log_level("DEBUG")
            assert get_log_level() == "DEBUG"
            set_log_level("WARNING")
            assert get_log_level() == "WARNING"
        finally:
            root.setLevel(original_level)

    def test_set_level_updates_handlers(self):
        from agentbase.runtime.logging import set_log_level

        root = logging.getLogger()
        original_level = root.level
        original_handler_levels = [h.level for h in root.handlers]
        try:
            set_log_level("ERROR")
            for handler in root.handlers:
                assert handler.level == logging.ERROR
        finally:
            root.setLevel(original_level)
            for h, lvl in zip(root.handlers, original_handler_levels):
                h.setLevel(lvl)


# ---------------------------------------------------------------------------
# get_logger
# ---------------------------------------------------------------------------


class TestGetLogger:
    def test_returns_named_logger(self):
        from agentbase.runtime.logging import get_logger

        logger = get_logger("test.module")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test.module"

    def test_same_name_returns_same_instance(self):
        from agentbase.runtime.logging import get_logger

        logger1 = get_logger("test.same")
        logger2 = get_logger("test.same")
        assert logger1 is logger2
