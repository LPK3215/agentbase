"""Unit tests for the API key management service (core.apikey_manager).

Covers:
- Key generation (generate_api_key)
- Key hashing (hash_api_key / verify_api_key_hash)
- ApiKeyEntry dataclass (to_dict / from_dict / is_expired)
- InMemoryApiKeyProvider CRUD (create / get / get_by_hash / list / update / delete)
- NullApiKeyProvider (no-op behaviour)
- ApiKeyManager (enabled / disabled / create / verify / revoke / CRUD)
- Registry (register_apikey_provider / get_apikey_provider / list_apikey_providers)
- Singleton (get_apikey_manager / set_apikey_manager)
- Concurrency (thread-safe operations)
- Protocol compliance
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from agentbase.core.apikey_manager import (
    ApiKeyEntry,
    ApiKeyManager,
    ApiKeyProvider,
    InMemoryApiKeyProvider,
    NullApiKeyProvider,
    generate_api_key,
    get_apikey_manager,
    get_apikey_provider,
    hash_api_key,
    list_apikey_providers,
    register_apikey_provider,
    set_apikey_manager,
    verify_api_key_hash,
)

# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------

class TestGenerateApiKey:
    """Test API key generation."""

    def test_generate_returns_string(self):
        key = generate_api_key()
        assert isinstance(key, str)
        assert len(key) > 0

    def test_generate_has_prefix(self):
        key = generate_api_key()
        assert key.startswith("agk_")

    def test_generate_random(self):
        key1 = generate_api_key()
        key2 = generate_api_key()
        assert key1 != key2  # extremely unlikely to collide

    def test_generate_sufficient_length(self):
        key = generate_api_key()
        # agk_ (4) + 64 hex chars = 68
        assert len(key) >= 60


# ---------------------------------------------------------------------------
# Key hashing
# ---------------------------------------------------------------------------

class TestHashApiKey:
    """Test API key hashing and verification."""

    def test_hash_returns_string(self):
        h = hash_api_key("agk_test123")
        assert isinstance(h, str)
        assert h.startswith("sha256$")

    def test_hash_does_not_contain_plaintext(self):
        raw = "agk_mysecretkey123"
        h = hash_api_key(raw)
        assert raw not in h

    def test_hash_deterministic(self):
        raw = "agk_samekey"
        h1 = hash_api_key(raw)
        h2 = hash_api_key(raw)
        assert h1 == h2  # same input → same hash (no salt)

    def test_verify_correct(self):
        raw = "agk_correct_key_123"
        h = hash_api_key(raw)
        assert verify_api_key_hash(raw, h) is True

    def test_verify_wrong(self):
        h = hash_api_key("agk_right_key_123")
        assert verify_api_key_hash("agk_wrong_key_456", h) is False

    def test_verify_empty_hash(self):
        assert verify_api_key_hash("agk_test", "") is False

    def test_verify_malformed_hash(self):
        assert verify_api_key_hash("agk_test", "not_a_hash") is False

    def test_verify_wrong_prefix(self):
        assert verify_api_key_hash("agk_test", "bcrypt$hashdata") is False

    def test_verify_missing_parts(self):
        assert verify_api_key_hash("agk_test", "sha256") is False


# ---------------------------------------------------------------------------
# ApiKeyEntry dataclass
# ---------------------------------------------------------------------------

class TestApiKeyEntry:
    """Test ApiKeyEntry dataclass."""

    def test_to_dict_excludes_hash_by_default(self):
        entry = ApiKeyEntry(
            key_id="test123",
            name="test-key",
            key_hash="sha256$abc",
            key_prefix="agk_a1b2c3d4",
        )
        d = entry.to_dict()
        assert "key_hash" not in d
        assert d["key_id"] == "test123"
        assert d["name"] == "test-key"
        assert d["key_prefix"] == "agk_a1b2c3d4"

    def test_to_dict_includes_hash_when_requested(self):
        entry = ApiKeyEntry(
            key_id="test123",
            key_hash="sha256$abc",
        )
        d = entry.to_dict(include_hash=True)
        assert d["key_hash"] == "sha256$abc"

    def test_from_dict_roundtrip(self):
        original = ApiKeyEntry(
            key_id="k1",
            name="my-key",
            key_hash="sha256$hash",
            key_prefix="agk_a1b2c3d4",
            roles=["admin"],
            user_id="alice",
            description="Test key",
            enabled=True,
            metadata={"env": "prod"},
        )
        d = original.to_dict(include_hash=True)
        restored = ApiKeyEntry.from_dict(d)
        assert restored.key_id == "k1"
        assert restored.name == "my-key"
        assert restored.key_hash == "sha256$hash"
        assert restored.roles == ["admin"]
        assert restored.user_id == "alice"
        assert restored.metadata == {"env": "prod"}

    def test_from_dict_ignores_unknown_keys(self):
        entry = ApiKeyEntry.from_dict({
            "key_id": "k1",
            "name": "test",
            "unknown_field": "ignored",
        })
        assert entry.key_id == "k1"
        assert entry.name == "test"

    def test_is_expired_false_when_no_expiry(self):
        entry = ApiKeyEntry(key_id="k1", expires_at="")
        assert entry.is_expired is False

    def test_is_expired_false_when_future(self):
        future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        entry = ApiKeyEntry(key_id="k1", expires_at=future)
        assert entry.is_expired is False

    def test_is_expired_true_when_past(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        entry = ApiKeyEntry(key_id="k1", expires_at=past)
        assert entry.is_expired is True

    def test_is_expired_handles_naive_datetime(self):
        past_naive = (datetime.utcnow() - timedelta(days=1)).isoformat()
        entry = ApiKeyEntry(key_id="k1", expires_at=past_naive)
        assert entry.is_expired is True

    def test_is_expired_handles_invalid_format(self):
        entry = ApiKeyEntry(key_id="k1", expires_at="not-a-date")
        assert entry.is_expired is False


# ---------------------------------------------------------------------------
# InMemoryApiKeyProvider
# ---------------------------------------------------------------------------

class TestInMemoryApiKeyProvider:
    """Test InMemoryApiKeyProvider CRUD operations."""

    def test_create_and_get(self):
        provider = InMemoryApiKeyProvider()
        entry = ApiKeyEntry(key_id="k1", name="key1", key_hash="sha256$h1")
        stored = provider.create(entry)
        assert stored.key_id == "k1"
        fetched = provider.get("k1")
        assert fetched is not None
        assert fetched.name == "key1"

    def test_get_nonexistent_returns_none(self):
        provider = InMemoryApiKeyProvider()
        assert provider.get("nonexistent") is None

    def test_create_duplicate_name_raises(self):
        provider = InMemoryApiKeyProvider()
        entry1 = ApiKeyEntry(key_id="k1", name="same-name", key_hash="h1")
        provider.create(entry1)
        entry2 = ApiKeyEntry(key_id="k2", name="same-name", key_hash="h2")
        with pytest.raises(ValueError, match="already exists"):
            provider.create(entry2)

    def test_create_different_names_ok(self):
        provider = InMemoryApiKeyProvider()
        provider.create(ApiKeyEntry(key_id="k1", name="name1", key_hash="h1"))
        provider.create(ApiKeyEntry(key_id="k2", name="name2", key_hash="h2"))
        assert len(provider.list()) == 2

    def test_get_by_hash(self):
        provider = InMemoryApiKeyProvider()
        entry = ApiKeyEntry(key_id="k1", key_hash="sha256$hash123")
        provider.create(entry)
        fetched = provider.get_by_hash("sha256$hash123")
        assert fetched is not None
        assert fetched.key_id == "k1"

    def test_get_by_hash_nonexistent(self):
        provider = InMemoryApiKeyProvider()
        assert provider.get_by_hash("sha256$nope") is None

    def test_list(self):
        provider = InMemoryApiKeyProvider()
        provider.create(ApiKeyEntry(key_id="k1", name="a"))
        provider.create(ApiKeyEntry(key_id="k2", name="b"))
        result = provider.list()
        assert len(result) == 2

    def test_list_empty(self):
        provider = InMemoryApiKeyProvider()
        assert provider.list() == []

    def test_update(self):
        provider = InMemoryApiKeyProvider()
        provider.create(ApiKeyEntry(key_id="k1", name="old", description="old desc"))
        updated = provider.update("k1", {"description": "new desc"})
        assert updated is not None
        assert updated.description == "new desc"

    def test_update_changes_name_index(self):
        provider = InMemoryApiKeyProvider()
        provider.create(ApiKeyEntry(key_id="k1", name="old-name"))
        provider.update("k1", {"name": "new-name"})
        # Old name should be available for reuse
        provider.create(ApiKeyEntry(key_id="k2", name="old-name"))
        assert len(provider.list()) == 2

    def test_update_nonexistent_returns_none(self):
        provider = InMemoryApiKeyProvider()
        assert provider.update("nonexistent", {"description": "test"}) is None

    def test_delete(self):
        provider = InMemoryApiKeyProvider()
        provider.create(ApiKeyEntry(key_id="k1", name="test", key_hash="h1"))
        assert provider.delete("k1") is True
        assert provider.get("k1") is None
        assert provider.get_by_hash("h1") is None

    def test_delete_nonexistent_returns_false(self):
        provider = InMemoryApiKeyProvider()
        assert provider.delete("nonexistent") is False

    def test_close_clears_all(self):
        provider = InMemoryApiKeyProvider()
        provider.create(ApiKeyEntry(key_id="k1", name="test", key_hash="h1"))
        provider.close()
        assert provider.list() == []

    def test_create_empty_key_id_raises(self):
        provider = InMemoryApiKeyProvider()
        with pytest.raises(ValueError, match="key_id"):
            provider.create(ApiKeyEntry(key_id=""))


# ---------------------------------------------------------------------------
# NullApiKeyProvider
# ---------------------------------------------------------------------------

class TestNullApiKeyProvider:
    """Test NullApiKeyProvider no-op behaviour."""

    def test_create_returns_entry(self):
        provider = NullApiKeyProvider()
        entry = ApiKeyEntry(key_id="k1")
        result = provider.create(entry)
        assert result.key_id == "k1"

    def test_get_returns_none(self):
        provider = NullApiKeyProvider()
        assert provider.get("anything") is None

    def test_get_by_hash_returns_none(self):
        provider = NullApiKeyProvider()
        assert provider.get_by_hash("anyhash") is None

    def test_list_returns_empty(self):
        provider = NullApiKeyProvider()
        assert provider.list() == []

    def test_update_returns_none(self):
        provider = NullApiKeyProvider()
        assert provider.update("k1", {"name": "test"}) is None

    def test_delete_returns_false(self):
        provider = NullApiKeyProvider()
        assert provider.delete("k1") is False

    def test_close_does_nothing(self):
        provider = NullApiKeyProvider()
        provider.close()  # should not raise


# ---------------------------------------------------------------------------
# ApiKeyManager
# ---------------------------------------------------------------------------

class TestApiKeyManager:
    """Test ApiKeyManager high-level operations."""

    def test_disabled_manager(self):
        mgr = ApiKeyManager(enabled=False)
        assert mgr.enabled is False
        assert isinstance(mgr.provider, NullApiKeyProvider)

    def test_enabled_manager(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        assert mgr.enabled is True
        assert isinstance(mgr.provider, InMemoryApiKeyProvider)

    def test_create_returns_entry_and_raw_key(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        entry, raw_key = mgr.create(name="test-key", roles=["user"])
        assert isinstance(entry, ApiKeyEntry)
        assert entry.name == "test-key"
        assert entry.roles == ["user"]
        assert raw_key.startswith("agk_")
        assert entry.key_prefix == raw_key[:12]

    def test_create_key_hash_not_equal_raw(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        entry, raw_key = mgr.create(name="test")
        assert entry.key_hash != raw_key
        assert verify_api_key_hash(raw_key, entry.key_hash)

    def test_verify_valid_key(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        entry, raw_key = mgr.create(name="test")
        verified = mgr.verify(raw_key)
        assert verified is not None
        assert verified.key_id == entry.key_id

    def test_verify_updates_usage_stats(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        entry, raw_key = mgr.create(name="test")
        assert entry.call_count == 0
        mgr.verify(raw_key)
        mgr.verify(raw_key)
        updated = mgr.get(entry.key_id)
        assert updated.call_count == 2
        assert updated.last_used_at != ""

    def test_verify_invalid_key_returns_none(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        assert mgr.verify("agk_invalid_key_that_does_not_exist") is None

    def test_verify_empty_key_returns_none(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        assert mgr.verify("") is None

    def test_verify_disabled_key_returns_none(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        entry, raw_key = mgr.create(name="test")
        mgr.revoke(entry.key_id)
        assert mgr.verify(raw_key) is None

    def test_verify_expired_key_returns_none(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        entry, raw_key = mgr.create(name="test", expires_at=past)
        assert mgr.verify(raw_key) is None

    def test_revoke_disables_key(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        entry, _ = mgr.create(name="test")
        revoked = mgr.revoke(entry.key_id)
        assert revoked is not None
        assert revoked.enabled is False

    def test_revoke_nonexistent_returns_none(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        assert mgr.revoke("nonexistent") is None

    def test_get_by_id(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        entry, _ = mgr.create(name="test")
        fetched = mgr.get(entry.key_id)
        assert fetched is not None
        assert fetched.name == "test"

    def test_get_nonexistent(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        assert mgr.get("nonexistent") is None

    def test_get_by_name(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        entry, _ = mgr.create(name="my-key")
        found = mgr.get_by_name("my-key")
        assert found is not None
        assert found.key_id == entry.key_id

    def test_get_by_name_nonexistent(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        assert mgr.get_by_name("nonexistent") is None

    def test_list(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        mgr.create(name="key1")
        mgr.create(name="key2")
        keys = mgr.list()
        assert len(keys) == 2

    def test_list_empty(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        assert mgr.list() == []

    def test_update(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        entry, _ = mgr.create(name="test")
        updated = mgr.update(entry.key_id, {"description": "new desc", "roles": ["admin"]})
        assert updated is not None
        assert updated.description == "new desc"
        assert updated.roles == ["admin"]

    def test_update_ignores_protected_fields(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        entry, raw_key = mgr.create(name="test")
        # key_hash and key_id should not be updatable
        mgr.update(entry.key_id, {"key_hash": "tampered", "key_id": "hacked"})
        result = mgr.get(entry.key_id)
        assert result.key_hash == entry.key_hash
        assert result.key_id == entry.key_id

    def test_update_nonexistent(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        assert mgr.update("nonexistent", {"description": "test"}) is None

    def test_delete(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        entry, _ = mgr.create(name="test")
        assert mgr.delete(entry.key_id) is True
        assert mgr.get(entry.key_id) is None

    def test_delete_nonexistent(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        assert mgr.delete("nonexistent") is False

    def test_create_duplicate_name_raises(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        mgr.create(name="duplicate")
        with pytest.raises(ValueError, match="already exists"):
            mgr.create(name="duplicate")

    def test_create_anonymous_key(self):
        """Keys without names should be allowed (multiple anonymous keys)."""
        mgr = ApiKeyManager(provider="memory", enabled=True)
        entry1, raw1 = mgr.create()
        entry2, raw2 = mgr.create()
        assert entry1.key_id != entry2.key_id
        assert raw1 != raw2

    def test_disabled_manager_returns_null_provider(self):
        mgr = ApiKeyManager(enabled=False)
        result = mgr.verify("any_key")
        assert result is None

    def test_unknown_provider_falls_back_to_null(self):
        mgr = ApiKeyManager(provider="nonexistent_provider", enabled=True)
        assert isinstance(mgr.provider, NullApiKeyProvider)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    """Test provider registry."""

    def test_list_providers_includes_defaults(self):
        names = list_apikey_providers()
        assert "memory" in names
        assert "null" in names

    def test_get_provider_memory(self):
        provider = get_apikey_provider("memory")
        assert isinstance(provider, InMemoryApiKeyProvider)

    def test_get_provider_null(self):
        provider = get_apikey_provider("null")
        assert isinstance(provider, NullApiKeyProvider)

    def test_get_provider_unknown_raises(self):
        from agentbase.runtime.errors import RegistryError

        with pytest.raises(RegistryError):
            get_apikey_provider("nonexistent")

    def test_register_custom_provider(self):
        @register_apikey_provider("test-custom")
        class CustomProvider:
            def __init__(self, **kwargs):
                pass

        provider = get_apikey_provider("test-custom")
        assert isinstance(provider, CustomProvider)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    """Test singleton management."""

    def test_get_default_manager_is_disabled(self):
        # Reset to ensure clean state
        set_apikey_manager(ApiKeyManager(enabled=False))
        mgr = get_apikey_manager()
        assert mgr.enabled is False

    def test_set_and_get_manager(self):
        custom = ApiKeyManager(provider="memory", enabled=True)
        set_apikey_manager(custom)
        assert get_apikey_manager() is custom

    def test_get_returns_same_instance(self):
        set_apikey_manager(ApiKeyManager(enabled=True))
        mgr1 = get_apikey_manager()
        mgr2 = get_apikey_manager()
        assert mgr1 is mgr2

        # Cleanup
        set_apikey_manager(ApiKeyManager(enabled=False))


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

class TestConcurrency:
    """Test thread safety of InMemoryApiKeyProvider."""

    def test_concurrent_creates(self):
        provider = InMemoryApiKeyProvider()
        errors: list[Exception] = []

        def create_keys(n: int):
            try:
                for i in range(n):
                    provider.create(
                        ApiKeyEntry(
                            key_id=f"k-{threading.current_thread().ident}-{i}",
                            name=f"key-{threading.current_thread().ident}-{i}",
                        )
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=create_keys, args=(10,)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(provider.list()) == 50

    def test_concurrent_verify(self):
        mgr = ApiKeyManager(provider="memory", enabled=True)
        _, raw_key = mgr.create(name="concurrent-test")
        errors: list[Exception] = []
        success_count = 0
        lock = threading.Lock()

        def verify_key(n: int):
            nonlocal success_count
            for _ in range(n):
                try:
                    result = mgr.verify(raw_key)
                    if result is not None:
                        with lock:
                            success_count += 1
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=verify_key, args=(20,)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert success_count == 100


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------

class TestProtocolCompliance:
    """Test that providers conform to ApiKeyProvider protocol."""

    def test_in_memory_is_provider(self):
        provider = InMemoryApiKeyProvider()
        assert isinstance(provider, ApiKeyProvider)

    def test_null_is_provider(self):
        provider = NullApiKeyProvider()
        assert isinstance(provider, ApiKeyProvider)
