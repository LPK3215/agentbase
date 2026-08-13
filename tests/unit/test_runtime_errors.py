"""Tests for the structured error system — covers all error classes, ErrorCode constants, classification, and retry logic.

Tests verify:
1. AgentbaseError — base class, to_dict, with_request_id, custom code/http_status/detail
2. All error subclasses — default code, http_status, message
3. ErrorCode constants — all domain constants
4. http_status_for_code — mapping lookup, fallback
5. _classify_error — exception to error code mapping
6. is_retriable — retriable vs permanent errors
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# AgentbaseError
# ---------------------------------------------------------------------------


class TestAgentbaseError:
    def test_default_values(self):
        from agentbase.runtime.errors import AgentbaseError

        err = AgentbaseError()
        assert err.code == "AGENTBASE_RT_999"
        assert err.http_status == 500
        assert str(err) == "An unexpected error occurred"
        assert err.detail is None

    def test_custom_message(self):
        from agentbase.runtime.errors import AgentbaseError

        err = AgentbaseError("something went wrong")
        assert str(err) == "something went wrong"

    def test_custom_code(self):
        from agentbase.runtime.errors import AgentbaseError

        err = AgentbaseError("msg", code="CUSTOM_CODE")
        assert err.code == "CUSTOM_CODE"

    def test_custom_http_status(self):
        from agentbase.runtime.errors import AgentbaseError

        err = AgentbaseError("msg", http_status=418)
        assert err.http_status == 418

    def test_custom_detail(self):
        from agentbase.runtime.errors import AgentbaseError

        err = AgentbaseError("msg", detail={"key": "value"})
        assert err.detail == {"key": "value"}

    def test_to_dict_basic(self):
        from agentbase.runtime.errors import AgentbaseError

        err = AgentbaseError("test error", code="TEST_001", http_status=400, detail={"info": "debug"})
        d = err.to_dict()
        assert d["error"] == "test error"
        assert d["code"] == "TEST_001"
        assert d["http_status"] == 400
        assert d["detail"] == {"info": "debug"}

    def test_to_dict_no_request_id(self):
        from agentbase.runtime.errors import AgentbaseError

        err = AgentbaseError("msg")
        d = err.to_dict()
        assert "request_id" not in d

    def test_with_request_id(self):
        from agentbase.runtime.errors import AgentbaseError

        err = AgentbaseError("msg").with_request_id("req-123")
        assert err.request_id == "req-123"
        d = err.to_dict()
        assert d["request_id"] == "req-123"

    def test_with_request_id_chaining(self):
        from agentbase.runtime.errors import AgentbaseError

        err = AgentbaseError("msg")
        result = err.with_request_id("req-456")
        assert result is err

    def test_is_exception(self):
        from agentbase.runtime.errors import AgentbaseError

        assert issubclass(AgentbaseError, Exception)

    def test_empty_message_uses_default(self):
        from agentbase.runtime.errors import AgentbaseError

        err = AgentbaseError("")
        assert str(err) == "An unexpected error occurred"


# ---------------------------------------------------------------------------
# Error subclasses
# ---------------------------------------------------------------------------


class TestErrorSubclasses:
    def test_config_error(self):
        from agentbase.runtime.errors import ConfigError

        err = ConfigError()
        assert err.code == "AGENTBASE_CONFIG_001"
        assert err.http_status == 500
        assert "Configuration error" in str(err)

    def test_registry_error(self):
        from agentbase.runtime.errors import RegistryError

        err = RegistryError()
        assert err.code == "AGENTBASE_REG_001"
        assert err.http_status == 500

    def test_factory_error(self):
        from agentbase.runtime.errors import FactoryError

        err = FactoryError()
        assert err.code == "AGENTBASE_FACTORY_001"
        assert err.http_status == 500

    def test_runtime_execution_error(self):
        from agentbase.runtime.errors import RuntimeExecutionError

        err = RuntimeExecutionError()
        assert err.code == "AGENTBASE_RT_999"
        assert err.http_status == 500

    def test_auth_error(self):
        from agentbase.runtime.errors import AuthError

        err = AuthError()
        assert err.code == "AGENTBASE_AUTH_001"
        assert err.http_status == 401

    def test_rate_limit_error(self):
        from agentbase.runtime.errors import RateLimitError

        err = RateLimitError()
        assert err.code == "AGENTBASE_RATE_001"
        assert err.http_status == 429

    def test_queue_error(self):
        from agentbase.runtime.errors import QueueError

        err = QueueError()
        assert err.code == "AGENTBASE_QUEUE_001"
        assert err.http_status == 400

    def test_knowledge_base_error(self):
        from agentbase.runtime.errors import KnowledgeBaseError

        err = KnowledgeBaseError()
        assert err.code == "AGENTBASE_KB_001"
        assert err.http_status == 500

    def test_upload_error(self):
        from agentbase.runtime.errors import UploadError

        err = UploadError()
        assert err.code == "AGENTBASE_UPLOAD_001"
        assert err.http_status == 400

    def test_not_found_error(self):
        from agentbase.runtime.errors import NotFoundError

        err = NotFoundError()
        assert err.code == "AGENTBASE_RT_002"
        assert err.http_status == 404

    def test_validation_error(self):
        from agentbase.runtime.errors import ValidationError

        err = ValidationError()
        assert err.code == "AGENTBASE_CONFIG_002"
        assert err.http_status == 422

    def test_all_subclass_agentbase_error(self):
        from agentbase.runtime.errors import (
            AgentbaseError,
            AuthError,
            ConfigError,
            FactoryError,
            KnowledgeBaseError,
            NotFoundError,
            QueueError,
            RateLimitError,
            RegistryError,
            RuntimeExecutionError,
            UploadError,
            ValidationError,
        )

        for cls in [
            ConfigError, RegistryError, FactoryError, RuntimeExecutionError,
            AuthError, RateLimitError, QueueError, KnowledgeBaseError,
            UploadError, NotFoundError, ValidationError,
        ]:
            assert issubclass(cls, AgentbaseError)


# ---------------------------------------------------------------------------
# ErrorCode constants
# ---------------------------------------------------------------------------


class TestErrorCode:
    def test_config_codes(self):
        from agentbase.runtime.errors import ErrorCode

        assert ErrorCode.CONFIG_NOT_FOUND == "AGENTBASE_CONFIG_001"
        assert ErrorCode.CONFIG_INVALID == "AGENTBASE_CONFIG_002"
        assert ErrorCode.CONFIG_ENV_MISSING == "AGENTBASE_CONFIG_003"

    def test_registry_codes(self):
        from agentbase.runtime.errors import ErrorCode

        assert ErrorCode.REG_NOT_FOUND == "AGENTBASE_REG_001"
        assert ErrorCode.REG_DUPLICATE == "AGENTBASE_REG_002"
        assert ErrorCode.REG_EMPTY_NAME == "AGENTBASE_REG_003"

    def test_factory_codes(self):
        from agentbase.runtime.errors import ErrorCode

        assert ErrorCode.FACTORY_ASSEMBLY == "AGENTBASE_FACTORY_001"
        assert ErrorCode.FACTORY_DEPENDENCY_MISSING == "AGENTBASE_FACTORY_002"
        assert ErrorCode.FACTORY_AGENT_NOT_FOUND == "AGENTBASE_FACTORY_003"
        assert ErrorCode.FACTORY_MODEL_INIT == "AGENTBASE_FACTORY_004"

    def test_runtime_codes(self):
        from agentbase.runtime.errors import ErrorCode

        assert ErrorCode.RT_TIMEOUT == "AGENTBASE_RT_001"
        assert ErrorCode.RT_NOT_FOUND == "AGENTBASE_RT_002"
        assert ErrorCode.RT_INVOKE_FAILED == "AGENTBASE_RT_003"
        assert ErrorCode.RT_STREAM_FAILED == "AGENTBASE_RT_004"
        assert ErrorCode.RT_RESUME_FAILED == "AGENTBASE_RT_005"
        assert ErrorCode.RT_RECURSION_LIMIT == "AGENTBASE_RT_006"
        assert ErrorCode.RT_UNKNOWN == "AGENTBASE_RT_999"

    def test_auth_codes(self):
        from agentbase.runtime.errors import ErrorCode

        assert ErrorCode.AUTH_MISSING_KEY == "AGENTBASE_AUTH_001"
        assert ErrorCode.AUTH_INVALID_TOKEN == "AGENTBASE_AUTH_002"
        assert ErrorCode.AUTH_EXPIRED_TOKEN == "AGENTBASE_AUTH_003"
        assert ErrorCode.AUTH_FORBIDDEN == "AGENTBASE_AUTH_004"

    def test_rate_codes(self):
        from agentbase.runtime.errors import ErrorCode

        assert ErrorCode.RATE_EXCEEDED == "AGENTBASE_RATE_001"

    def test_queue_codes(self):
        from agentbase.runtime.errors import ErrorCode

        assert ErrorCode.QUEUE_NOT_INITIALIZED == "AGENTBASE_QUEUE_001"
        assert ErrorCode.QUEUE_TASK_NOT_FOUND == "AGENTBASE_QUEUE_002"
        assert ErrorCode.QUEUE_CANCEL_FAILED == "AGENTBASE_QUEUE_003"

    def test_kb_codes(self):
        from agentbase.runtime.errors import ErrorCode

        assert ErrorCode.KB_PARSE_FAILED == "AGENTBASE_KB_001"
        assert ErrorCode.KB_SEARCH_FAILED == "AGENTBASE_KB_002"
        assert ErrorCode.KB_DOC_NOT_FOUND == "AGENTBASE_KB_003"

    def test_upload_codes(self):
        from agentbase.runtime.errors import ErrorCode

        assert ErrorCode.UPLOAD_TOO_LARGE == "AGENTBASE_UPLOAD_001"
        assert ErrorCode.UPLOAD_UNSUPPORTED_TYPE == "AGENTBASE_UPLOAD_002"
        assert ErrorCode.UPLOAD_FAILED == "AGENTBASE_UPLOAD_003"

    def test_ws_codes(self):
        from agentbase.runtime.errors import ErrorCode

        assert ErrorCode.WS_AGENT_NOT_FOUND == "AGENTBASE_WS_001"
        assert ErrorCode.WS_EMPTY_MESSAGE == "AGENTBASE_WS_002"


# ---------------------------------------------------------------------------
# http_status_for_code
# ---------------------------------------------------------------------------


class TestHttpStatusForCode:
    def test_known_code(self):
        from agentbase.runtime.errors import ErrorCode, http_status_for_code

        assert http_status_for_code(ErrorCode.AUTH_MISSING_KEY) == 401
        assert http_status_for_code(ErrorCode.RATE_EXCEEDED) == 429
        assert http_status_for_code(ErrorCode.RT_NOT_FOUND) == 404
        assert http_status_for_code(ErrorCode.RT_TIMEOUT) == 504

    def test_unknown_code_defaults_500(self):
        from agentbase.runtime.errors import http_status_for_code

        assert http_status_for_code("UNKNOWN_CODE") == 500

    def test_all_codes_have_mapping(self):
        from agentbase.runtime.errors import ErrorCode, http_status_for_code

        code_attrs = [
            attr for attr in dir(ErrorCode)
            if not attr.startswith("_") and isinstance(getattr(ErrorCode, attr), str)
        ]
        for attr in code_attrs:
            code = getattr(ErrorCode, attr)
            status = http_status_for_code(code)
            assert isinstance(status, int)
            assert 100 <= status <= 599


# ---------------------------------------------------------------------------
# _classify_error
# ---------------------------------------------------------------------------


class TestClassifyError:
    def test_agentbase_error_preserves_code(self):
        from agentbase.runtime.errors import AuthError, _classify_error

        err = AuthError("custom")
        assert _classify_error(err) == "AGENTBASE_AUTH_001"

    def test_timeout_error(self):
        from agentbase.runtime.errors import ErrorCode, _classify_error

        assert _classify_error(TimeoutError()) == ErrorCode.RT_TIMEOUT

    def test_key_error(self):
        from agentbase.runtime.errors import ErrorCode, _classify_error

        assert _classify_error(KeyError("key")) == ErrorCode.RT_NOT_FOUND

    def test_connection_error(self):
        from agentbase.runtime.errors import ErrorCode, _classify_error

        assert _classify_error(ConnectionError()) == ErrorCode.RT_INVOKE_FAILED

    def test_connection_refused(self):
        from agentbase.runtime.errors import ErrorCode, _classify_error

        assert _classify_error(ConnectionRefusedError()) == ErrorCode.RT_INVOKE_FAILED

    def test_recursion_error(self):
        from agentbase.runtime.errors import ErrorCode, _classify_error

        assert _classify_error(RecursionError()) == ErrorCode.RT_RECURSION_LIMIT

    def test_permission_error(self):
        from agentbase.runtime.errors import ErrorCode, _classify_error

        assert _classify_error(PermissionError()) == ErrorCode.AUTH_FORBIDDEN

    def test_file_not_found_error(self):
        from agentbase.runtime.errors import ErrorCode, _classify_error

        assert _classify_error(FileNotFoundError("file.txt")) == ErrorCode.RT_NOT_FOUND

    def test_generic_exception(self):
        from agentbase.runtime.errors import ErrorCode, _classify_error

        assert _classify_error(Exception("unknown")) == ErrorCode.RT_UNKNOWN

    def test_value_error(self):
        from agentbase.runtime.errors import ErrorCode, _classify_error

        assert _classify_error(ValueError("bad value")) == ErrorCode.RT_UNKNOWN


# ---------------------------------------------------------------------------
# is_retriable
# ---------------------------------------------------------------------------


class TestIsRetriable:
    def test_timeout_error_retriable(self):
        from agentbase.runtime.errors import is_retriable

        assert is_retriable(TimeoutError()) is True

    def test_connection_error_retriable(self):
        from agentbase.runtime.errors import is_retriable

        assert is_retriable(ConnectionError()) is True

    def test_connection_refused_retriable(self):
        from agentbase.runtime.errors import is_retriable

        assert is_retriable(ConnectionRefusedError()) is True

    def test_rate_limit_error_retriable(self):
        from agentbase.runtime.errors import RateLimitError, is_retriable

        assert is_retriable(RateLimitError()) is True

    def test_runtime_execution_error_retriable(self):
        from agentbase.runtime.errors import RuntimeExecutionError, is_retriable

        # RT_999 is not in retriable set
        assert is_retriable(RuntimeExecutionError()) is False

    def test_auth_error_not_retriable(self):
        from agentbase.runtime.errors import AuthError, is_retriable

        assert is_retriable(AuthError()) is False

    def test_not_found_error_not_retriable(self):
        from agentbase.runtime.errors import NotFoundError, is_retriable

        assert is_retriable(NotFoundError()) is False

    def test_validation_error_not_retriable(self):
        from agentbase.runtime.errors import ValidationError, is_retriable

        assert is_retriable(ValidationError()) is False

    def test_generic_exception_not_retriable(self):
        from agentbase.runtime.errors import is_retriable

        assert is_retriable(Exception("unknown")) is False

    def test_custom_retriable_code(self):
        from agentbase.runtime.errors import AgentbaseError, ErrorCode, is_retriable

        err = AgentbaseError("msg", code=ErrorCode.RT_TIMEOUT)
        assert is_retriable(err) is True

    def test_custom_non_retriable_code(self):
        from agentbase.runtime.errors import AgentbaseError, ErrorCode, is_retriable

        err = AgentbaseError("msg", code=ErrorCode.AUTH_FORBIDDEN)
        assert is_retriable(err) is False
