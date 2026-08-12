from __future__ import annotations

import pytest

from agentbase.config.loader import _deep_merge, _read_yaml
from agentbase.runtime.errors import ConfigError


def test_deep_merge_simple():
    base = {"a": 1, "b": 2}
    overlay = {"b": 3, "c": 4}
    result = _deep_merge(base, overlay)
    assert result == {"a": 1, "b": 3, "c": 4}


def test_deep_merge_nested():
    base = {"model": {"name": "gpt", "temp": 0}}
    overlay = {"model": {"temp": 1}}
    result = _deep_merge(base, overlay)
    assert result == {"model": {"name": "gpt", "temp": 1}}


def test_deep_merge_no_mutate():
    base = {"a": {"x": 1}}
    overlay = {"a": {"y": 2}}
    _deep_merge(base, overlay)
    assert base == {"a": {"x": 1}}


def test_read_yaml_not_found(tmp_path):
    with pytest.raises(ConfigError):
        _read_yaml(tmp_path / "nonexistent.yaml")


def test_read_yaml_valid(tmp_path):
    path = tmp_path / "test.yaml"
    path.write_text("key: value\n", encoding="utf-8")
    data = _read_yaml(path)
    assert data == {"key": "value"}


def test_read_yaml_empty(tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    data = _read_yaml(path)
    assert data == {}


def test_read_yaml_non_dict(tmp_path):
    path = tmp_path / "list.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        _read_yaml(path)