"""Model factory — builds chat model instances from configuration.

Features:
- Model caching — identical configs reuse the same model instance
- API key resolution — tries multiple env vars per provider
- Model string composition — handles OpenAI-compatible gateways
- Compatibility fallback — tolerates LangChain API differences
"""
from __future__ import annotations

import threading
from typing import Any

from agentbase.config.schema import AgentModelOverride, AppConfig, ModelConfig
from agentbase.config.settings import env_get
from agentbase.runtime.errors import ErrorCode, FactoryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)

# Model cache — keyed by (provider, name, temperature, max_tokens, base_url)
_model_cache: dict[str, Any] = {}
_model_cache_lock = threading.Lock()


def _cache_key(model_cfg: ModelConfig) -> str:
    """Generate a cache key from model config."""
    return f"{model_cfg.provider}:{model_cfg.name}:{model_cfg.temperature}:{model_cfg.max_tokens}:{model_cfg.base_url}"


def _resolve_api_key(model_cfg: ModelConfig) -> str | None:
    if model_cfg.api_key_env:
        value = env_get(model_cfg.api_key_env)
        if value:
            return value

    provider = model_cfg.provider.lower()
    provider_env_map = {
        "openai": ["OPENAI_API_KEY", "SILICONFLOW_API_KEY", "AGNES_API_KEY", "DEEPSEEK_API_KEY"],
        "siliconflow": ["SILICONFLOW_API_KEY", "OPENAI_API_KEY"],
        "deepseek": ["DEEPSEEK_API_KEY", "OPENAI_API_KEY"],
        "anthropic": ["ANTHROPIC_API_KEY"],
        "google": ["GOOGLE_API_KEY"],
        "google_genai": ["GOOGLE_API_KEY"],
    }
    for key_name in provider_env_map.get(provider, ["OPENAI_API_KEY"]):
        value = env_get(key_name)
        if value:
            return value
    return None


def merge_model_config(app_config: AppConfig, override: AgentModelOverride | None) -> ModelConfig:
    base = app_config.model.model_copy(deep=True)
    if override is None:
        return base

    data = base.model_dump()
    for key, value in override.model_dump(exclude_none=True).items():
        if key == "extra":
            data["extra"] = {**data.get("extra", {}), **value}
        else:
            data[key] = value
    return ModelConfig.model_validate(data)


def build_model(model_cfg: ModelConfig) -> Any:
    """Build a chat model object using LangChain init_chat_model.

    Caches model instances by configuration — identical configs return
    the same model instance, avoiding unnecessary re-initialisation.
    """
    # Check cache first
    key = _cache_key(model_cfg)
    with _model_cache_lock:
        cached = _model_cache.get(key)
    if cached is not None:
        logger.debug("Model cache hit: %s", key)
        return cached

    try:
        from langchain.chat_models import init_chat_model
    except Exception as exc:  # noqa: BLE001
        raise FactoryError(
            f"langchain.chat_models.init_chat_model unavailable: {exc}",
            code=ErrorCode.FACTORY_MODEL_INIT,
        ) from exc

    api_key = _resolve_api_key(model_cfg)
    kwargs: dict[str, Any] = {
        "temperature": model_cfg.temperature,
    }
    if model_cfg.max_tokens is not None:
        kwargs["max_tokens"] = model_cfg.max_tokens
    if model_cfg.timeout_seconds is not None:
        kwargs["timeout"] = model_cfg.timeout_seconds
    if model_cfg.base_url:
        kwargs["base_url"] = model_cfg.base_url
    if api_key:
        kwargs["api_key"] = api_key
    kwargs.update(model_cfg.extra or {})

    model_name = model_cfg.model_string
    # OpenAI-compatible gateways often need provider openai + custom base_url.
    if model_cfg.base_url and model_cfg.provider in {"siliconflow", "deepseek", "agnes", "custom"}:
        model_name = f"openai:{model_cfg.name}"

    # Validate that we have an API key when one is needed
    if not api_key and model_cfg.provider not in {"ollama", "local", "none"}:
        logger.warning(
            "No API key found for provider='%s' model='%s' — model may fail at runtime",
            model_cfg.provider,
            model_cfg.name,
            extra={
                "event": "model.no_api_key",
                "provider": model_cfg.provider,
                "model": model_cfg.name,
            },
        )

    try:
        logger.info(
            "Building model: %s (provider=%s)",
            model_name,
            model_cfg.provider,
            extra={
                "event": "model.build",
                "model": model_name,
                "provider": model_cfg.provider,
            },
        )
        model = init_chat_model(model_name, **kwargs)
    except TypeError:
        # Some versions use model= kw-only differently
        try:
            model = init_chat_model(model=model_name, **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise FactoryError(
                f"Failed to init model '{model_name}': {exc}",
                code=ErrorCode.FACTORY_MODEL_INIT,
                detail={"model": model_name, "provider": model_cfg.provider},
            ) from exc
    except Exception as exc:  # noqa: BLE001
        raise FactoryError(
            f"Failed to init model '{model_name}': {exc}",
            code=ErrorCode.FACTORY_MODEL_INIT,
            detail={"model": model_name, "provider": model_cfg.provider},
        ) from exc

    # Cache the model instance
    with _model_cache_lock:
        _model_cache[key] = model

    return model


def clear_model_cache() -> int:
    """Clear the model cache. Returns the number of cleared entries."""
    with _model_cache_lock:
        count = len(_model_cache)
        _model_cache.clear()
        logger.info("Model cache cleared: %d entries", count)
        return count


def get_model_cache_size() -> int:
    """Return the number of cached model instances."""
    with _model_cache_lock:
        return len(_model_cache)
