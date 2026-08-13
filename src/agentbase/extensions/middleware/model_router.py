"""Model router middleware — routes model calls to different models per strategy.

Supports multiple routing strategies:
- ``round_robin`` — cycle through models in order (default)
- ``weighted`` — random selection weighted by ``weight`` field
- ``random`` — uniform random selection
- ``failover`` — try primary first, fall back to next on error

Uses ``langchain.agents.middleware.wrap_model_call`` to intercept the
model call request, swap the ``request.model`` to the selected model,
then delegate to the original handler.

Configuration via ``agent_config.metadata.model_router``:

.. code-block:: yaml

    metadata:
      model_router:
        strategy: round_robin   # round_robin | weighted | random | failover
        models:
          - provider: openai
            name: gpt-4.1-mini
            weight: 3            # only for weighted strategy
          - provider: openai
            name: gpt-4.1
            weight: 1
            api_key_env: OPENAI_API_KEY
          - provider: deepseek
            name: deepseek-chat
            weight: 1
            api_key_env: DEEPSEEK_API_KEY
            base_url: https://api.deepseek.com/v1

For ``failover`` strategy, the first model in the list is the primary.
On error, the next model is tried, and so on until one succeeds or
all are exhausted.

Thread-safe via ``threading.Lock`` for round-robin index and failover state.
"""
from __future__ import annotations

import random
import threading
from typing import Any, Callable

from agentbase.extensions._meta import ExtensionMeta
from agentbase.factories.model_factory import build_model
from agentbase.config.schema import ModelConfig
from agentbase.registry.middleware import register_middleware
from agentbase.runtime.errors import ErrorCode, RuntimeExecutionError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)

_MODEL_ROUTER_META = ExtensionMeta(
    name="model_router",
    kind="middleware",
    description="Route model calls to different models per strategy (round_robin/weighted/random/failover).",
    requires_context=["agent_config"],
    default_enabled=False,
)

_VALID_STRATEGIES = {"round_robin", "weighted", "random", "failover"}


class ModelRouter:
    """Thread-safe model router with pluggable strategies.

    Holds pre-built model instances and selects one per call according
    to the configured strategy.

    Attributes:
        models: List of built model objects.
        strategy: Routing strategy name.
        weights: Per-model weights (for ``weighted`` strategy).
    """

    def __init__(
        self,
        *,
        models: list[Any],
        strategy: str = "round_robin",
        weights: list[int] | None = None,
    ) -> None:
        if not models:
            raise ValueError("ModelRouter requires at least one model")
        self._models = models
        self._strategy = strategy
        self._weights = weights or [1] * len(models)
        self._index = 0
        self._lock = threading.Lock()
        # Pre-calculate cumulative weights for weighted selection
        self._cumulative: list[int] = []
        total = 0
        for w in self._weights:
            total += max(0, w)
            self._cumulative.append(total)
        self._total_weight = total or 1

    @property
    def strategy(self) -> str:
        return self._strategy

    @property
    def model_count(self) -> int:
        return len(self._models)

    @property
    def stats(self) -> dict[str, Any]:
        """Return router statistics for observability."""
        with self._lock:
            return {
                "strategy": self._strategy,
                "model_count": len(self._models),
                "current_index": self._index,
                "weights": list(self._weights),
                "total_weight": self._total_weight,
            }

    def select(self) -> Any:
        """Select the next model according to the strategy.

        Returns the selected model object.
        """
        with self._lock:
            if self._strategy == "round_robin":
                model = self._models[self._index % len(self._models)]
                self._index = (self._index + 1) % len(self._models)
                return model

            if self._strategy == "weighted":
                target = random.randint(1, self._total_weight)
                for i, cum in enumerate(self._cumulative):
                    if target <= cum:
                        return self._models[i]
                return self._models[-1]

            if self._strategy == "random":
                return random.choice(self._models)

            # failover: always return primary (index 0)
            return self._models[0]

    def select_failover(self, failed_index: int) -> Any | None:
        """Get the next model for failover after a failure.

        Returns None if no more models to try.
        """
        with self._lock:
            next_index = failed_index + 1
            if next_index >= len(self._models):
                return None
            return self._models[next_index]


