from __future__ import annotations

import json
import logging

from agentbase.runtime.logging import JsonFormatter, SecretRedactionFilter


def _make_record(msg: str, **extra):
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0, msg=msg, args=None, exc_info=None
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_required_fields():
    formatter = JsonFormatter()
    record = _make_record("hello")
    output = formatter.format(record)
    data = json.loads(output)
    for field in JsonFormatter.REQUIRED_FIELDS:
        assert field in data, f"Missing field: {field}"


def test_json_formatter_event_from_message():
    formatter = JsonFormatter()
    record = _make_record("test message")
    output = formatter.format(record)
    data = json.loads(output)
    assert data["event"] == "test message"


def test_json_formatter_event_attr():
    formatter = JsonFormatter()
    record = _make_record("msg", event="custom.event", thread_id="T1", agent="default", duration_ms=42)
    output = formatter.format(record)
    data = json.loads(output)
    assert data["event"] == "custom.event"
    assert data["thread_id"] == "T1"
    assert data["agent"] == "default"
    assert data["duration_ms"] == 42


def test_json_formatter_null_fields():
    formatter = JsonFormatter()
    record = _make_record("msg")
    output = formatter.format(record)
    data = json.loads(output)
    assert data["thread_id"] is None
    assert data["agent"] is None
    assert data["duration_ms"] is None


def test_secret_redaction_api_key():
    filt = SecretRedactionFilter()
    record = _make_record("OPENAI_API_KEY=sk-abc123secret")
    filt.filter(record)
    assert "sk-abc123secret" not in str(record.msg)
    assert "***" in str(record.msg)


def test_secret_redaction_dsn():
    filt = SecretRedactionFilter()
    record = _make_record("postgres://user:passw0rd@localhost:5432/db")
    filt.filter(record)
    assert "passw0rd" not in str(record.msg)
    assert "***" in str(record.msg)