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
    runtime__session_ttl_seconds: float | None = None

    audit__enabled: bool | None = None
    audit__provider: str | None = None
    audit__db_dir: str | None = None
    audit__dsn: str | None = None

    redaction__enabled: bool | None = None
    redaction__provider: str | None = None

    secrets__enabled: bool | None = None
    secrets__provider: str | None = None

    experiment__enabled: bool | None = None
    experiment__provider: str | None = None

    model_manager__enabled: bool | None = None
    model_manager__provider: str | None = None

    prompt_manager__enabled: bool | None = None
    prompt_manager__provider: str | None = None

    user_manager__enabled: bool | None = None
    user_manager__provider: str | None = None

    apikey_manager__enabled: bool | None = None
    apikey_manager__provider: str | None = None

    oauth2__enabled: bool | None = None

    usage__enabled: bool | None = None
    usage__provider: str | None = None

    webhook__enabled: bool | None = None
    webhook__provider: str | None = None

    feedback__enabled: bool | None = None
    feedback__provider: str | None = None

    notification__enabled: bool | None = None
    notification__provider: str | None = None

    conversation__enabled: bool | None = None
    conversation__provider: str | None = None

    scheduler__enabled: bool | None = None
    scheduler__provider: str | None = None
    scheduler__tick_seconds: float | None = None

    calendar__enabled: bool | None = None
    calendar__provider: str | None = None

    system_config__enabled: bool | None = None
    system_config__provider: str | None = None

    rbac__enabled: bool | None = None
    rbac__provider: str | None = None

    migration__enabled: bool | None = None

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

    @property
    def runtime_session_ttl_seconds(self) -> float | None:
        return self.runtime__session_ttl_seconds

    @property
    def audit_enabled(self) -> bool | None:
        return self.audit__enabled

    @property
    def audit_provider(self) -> str | None:
        return self.audit__provider

    @property
    def audit_db_dir(self) -> str | None:
        return self.audit__db_dir

    @property
    def audit_dsn(self) -> str | None:
        return self.audit__dsn

    @property
    def redaction_enabled(self) -> bool | None:
        return self.redaction__enabled

    @property
    def redaction_provider(self) -> str | None:
        return self.redaction__provider

    @property
    def secrets_enabled(self) -> bool | None:
        return self.secrets__enabled

    @property
    def secrets_provider(self) -> str | None:
        return self.secrets__provider

    @property
    def experiment_enabled(self) -> bool | None:
        return self.experiment__enabled

    @property
    def experiment_provider(self) -> str | None:
        return self.experiment__provider

    @property
    def model_manager_enabled(self) -> bool | None:
        return self.model_manager__enabled

    @property
    def model_manager_provider(self) -> str | None:
        return self.model_manager__provider

    @property
    def prompt_manager_enabled(self) -> bool | None:
        return self.prompt_manager__enabled

    @property
    def prompt_manager_provider(self) -> str | None:
        return self.prompt_manager__provider

    @property
    def user_manager_enabled(self) -> bool | None:
        return self.user_manager__enabled

    @property
    def user_manager_provider(self) -> str | None:
        return self.user_manager__provider

    @property
    def apikey_manager_enabled(self) -> bool | None:
        return self.apikey_manager__enabled

    @property
    def apikey_manager_provider(self) -> str | None:
        return self.apikey_manager__provider

    @property
    def oauth2_enabled(self) -> bool | None:
        return self.oauth2__enabled

    @property
    def usage_enabled(self) -> bool | None:
        return self.usage__enabled

    @property
    def usage_provider(self) -> str | None:
        return self.usage__provider

    @property
    def webhook_enabled(self) -> bool | None:
        return self.webhook__enabled

    @property
    def webhook_provider(self) -> str | None:
        return self.webhook__provider

    @property
    def feedback_enabled(self) -> bool | None:
        return self.feedback__enabled

    @property
    def feedback_provider(self) -> str | None:
        return self.feedback__provider

    @property
    def notification_enabled(self) -> bool | None:
        return self.notification__enabled

    @property
    def notification_provider(self) -> str | None:
        return self.notification__provider

    @property
    def conversation_enabled(self) -> bool | None:
        return self.conversation__enabled

    @property
    def conversation_provider(self) -> str | None:
        return self.conversation__provider

    @property
    def scheduler_enabled(self) -> bool | None:
        return self.scheduler__enabled

    @property
    def scheduler_provider(self) -> str | None:
        return self.scheduler__provider

    @property
    def scheduler_tick_seconds(self) -> float | None:
        return self.scheduler__tick_seconds

    @property
    def calendar_enabled(self) -> bool | None:
        return self.calendar__enabled

    @property
    def calendar_provider(self) -> str | None:
        return self.calendar__provider

    @property
    def system_config_enabled(self) -> bool | None:
        return self.system_config__enabled

    @property
    def system_config_provider(self) -> str | None:
        return self.system_config__provider

    @property
    def rbac_enabled(self) -> bool | None:
        return self.rbac__enabled

    @property
    def rbac_provider(self) -> str | None:
        return self.rbac__provider

    @property
    def migration_enabled(self) -> bool | None:
        return self.migration__enabled


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
