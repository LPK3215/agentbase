from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvSettings(BaseSettings):
    """Environment overlays. YAML remains the source of structure."""

    model_config = SettingsConfigDict(
        env_file=None,
        env_prefix="agentbase_",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    app__env: str | None = None
    app__log_level: str | None = None

    model__provider: str | None = None
    model__name: str | None = None
    model__temperature: float | None = None
    model__base_url: str | None = None
    model__api_key_env: str | None = None

    backend__type: str | None = None
    backend__root_dir: str | None = None

    checkpointer__type: str | None = None
    checkpointer__dsn: str | None = None

    storage__type: str | None = None
    storage__dsn: str | None = None
    storage__db_dir: str | None = None

    auth__type: str | None = None
    auth__secret: str | None = None
    auth__token_expiry_hours: int | None = None

    rate_limit__enabled: bool | None = None
    rate_limit__max_requests: int | None = None
    rate_limit__window_seconds: int | None = None
    rate_limit__burst: int | None = None

    metrics__enabled: bool | None = None
    metrics__path: str | None = None

    runtime__default_agent: str | None = None
    runtime__config_dir: str | None = None
    runtime__workspace_dir: str | None = None

    @property
    def app_env(self) -> str | None:
        return self.app__env

    @property
    def app_log_level(self) -> str | None:
        return self.app__log_level

    @property
    def model_provider(self) -> str | None:
        return self.model__provider

    @property
    def model_name(self) -> str | None:
        return self.model__name

    @property
    def model_temperature(self) -> float | None:
        return self.model__temperature

    @property
    def model_base_url(self) -> str | None:
        return self.model__base_url

    @property
    def model_api_key_env(self) -> str | None:
        return self.model__api_key_env

    @property
    def backend_type(self) -> str | None:
        return self.backend__type

    @property
    def backend_root_dir(self) -> str | None:
        return self.backend__root_dir

    @property
    def checkpointer_type(self) -> str | None:
        return self.checkpointer__type

    @property
    def checkpointer_dsn(self) -> str | None:
        return self.checkpointer__dsn

    @property
    def storage_type(self) -> str | None:
        return self.storage__type

    @property
    def storage_dsn(self) -> str | None:
        return self.storage__dsn

    @property
    def storage_db_dir(self) -> str | None:
        return self.storage__db_dir

    @property
    def auth_type(self) -> str | None:
        return self.auth__type

    @property
    def auth_secret(self) -> str | None:
        return self.auth__secret

    @property
    def auth_token_expiry_hours(self) -> int | None:
        return self.auth__token_expiry_hours

    @property
    def rate_limit_enabled(self) -> bool | None:
        return self.rate_limit__enabled

    @property
    def rate_limit_max_requests(self) -> int | None:
        return self.rate_limit__max_requests

    @property
    def rate_limit_window_seconds(self) -> int | None:
        return self.rate_limit__window_seconds

    @property
    def rate_limit_burst(self) -> int | None:
        return self.rate_limit__burst

    @property
    def metrics_enabled(self) -> bool | None:
        return self.metrics__enabled

    @property
    def metrics_path(self) -> str | None:
        return self.metrics__path

    @property
    def runtime_default_agent(self) -> str | None:
        return self.runtime__default_agent

    @property
    def runtime_config_dir(self) -> str | None:
        return self.runtime__config_dir

    @property
    def runtime_workspace_dir(self) -> str | None:
        return self.runtime__workspace_dir


def load_dotenv_files(root_dir: Path) -> None:
    """Load ``.env`` files in priority order.

    Loading order (later files override earlier):
    1. ``.env`` — base environment
    2. ``.env.local`` — local overrides (not committed to git)
    3. ``.env.{profile}`` — profile-specific (if AGENTBASE_PROFILE is set)

    ``.env.local`` and profile files are loaded with ``override=True``
    so they take precedence over the base ``.env``.
    """
    import os

    base_path = root_dir / ".env"
    if base_path.exists():
        load_dotenv(base_path, override=False)

    local_path = root_dir / ".env.local"
    if local_path.exists():
        load_dotenv(local_path, override=True)

    # Load profile-specific env file if profile is set
    profile = os.getenv("AGENTBASE_PROFILE")
    if profile:
        profile_env = root_dir / f".env.{profile}"
        if profile_env.exists():
            load_dotenv(profile_env, override=True)


@lru_cache(maxsize=1)
def get_env_settings() -> EnvSettings:
    return EnvSettings()


def env_get(*names: str, default: str | None = None) -> str | None:
    """Get the first non-empty environment variable from ``names``.

    Tries each name in order and returns the first non-empty value.
    If none are set, returns ``default``.

    Usage::

        # Try multiple env vars
        key = env_get("OPENAI_API_KEY", "SILICONFLOW_API_KEY")

        # With a default
        timeout = env_get("TIMEOUT", default="30")
    """
    import os

    for name in names:
        value = os.getenv(name)
        if value is not None and value != "":
            return value
    return default


def env_get_int(*names: str, default: int | None = None) -> int | None:
    """Get an integer environment variable.

    Returns ``default`` if the value is not set or cannot be parsed as int.
    """
    value = env_get(*names)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_get_float(*names: str, default: float | None = None) -> float | None:
    """Get a float environment variable.

    Returns ``default`` if the value is not set or cannot be parsed as float.
    """
    value = env_get(*names)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def env_get_bool(*names: str, default: bool | None = None) -> bool | None:
    """Get a boolean environment variable.

    Parses common truthy values: ``true``, ``1``, ``yes``, ``on`` (case-insensitive).
    Everything else is ``False``.

    Returns ``default`` if the value is not set.
    """
    value = env_get(*names)
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes", "on")
