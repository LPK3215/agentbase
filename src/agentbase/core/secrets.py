"""Secrets store — encrypted storage for sensitive configuration values.

Provides encrypted at-rest storage for API keys, passwords, and other
secrets that should not be stored in plaintext config files.

Providers:
- ``FernetSecretsProvider`` (default) — Fernet symmetric encryption + local key file
- ``EnvSecretsProvider`` — reads secrets from environment variables
- ``NullSecretsProvider`` — no-op passthrough (when disabled)

Register custom providers with ``@register_secrets_provider("name")``.

Usage::

    from agentbase.core.secrets import SecretsManager

    # Fernet encryption (requires `cryptography` package)
    manager = SecretsManager(provider="fernet", enabled=True, key_file=Path(".secret_key"))
    manager.set("OPENAI_API_KEY", "sk-xxxxx")
    value = manager.get("OPENAI_API_KEY")  # → "sk-xxxxx"
    # Stored encrypted on disk, decrypted on read

    # Env-based (no encryption, reads from os.environ)
    manager = SecretsManager(provider="env", enabled=True)
    value = manager.get("OPENAI_API_KEY")  # → os.environ["OPENAI_API_KEY"]
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from agentbase.runtime.errors import RegistryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class SecretsProvider(Protocol):
    """Protocol for secrets providers.

    Implementations must be thread-safe.
    """

    def set(self, key: str, value: str) -> None:
        """Store a secret value. Overwrites if key exists."""
        ...

    def get(self, key: str) -> str | None:
        """Retrieve a secret value. Returns None if not found."""
        ...

    def exists(self, key: str) -> bool:
        """Check if a secret exists."""
        ...

    def delete(self, key: str) -> bool:
        """Delete a secret. Returns True if deleted, False if not found."""
        ...

    def list_keys(self) -> list[str]:
        """List all secret key names (not values)."""
        ...

    def close(self) -> None:
        """Release resources."""
        ...


# ---------------------------------------------------------------------------
# Null provider (passthrough, zero overhead)
# ---------------------------------------------------------------------------

class NullSecretsProvider:
    """No-op secrets provider — returns None for all gets.

    Used when secrets management is disabled (``secrets.enabled=false``).
    When used through ``SecretsManager`` with ``transparent=True``,
    falls back to environment variables.
    """

    def __init__(self, *, transparent: bool = False) -> None:
        self._transparent = transparent

    def set(self, key: str, value: str) -> None:
        # No-op, but set in env if transparent
        if self._transparent:
            os.environ[key] = value

    def get(self, key: str) -> str | None:
        if self._transparent:
            return os.environ.get(key)
        return None

    def exists(self, key: str) -> bool:
        if self._transparent:
            return key in os.environ
        return False

    def delete(self, key: str) -> bool:
        if self._transparent and key in os.environ:
            del os.environ[key]
            return True
        return False

    def list_keys(self) -> list[str]:
        return []

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Fernet provider (default, requires cryptography)
# ---------------------------------------------------------------------------

class FernetSecretsProvider:
    """Fernet symmetric encryption provider.

    Stores encrypted secrets in a JSON file on disk. The encryption
    key is stored in a separate key file (default ``.secret_key``).

    Requires the ``cryptography`` package. Install with::

        pip install agentbase[secrets]

    Usage::

        provider = FernetSecretsProvider(
            secrets_file=Path(".secrets.json"),
            key_file=Path(".secret_key"),
        )
        provider.set("API_KEY", "sk-xxxxx")
        assert provider.get("API_KEY") == "sk-xxxxx"
    """

    def __init__(
        self,
        *,
        secrets_file: Path | None = None,
        key_file: Path | None = None,
    ) -> None:
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:
            raise ImportError(
                "FernetSecretsProvider requires cryptography. "
                "Install with: pip install agentbase[secrets]"
            ) from exc

        self._Fernet = Fernet
        self._lock = threading.RLock()
        self._secrets_file = secrets_file or Path(".secrets.json")
        self._key_file = key_file or Path(".secret_key")
        self._fernet: Fernet | None = None
        self._data: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        """Load or generate the encryption key and encrypted secrets."""
        # Load or generate key
        if self._key_file.exists():
            key = self._key_file.read_bytes()
        else:
            key = self._Fernet.generate_key()
            self._key_file.write_bytes(key)
            # Restrict file permissions on Unix
            try:
                self._key_file.chmod(0o600)
            except (OSError, PermissionError):
                pass  # Windows doesn't support chmod the same way

        self._fernet = self._Fernet(key)

        # Load encrypted secrets
        if self._secrets_file.exists():
            try:
                raw = self._secrets_file.read_text(encoding="utf-8")
                self._data = json.loads(raw) if raw.strip() else {}
            except (json.JSONDecodeError, ValueError) as exc:
                raise RegistryError(
                    f"Corrupted secrets file {self._secrets_file}: {exc}"
                ) from exc

    def _save(self) -> None:
        """Save encrypted secrets to disk."""
        self._secrets_file.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        try:
            self._secrets_file.chmod(0o600)
        except (OSError, PermissionError):
            pass

    def _encrypt(self, value: str) -> str:
        """Encrypt a plaintext value."""
        assert self._fernet is not None
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def _decrypt(self, token: str) -> str:
        """Decrypt an encrypted token."""
        assert self._fernet is not None
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except Exception as exc:
            raise RegistryError(
                f"Failed to decrypt secret: invalid key or corrupted data. {exc}"
            ) from exc

    def set(self, key: str, value: str) -> None:
        """Store an encrypted secret."""
        with self._lock:
            self._data[key] = self._encrypt(value)
            self._save()
        logger.debug(
            "Secret stored: %s",
            key,
            extra={"event": "secrets.set", "key": key},
        )

    def get(self, key: str) -> str | None:
        """Retrieve and decrypt a secret."""
        with self._lock:
            token = self._data.get(key)
        if token is None:
            return None
        return self._decrypt(token)

    def exists(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def delete(self, key: str) -> bool:
        with self._lock:
            if key not in self._data:
                return False
            self._data.pop(key, None)
            self._save()
            return True

    def list_keys(self) -> list[str]:
        with self._lock:
            return sorted(self._data.keys())

    def close(self) -> None:
        # Nothing to close for file-based
        pass

    def rotate_key(self) -> None:
        """Generate a new encryption key and re-encrypt all secrets.

        Useful for key rotation in security policies.
        """
        with self._lock:
            # Decrypt all with old key
            plaintext: dict[str, str] = {}
            for key, token in self._data.items():
                plaintext[key] = self._decrypt(token)

            # Generate new key
            new_key = self._Fernet.generate_key()
            self._key_file.write_bytes(new_key)
            try:
                self._key_file.chmod(0o600)
            except (OSError, PermissionError):
                pass
            self._fernet = self._Fernet(new_key)

            # Re-encrypt all with new key
            self._data = {k: self._encrypt(v) for k, v in plaintext.items()}
            self._save()

        logger.info("Encryption key rotated successfully")


# ---------------------------------------------------------------------------
# Env provider (reads from environment variables)
# ---------------------------------------------------------------------------

class EnvSecretsProvider:
    """Environment-based secrets provider.

    Reads secrets directly from environment variables.
    No encryption, no file storage — just a thin wrapper around ``os.environ``.

    Useful for Docker / K8s deployments where secrets are injected
    via env vars.
    """

    def __init__(self, *, prefix: str = "") -> None:
        self._prefix = prefix

    def _resolve_key(self, key: str) -> str:
        return f"{self._prefix}{key}" if self._prefix else key

    def set(self, key: str, value: str) -> None:
        os.environ[self._resolve_key(key)] = value

    def get(self, key: str) -> str | None:
        return os.environ.get(self._resolve_key(key))

    def exists(self, key: str) -> bool:
        return self._resolve_key(key) in os.environ

    def delete(self, key: str) -> bool:
        env_key = self._resolve_key(key)
        if env_key in os.environ:
            del os.environ[env_key]
            return True
        return False

    def list_keys(self) -> list[str]:
        if self._prefix:
            return sorted(
                k[len(self._prefix):] for k in os.environ
                if k.startswith(self._prefix)
            )
        return sorted(os.environ.keys())

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class SecretsRegistry:
    """Thread-safe registry for secrets providers."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., SecretsProvider]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        factory: Callable[..., SecretsProvider],
        *,
        override: bool = False,
    ) -> None:
        key = name.strip().lower()
        with self._lock:
            if not key:
                raise RegistryError("Cannot register empty secrets provider name")
            if key in self._factories and not override:
                raise RegistryError(f"Secrets provider already registered: {key}")
            self._factories[key] = factory

    def create(self, name: str, **kwargs: Any) -> SecretsProvider:
        key = name.strip().lower()
        with self._lock:
            if key not in self._factories:
                available = ", ".join(sorted(self._factories)) or "<empty>"
                raise RegistryError(f"Unknown secrets provider: {key}. Available: {available}")
            factory = self._factories[key]
        return factory(**kwargs)

    def has(self, name: str) -> bool:
        with self._lock:
            return name.strip().lower() in self._factories

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._factories.keys())

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._factories)

    def unregister(self, name: str) -> bool:
        key = name.strip().lower()
        with self._lock:
            if key not in self._factories:
                return False
            self._factories.pop(key, None)
            return True


