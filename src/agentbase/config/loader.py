from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agentbase.config.schema import AgentConfig, AppConfig
from agentbase.config.settings import EnvSettings, get_env_settings, load_dotenv_files
from agentbase.runtime.errors import ConfigError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Config file must be a mapping: {path}")
    return data


def _resolve_profile() -> str | None:
    """Determine the active profile from the environment.

    Profiles allow loading environment-specific config overrides.
    Set ``AGENTBASE_PROFILE`` to ``dev``, ``staging``, ``prod``, etc.
    When a profile is set, the loader looks for ``configs/default.{profile}.yaml``
    and deep-merges it over the base config.
    """
    from agentbase.config.settings import env_get
    return env_get("AGENTBASE_PROFILE")


def _load_profile_overlay(root_dir: Path, profile: str) -> dict[str, Any]:
    """Load a profile-specific overlay config if it exists.

    Profile files are optional. If ``configs/default.{profile}.yaml``
    exists, it is deep-merged over the base config. If it doesn't
    exist, an empty overlay is returned (no error).
    """
    profile_path = root_dir / "configs" / f"default.{profile}.yaml"
    if not profile_path.exists():
        return {}
    logger.info("Loading profile overlay: %s", profile_path.name)
    return _read_yaml(profile_path)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _settings_overlay(settings: EnvSettings) -> dict[str, Any]:
    overlay: dict[str, Any] = {}

    mapping = {
        ("app", "env"): settings.app_env,
        ("app", "log_level"): settings.app_log_level,
        ("model", "provider"): settings.model_provider,
        ("model", "name"): settings.model_name,
        ("model", "temperature"): settings.model_temperature,
        ("model", "base_url"): settings.model_base_url,
        ("model", "api_key_env"): settings.model_api_key_env,
        ("backend", "type"): settings.backend_type,
        ("backend", "root_dir"): settings.backend_root_dir,
        ("checkpointer", "type"): settings.checkpointer_type,
        ("checkpointer", "dsn"): settings.checkpointer_dsn,
        ("storage", "type"): settings.storage_type,
        ("storage", "dsn"): settings.storage_dsn,
        ("storage", "db_dir"): settings.storage_db_dir,
        ("auth", "type"): settings.auth_type,
        ("auth", "secret"): settings.auth_secret,
        ("auth", "token_expiry_hours"): settings.auth_token_expiry_hours,
        ("rate_limit", "enabled"): settings.rate_limit_enabled,
        ("rate_limit", "max_requests"): settings.rate_limit_max_requests,
        ("rate_limit", "window_seconds"): settings.rate_limit_window_seconds,
        ("rate_limit", "burst"): settings.rate_limit_burst,
        ("metrics", "enabled"): settings.metrics_enabled,
        ("metrics", "path"): settings.metrics_path,
        ("runtime", "default_agent"): settings.runtime_default_agent,
        ("runtime", "config_dir"): settings.runtime_config_dir,
        ("runtime", "workspace_dir"): settings.runtime_workspace_dir,
        ("runtime", "session_ttl_seconds"): settings.runtime_session_ttl_seconds,
        ("audit", "enabled"): settings.audit_enabled,
        ("audit", "provider"): settings.audit_provider,
        ("audit", "db_dir"): settings.audit_db_dir,
        ("audit", "dsn"): settings.audit_dsn,
        ("redaction", "enabled"): settings.redaction_enabled,
        ("redaction", "provider"): settings.redaction_provider,
        ("secrets", "enabled"): settings.secrets_enabled,
        ("secrets", "provider"): settings.secrets_provider,
        ("experiment", "enabled"): settings.experiment_enabled,
        ("experiment", "provider"): settings.experiment_provider,
        ("model_manager", "enabled"): settings.model_manager_enabled,
        ("model_manager", "provider"): settings.model_manager_provider,
        ("prompt_manager", "enabled"): settings.prompt_manager_enabled,
        ("prompt_manager", "provider"): settings.prompt_manager_provider,
        ("user_manager", "enabled"): settings.user_manager_enabled,
        ("user_manager", "provider"): settings.user_manager_provider,
        ("apikey_manager", "enabled"): settings.apikey_manager_enabled,
        ("apikey_manager", "provider"): settings.apikey_manager_provider,
        ("oauth2", "enabled"): settings.oauth2_enabled,
        ("usage", "enabled"): settings.usage_enabled,
        ("usage", "provider"): settings.usage_provider,
        ("webhook", "enabled"): settings.webhook_enabled,
        ("webhook", "provider"): settings.webhook_provider,
        ("feedback", "enabled"): settings.feedback_enabled,
        ("feedback", "provider"): settings.feedback_provider,
        ("notification", "enabled"): settings.notification_enabled,
        ("notification", "provider"): settings.notification_provider,
        ("conversation", "enabled"): settings.conversation_enabled,
        ("conversation", "provider"): settings.conversation_provider,
        ("scheduler", "enabled"): settings.scheduler_enabled,
        ("scheduler", "provider"): settings.scheduler_provider,
        ("scheduler", "tick_seconds"): settings.scheduler_tick_seconds,
        ("calendar", "enabled"): settings.calendar_enabled,
        ("calendar", "provider"): settings.calendar_provider,
        ("migration", "enabled"): settings.migration_enabled,
    }
    for (section, key), value in mapping.items():
        if value is not None:
            overlay.setdefault(section, {})[key] = value

    # Drop empty sections
    return {k: v for k, v in overlay.items() if v}