def _build_models_from_config(
    model_configs: list[dict[str, Any]],
) -> tuple[list[Any], list[int]]:
    """Build model instances and extract weights from config dicts.

    Returns (models, weights).
    """
    models: list[Any] = []
    weights: list[int] = []

    for cfg_dict in model_configs:
        # Extract weight before building (not a ModelConfig field)
        weight = int(cfg_dict.pop("weight", 1))

        # Build ModelConfig from dict
        model_cfg = ModelConfig(
            provider=cfg_dict.get("provider", "openai"),
            name=cfg_dict.get("name", "gpt-4.1-mini"),
            temperature=float(cfg_dict.get("temperature", 0.0)),
            max_tokens=cfg_dict.get("max_tokens"),
            timeout_seconds=int(cfg_dict.get("timeout_seconds", 120)),
            base_url=cfg_dict.get("base_url"),
            api_key_env=cfg_dict.get("api_key_env"),
            extra=cfg_dict.get("extra", {}),
        )

        try:
            model = build_model(model_cfg)
            models.append(model)
            weights.append(weight)
            logger.info(
                "Model router: built model '%s' (provider=%s, weight=%d)",
                model_cfg.name,
                model_cfg.provider,
                weight,
                extra={
                    "event": "model_router.model_built",
                    "model": model_cfg.name,
                    "provider": model_cfg.provider,
                    "weight": weight,
                },
            )
        except Exception as exc:
            logger.warning(
                "Model router: failed to build model '%s' — skipping: %s",
                cfg_dict.get("name", "unknown"),
                exc,
                extra={
                    "event": "model_router.build_failed",
                    "model": cfg_dict.get("name", "unknown"),
                    "error": str(exc),
                },
            )

    return models, weights


@register_middleware("model_router", meta=_MODEL_ROUTER_META)
def build_model_router(context: dict[str, Any] | None = None):
    """Build model-router middleware from agent config context.

    Reads configuration from ``agent_config.metadata.model_router``:
    - ``strategy``: ``"round_robin"`` | ``"weighted"`` | ``"random"`` | ``"failover"``
    - ``models``: list of model config dicts (provider, name, weight, etc.)
    """
    context = context or {}
    agent_config = context.get("agent_config")

    strategy = "round_robin"
    model_configs: list[dict[str, Any]] = []

    if agent_config is not None:
        router_cfg = agent_config.metadata.get("model_router", {})
        strategy = router_cfg.get("strategy", "round_robin")
        model_configs = router_cfg.get("models", [])

    if strategy not in _VALID_STRATEGIES:
        logger.warning(
            "Model router: invalid strategy '%s', falling back to 'round_robin'",
            strategy,
            extra={"event": "model_router.invalid_strategy", "strategy": strategy},
        )
        strategy = "round_robin"

    if not model_configs:
        logger.warning(
            "middleware disabled: name=model_router reason=no_models_configured",
            extra={"event": "middleware.disabled"},
        )
        return []

    # Build model instances from config
    models, weights = _build_models_from_config(
        [dict(cfg) for cfg in model_configs]  # deep copy to avoid mutating original
    )

    if not models:
        logger.warning(
            "middleware disabled: name=model_router reason=no_models_built",
            extra={"event": "middleware.disabled"},
        )
        return []

    router = ModelRouter(models=models, strategy=strategy, weights=weights)

    try:
        from langchain.agents.middleware import wrap_model_call
    except Exception:
        logger.warning(
            "middleware disabled: name=model_router reason=wrap_model_call_unavailable",
            extra={"event": "middleware.disabled"},
        )
        return []

    if strategy == "failover":
        @wrap_model_call
        def model_router_middleware(request: Any, handler: Callable[[Any], Any]) -> Any:
            """Failover strategy: try primary, fall back on error."""
            # Try each model in order until one succeeds
            last_error: Exception | None = None
            for i in range(len(models)):
                model = models[i]
                # Swap the model on the request
                try:
                    setattr(request, "model", model)
                except Exception:
                    # If we can't set attribute, try dict-style
                    if isinstance(request, dict):
                        request["model"] = model
                    else:
                        pass  # Best-effort; primary model may already be set

                try:
                    result = handler(request)
                    if i > 0:
                        logger.info(
                            "Model router failover: succeeded on model #%d after %d failure(s)",
                            i,
                            i,
                            extra={
                                "event": "model_router.failover_success",
                                "attempt": i,
                                "previous_failures": i,
                            },
                        )
                    return result
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "Model router failover: model #%d failed, trying next: %s",
                        i,
                        exc,
                        extra={
                            "event": "model_router.failover_attempt",
                            "attempt": i,
                            "error": str(exc),
                        },
                    )

            # All models failed
            raise RuntimeExecutionError(
                f"All {len(models)} models failed in failover. Last error: {last_error}",
                code=ErrorCode.RT_INVOKE_FAILED,
                detail={
                    "strategy": "failover",
                    "model_count": len(models),
                    "last_error": str(last_error) if last_error else None,
                },
            ) from last_error
    else:
        @wrap_model_call
        def model_router_middleware(request: Any, handler: Callable[[Any], Any]) -> Any:
            """Round-robin / weighted / random strategy: select one model per call."""
            selected = router.select()

            # Swap the model on the request
            try:
                setattr(request, "model", selected)
            except Exception:
                if isinstance(request, dict):
                    request["model"] = selected

            logger.debug(
                "Model router: selected model for call (strategy=%s)",
                strategy,
                extra={
                    "event": "model_router.select",
                    "strategy": strategy,
                },
            )

            return handler(request)

    # Attach router for external inspection
    model_router_middleware.router = router  # type: ignore[attr-defined]
    return model_router_middleware
