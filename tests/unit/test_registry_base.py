from __future__ import annotations

import pytest

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.base import Registry
from agentbase.runtime.errors import RegistryError


def test_register_and_get():
    reg: Registry = Registry("tool")
    reg.register("echo", lambda: None)
    assert reg.has("echo")
    assert reg.get("echo") is not None


def test_duplicate_register_raises():
    reg: Registry = Registry("tool")
    reg.register("echo", lambda: None)
    with pytest.raises(RegistryError):
        reg.register("echo", lambda: None)


def test_register_with_meta():
    reg: Registry = Registry("tool")
    meta = ExtensionMeta(name="echo", kind="tool", description="Echo text")
    reg.register("echo", lambda: None, meta=meta)
    assert reg.get_meta("echo") is meta


def test_get_meta_none():
    reg: Registry = Registry("tool")
    reg.register("echo", lambda: None)
    assert reg.get_meta("echo") is None


def test_metas_returns_all():
    reg: Registry = Registry("tool")
    m1 = ExtensionMeta(name="a", kind="tool", description="A")
    m2 = ExtensionMeta(name="b", kind="tool", description="B")
    reg.register("a", lambda: None, meta=m1)
    reg.register("b", lambda: None, meta=m2)
    all_metas = reg.metas()
    assert len(all_metas) == 2


def test_register_empty_name_raises():
    reg: Registry = Registry("tool")
    with pytest.raises(RegistryError):
        reg.register("  ", lambda: None)


def test_unknown_get_raises():
    reg: Registry = Registry("tool")
    with pytest.raises(RegistryError):
        reg.get("nonexistent")


def test_fallback_meta_attribute():
    reg: Registry = Registry("tool")

    def builder():
        return None

    builder.__agentbase_meta__ = ExtensionMeta(name="fb", kind="tool", description="Fallback")
    reg.register("fb", builder)
    assert reg.get_meta("fb") is not None
    assert reg.get_meta("fb").name == "fb"