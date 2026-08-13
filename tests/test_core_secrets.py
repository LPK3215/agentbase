"""Tests for secrets store — encryption round-trip, bad key, disabled passthrough."""
from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from agentbase.core.secrets import (
    EnvSecretsProvider,
    FernetSecretsProvider,
    NullSecretsProvider,
    SecretsManager,
    SecretsRegistry,
    secrets_registry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fernet_provider(tmp_path):
    """Create a FernetSecretsProvider with temp files."""
    provider = FernetSecretsProvider(
        secrets_file=tmp_path / ".secrets.json",
        key_file=tmp_path / ".secret_key",
    )
    yield provider
    provider.close()


# ---------------------------------------------------------------------------
# NullSecretsProvider
# ---------------------------------------------------------------------------

class TestNullSecretsProvider:
    def test_get_returns_none(self):
        provider = NullSecretsProvider()
        assert provider.get("ANY_KEY") is None

    def test_exists_returns_false(self):
        provider = NullSecretsProvider()
        assert provider.exists("ANY_KEY") is False

    def test_list_keys_empty(self):
        provider = NullSecretsProvider()
        assert provider.list_keys() == []

    def test_transparent_env_fallback(self):
        """Transparent mode should read from env vars."""
        os.environ["TEST_SECRET"] = "test_value"
        try:
            provider = NullSecretsProvider(transparent=True)
            assert provider.get("TEST_SECRET") == "test_value"
            assert provider.exists("TEST_SECRET") is True
        finally:
            del os.environ["TEST_SECRET"]

    def test_transparent_set(self):
        """Transparent mode should set env vars."""
        provider = NullSecretsProvider(transparent=True)
        provider.set("TRANSPARENT_TEST", "value123")
        assert os.environ["TRANSPARENT_TEST"] == "value123"
        del os.environ["TRANSPARENT_TEST"]


# ---------------------------------------------------------------------------
# FernetSecretsProvider
# ---------------------------------------------------------------------------

class TestFernetSecretsProvider:
    def test_encrypt_decrypt_roundtrip(self, fernet_provider):
        """Set and get should return the original value."""
        fernet_provider.set("API_KEY", "sk-abc123def456")
        assert fernet_provider.get("API_KEY") == "sk-abc123def456"

    def test_multiple_secrets(self, fernet_provider):
        """Multiple secrets should be stored independently."""
        fernet_provider.set("KEY1", "value1")
        fernet_provider.set("KEY2", "value2")
        assert fernet_provider.get("KEY1") == "value1"
        assert fernet_provider.get("KEY2") == "value2"

    def test_overwrite(self, fernet_provider):
        """Setting the same key should overwrite."""
        fernet_provider.set("KEY", "old_value")
        fernet_provider.set("KEY", "new_value")
        assert fernet_provider.get("KEY") == "new_value"

    def test_get_nonexistent(self, fernet_provider):
        """Getting a non-existent key should return None."""
        assert fernet_provider.get("NONEXISTENT") is None

    def test_exists(self, fernet_provider):
        """Exists should return True for set keys, False for missing."""
        fernet_provider.set("EXISTS_KEY", "value")
        assert fernet_provider.exists("EXISTS_KEY") is True
        assert fernet_provider.exists("MISSING_KEY") is False

    def test_delete(self, fernet_provider):
        """Delete should remove a secret."""
        fernet_provider.set("DELETE_ME", "value")
        assert fernet_provider.delete("DELETE_ME") is True
        assert fernet_provider.get("DELETE_ME") is None
        assert fernet_provider.delete("DELETE_ME") is False  # already deleted

    def test_list_keys(self, fernet_provider):
        """List keys should return all stored key names."""
        fernet_provider.set("KEY_A", "a")
        fernet_provider.set("KEY_B", "b")
        keys = fernet_provider.list_keys()
        assert "KEY_A" in keys
        assert "KEY_B" in keys

    def test_data_is_encrypted_on_disk(self, fernet_provider, tmp_path):
        """The secrets file should not contain plaintext values."""
        fernet_provider.set("SECRET_KEY", "sk-super-secret-value")
        raw_content = (tmp_path / ".secrets.json").read_text()
        assert "sk-super-secret-value" not in raw_content

    def test_key_file_created(self, tmp_path):
        """The key file should be created on first use."""
        key_file = tmp_path / ".my_key"
        assert not key_file.exists()
        provider = FernetSecretsProvider(
            secrets_file=tmp_path / ".secrets.json",
            key_file=key_file,
        )
        assert key_file.exists()
        provider.close()

    def test_persistence_across_instances(self, tmp_path):
        """Secrets should persist across provider instances."""
        secrets_file = tmp_path / ".secrets.json"
        key_file = tmp_path / ".secret_key"

        p1 = FernetSecretsProvider(secrets_file=secrets_file, key_file=key_file)
        p1.set("PERSIST_KEY", "persisted_value")
        p1.close()

        p2 = FernetSecretsProvider(secrets_file=secrets_file, key_file=key_file)
        assert p2.get("PERSIST_KEY") == "persisted_value"
        p2.close()

    def test_bad_key_raises_error(self, tmp_path):
        """A corrupted key should raise an error."""
        secrets_file = tmp_path / ".secrets.json"
        key_file = tmp_path / ".secret_key"

        p1 = FernetSecretsProvider(secrets_file=secrets_file, key_file=key_file)
        p1.set("MY_KEY", "my_value")
        p1.close()

        # Corrupt the key file with invalid base64
        key_file.write_bytes(b"bad-key-data-bad-key-data-bad-key-data")

        # Loading with a corrupted key should raise
        with pytest.raises((ValueError, Exception)):
            FernetSecretsProvider(secrets_file=secrets_file, key_file=key_file)

    def test_rotate_key(self, tmp_path):
        """Key rotation should re-encrypt all secrets."""
        secrets_file = tmp_path / ".secrets.json"
        key_file = tmp_path / ".secret_key"

        provider = FernetSecretsProvider(secrets_file=secrets_file, key_file=key_file)
        provider.set("ROTATE_A", "val_a")
        provider.set("ROTATE_B", "val_b")

        old_key = key_file.read_bytes()

        provider.rotate_key()

        new_key = key_file.read_bytes()
        assert old_key != new_key  # Key changed

        # Values should still be readable with new key
        assert provider.get("ROTATE_A") == "val_a"
        assert provider.get("ROTATE_B") == "val_b"
        provider.close()

    def test_concurrent_writes(self, fernet_provider):
        """Concurrent writes should be thread-safe."""
        def writer(start: int, count: int) -> None:
            for i in range(start, start + count):
                fernet_provider.set(f"CONCURRENT_{i}", f"value_{i}")

        threads = []
        for i in range(4):
            t = threading.Thread(target=writer, args=(i * 25, 25))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        # All 100 secrets should be stored
        keys = fernet_provider.list_keys()
        concurrent_keys = [k for k in keys if k.startswith("CONCURRENT_")]
        assert len(concurrent_keys) == 100


# ---------------------------------------------------------------------------
# EnvSecretsProvider
# ---------------------------------------------------------------------------

class TestEnvSecretsProvider:
    def test_get_from_env(self):
        os.environ["ENV_SECRET_TEST"] = "env_value"
        try:
            provider = EnvSecretsProvider()
            assert provider.get("ENV_SECRET_TEST") == "env_value"
            assert provider.exists("ENV_SECRET_TEST") is True
        finally:
            del os.environ["ENV_SECRET_TEST"]

    def test_set_to_env(self):
        provider = EnvSecretsProvider()
        provider.set("ENV_SET_TEST", "set_value")
        assert os.environ["ENV_SET_TEST"] == "set_value"
        del os.environ["ENV_SET_TEST"]

    def test_prefix(self):
        """Provider with prefix should prepend to key names."""
        os.environ["MY_APP_SECRET"] = "prefixed"
        try:
            provider = EnvSecretsProvider(prefix="MY_APP_")
            assert provider.get("SECRET") == "prefixed"
        finally:
            del os.environ["MY_APP_SECRET"]

    def test_get_nonexistent(self):
        provider = EnvSecretsProvider()
        assert provider.get("NONEXISTENT_ENV_VAR") is None

    def test_delete(self):
        os.environ["ENV_DELETE_ME"] = "temp"
        provider = EnvSecretsProvider()
        assert provider.delete("ENV_DELETE_ME") is True
        assert "ENV_DELETE_ME" not in os.environ


# ---------------------------------------------------------------------------
# SecretsRegistry
# ---------------------------------------------------------------------------

class TestSecretsRegistry:
    def test_register_and_create(self):
        reg = SecretsRegistry()
        reg.register("test_prov", FernetSecretsProvider)
        assert reg.has("test_prov")
        provider = reg.create("test_prov", secrets_file=Path(".test_secrets.json"), key_file=Path(".test_key"))
        assert isinstance(provider, FernetSecretsProvider)
        provider.close()
        # Cleanup
        Path(".test_secrets.json").unlink(missing_ok=True)
        Path(".test_key").unlink(missing_ok=True)

    def test_register_duplicate_raises(self):
        reg = SecretsRegistry()
        reg.register("dup_prov", NullSecretsProvider)
        with pytest.raises(Exception, match="already registered"):
            reg.register("dup_prov", NullSecretsProvider)

    def test_unregister(self):
        reg = SecretsRegistry()
        reg.register("temp_prov", NullSecretsProvider)
        assert reg.unregister("temp_prov") is True
        assert not reg.has("temp_prov")
        assert reg.unregister("temp_prov") is False

    def test_create_unknown_raises(self):
        reg = SecretsRegistry()
        with pytest.raises(Exception, match="Unknown secrets provider"):
            reg.create("nonexistent")

    def test_count(self):
        reg = SecretsRegistry()
        assert reg.count == 0
        reg.register("a", NullSecretsProvider)
        assert reg.count == 1

    def test_global_registry_has_defaults(self):
        assert secrets_registry.has("null")
        assert secrets_registry.has("fernet")
        assert secrets_registry.has("env")


# ---------------------------------------------------------------------------
# SecretsManager
# ---------------------------------------------------------------------------

class TestSecretsManager:
    def test_disabled_manager_uses_env(self):
        """When disabled, manager should fall back to env vars."""
        os.environ["MANAGER_DISABLED_TEST"] = "from_env"
        try:
            mgr = SecretsManager(provider="fernet", enabled=False)
            assert mgr.enabled is False
            assert mgr.get_secret("MANAGER_DISABLED_TEST") == "from_env"
            assert mgr.has_secret("MANAGER_DISABLED_TEST") is True
        finally:
            del os.environ["MANAGER_DISABLED_TEST"]

    def test_enabled_manager_encrypts(self, tmp_path):
        """When enabled, manager should encrypt and decrypt."""
        mgr = SecretsManager(
            provider="fernet",
            enabled=True,
            secrets_file=tmp_path / ".secrets.json",
            key_file=tmp_path / ".secret_key",
        )
        mgr.set_secret("ENCRYPTED_KEY", "encrypted_value")
        assert mgr.get_secret("ENCRYPTED_KEY") == "encrypted_value"
        assert mgr.has_secret("ENCRYPTED_KEY") is True
        mgr.close()

    def test_manager_fallback_to_env(self, tmp_path):
        """Manager should fall back to env if not in provider."""
        os.environ["FALLBACK_TEST"] = "fallback_value"
        try:
            mgr = SecretsManager(
                provider="fernet",
                enabled=True,
                secrets_file=tmp_path / ".secrets.json",
                key_file=tmp_path / ".secret_key",
            )
            assert mgr.get_secret("FALLBACK_TEST") == "fallback_value"
            mgr.close()
        finally:
            del os.environ["FALLBACK_TEST"]

    def test_manager_default_value(self):
        """get_secret should return default when not found."""
        mgr = SecretsManager(enabled=False)
        assert mgr.get_secret("NONEXISTENT", default="default_val") == "default_val"

    def test_manager_delete_secret(self, tmp_path):
        """delete_secret should remove from provider."""
        mgr = SecretsManager(
            provider="fernet",
            enabled=True,
            secrets_file=tmp_path / ".secrets.json",
            key_file=tmp_path / ".secret_key",
        )
        mgr.set_secret("DELETE_KEY", "value")
        assert mgr.delete_secret("DELETE_KEY") is True
        assert mgr.get_secret("DELETE_KEY") is None
        mgr.close()

    def test_manager_list_keys(self, tmp_path):
        """list_secret_keys should return provider keys."""
        mgr = SecretsManager(
            provider="fernet",
            enabled=True,
            secrets_file=tmp_path / ".secrets.json",
            key_file=tmp_path / ".secret_key",
        )
        mgr.set_secret("LIST_A", "a")
        mgr.set_secret("LIST_B", "b")
        keys = mgr.list_secret_keys()
        assert "LIST_A" in keys
        assert "LIST_B" in keys
        mgr.close()