def load_app_config(root_dir: Path) -> AppConfig:
    load_dotenv_files(root_dir)
    config_path = root_dir / "configs" / "default.yaml"
    raw = _read_yaml(config_path)

    # Load profile overlay if AGENTBASE_PROFILE is set
    profile = _resolve_profile()
    if profile:
        profile_overlay = _load_profile_overlay(root_dir, profile)
        raw = _deep_merge(raw, profile_overlay)

    overlay = _settings_overlay(get_env_settings())
    merged = _deep_merge(raw, overlay)

    # Compatibility with pre-existing provider keys from other projects.
    # Prefer explicit agentbase_* settings, then common provider envs.
    model = merged.setdefault("model", {})
    if not model.get("api_key_env"):
        for candidate in (
            "OPENAI_API_KEY",
            "SILICONFLOW_API_KEY",
            "DEEPSEEK_API_KEY",
            "AGNES_API_KEY",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
        ):
            from agentbase.config.settings import env_get

            if env_get(candidate):
                model["api_key_env"] = candidate
                break

    if not model.get("base_url"):
        from agentbase.config.settings import env_get

        base = env_get("OPENAI_BASE_URL", "SILICONFLOW_BASE_URL", "DEEPSEEK_BASE_URL", "MINICPM_CLOUD_BASE_URL")
        if base:
            model["base_url"] = base

    checkpointer = merged.setdefault("checkpointer", {})
    if not checkpointer.get("dsn"):
        from agentbase.config.settings import env_get

        dsn = env_get("AGENTBASE_CHECKPOINTER__DSN", "LANGGRAPH_CHECKPOINT_POSTGRES", "POSTGRES_DSN")
        if dsn and checkpointer.get("type") in {None, "memory", "postgres"}:
            # Keep type as configured; only fill DSN when useful.
            checkpointer["dsn"] = dsn

    try:
        return AppConfig.model_validate(merged)
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"Invalid app config: {exc}") from exc


def load_agent_config(config_dir: Path, agent_name: str) -> AgentConfig:
    path = config_dir / "agents" / f"{agent_name}.yaml"
    raw = _read_yaml(path)
    if "name" not in raw:
        raw["name"] = agent_name
    try:
        return AgentConfig.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"Invalid agent config '{agent_name}': {exc}") from exc


def list_agent_names(config_dir: Path) -> list[str]:
    agents_dir = config_dir / "agents"
    if not agents_dir.exists():
        return []
    names = sorted(p.stem for p in agents_dir.glob("*.yaml"))
    return names


def validate_config_dir(root_dir: Path) -> dict[str, Any]:
    """Validate a project's configuration directory structure.

    Returns a dict with:
    - ``valid``: True if all critical configs are loadable
    - ``errors``: list of error messages
    - ``warnings``: list of warning messages
    - ``agents``: list of agent names found
    - ``app_config``: the loaded AppConfig (if successful)
    """
    errors: list[str] = []
    warnings: list[str] = []
    agents: list[str] = []
    app_config = None

    config_path = root_dir / "configs" / "default.yaml"
    if not config_path.exists():
        errors.append(f"Missing config file: {config_path}")
    else:
        try:
            app_config = load_app_config(root_dir)
        except Exception as exc:
            errors.append(f"Failed to load app config: {exc}")

    agents_dir = root_dir / "configs" / "agents"
    if not agents_dir.exists():
        warnings.append("No agents directory found (configs/agents/)")
    else:
        agents = list_agent_names(root_dir / "configs")
        if not agents:
            warnings.append("No agent configs found")
        else:
            for name in agents:
                try:
                    load_agent_config(root_dir / "configs", name)
                except Exception as exc:
                    errors.append(f"Agent '{name}' config error: {exc}")

    workspace_dir = root_dir / "workspace"
    if not workspace_dir.exists():
        warnings.append("Workspace directory not found")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "agents": agents,
        "app_config": app_config,
    }