# Global singleton
secrets_registry = SecretsRegistry()

# Register defaults
secrets_registry.register("null", NullSecretsProvider)
secrets_registry.register("fernet", FernetSecretsProvider)
secrets_registry.register("env", EnvSecretsProvider)


def register_secrets_provider(name: str, *, override: bool = False):
    """Decorator: register a secrets provider class.

    Usage::

        @register_secrets_provider("vault")
        class VaultSecretsProvider:
            def get(self, key: str) -> str | None: ...
    """
    def decorator(factory: Callable[..., SecretsProvider]):
        secrets_registry.register(name, factory, override=override)
        return factory
    return decorator


# ---------------------------------------------------------------------------
# Manager — high-level facade
# ---------------------------------------------------------------------------

class SecretsManager:
    """High-level secrets manager.

    Wraps a ``SecretsProvider`` and provides convenience methods.
    When ``enabled=False``, uses ``NullSecretsProvider`` with
    transparent env var fallback (so code works the same way,
    just without encryption).

    Usage::

        manager = SecretsManager(
            provider="fernet",
            enabled=True,
            secrets_file=Path(".secrets.json"),
            key_file=Path(".secret_key"),
        )
        manager.set_secret("API_KEY", "sk-xxxxx")
        value = manager.get_secret("API_KEY")  # → "sk-xxxxx"
    """

    def __init__(
        self,
        *,
        provider: str = "null",
        enabled: bool = False,
        **provider_kwargs: Any,
    ) -> None:
        self._enabled = enabled
        if not enabled:
            self._provider: SecretsProvider = NullSecretsProvider(transparent=True)
        else:
            self._provider = secrets_registry.create(provider, **provider_kwargs)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_secret(self, key: str, value: str) -> None:
        """Store a secret value."""
        self._provider.set(key, value)

    def get_secret(self, key: str, default: str | None = None) -> str | None:
        """Retrieve a secret value.

        Falls back to environment variable if not found in the provider.
        """
        value = self._provider.get(key)
        if value is not None:
            return value
        # Fallback to env var
        return os.environ.get(key, default)

    def has_secret(self, key: str) -> bool:
        """Check if a secret exists."""
        return self._provider.exists(key) or key in os.environ

    def delete_secret(self, key: str) -> bool:
        """Delete a secret."""
        return self._provider.delete(key)

    def list_secret_keys(self) -> list[str]:
        """List all secret key names."""
        return self._provider.list_keys()

    def close(self) -> None:
        self._provider.close()
