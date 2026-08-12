from __future__ import annotations

import pytest

from agentbase.runtime.errors import (
    AgentbaseError,
    ConfigError,
    FactoryError,
    RegistryError,
    RuntimeExecutionError,
    _classify_error,
)


def test_agentbase_error_default_code():
    err = AgentbaseError("something")
    assert err.code == "AGENTBASE_RT_999"
    assert str(err) == "something"


def test_agentbase_error_custom_code():
    err = AgentbaseError("bad", code="AGENTBASE_CUSTOM_001")
    assert err.code == "AGENTBASE_CUSTOM_001"


def test_config_error_code():
    err = ConfigError("missing")
    assert err.code == "AGENTBASE_CONFIG_001"


def test_registry_error_code():
    err = RegistryError("dup")
    assert err.code == "AGENTBASE_REG_001"


def test_factory_error_code():
    err = FactoryError("boom")
    assert err.code == "AGENTBASE_FACTORY_001"


def test_runtime_execution_error_code():
    err = RuntimeExecutionError("fail")
    assert err.code == "AGENTBASE_RT_999"


def test_runtime_execution_error_custom_code():
    err = RuntimeExecutionError("missing", code="AGENTBASE_RT_002")
    assert err.code == "AGENTBASE_RT_002"


def test_classify_agentbase_error():
    err = ConfigError("x")
    assert _classify_error(err) == "AGENTBASE_CONFIG_001"


def test_classify_timeout():
    assert _classify_error(TimeoutError("slow")) == "AGENTBASE_RT_001"


def test_classify_unknown():
    assert _classify_error(ValueError("x")) == "AGENTBASE_RT_999"


def test_backward_compat_raise_without_code():
    with pytest.raises(ConfigError) as exc_info:
        raise ConfigError("old style")
    assert exc_info.value.code == "AGENTBASE_CONFIG_001"