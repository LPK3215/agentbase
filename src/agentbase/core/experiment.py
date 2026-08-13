"""A/B testing framework — compare agent strategies and measure outcomes.

Provides a pluggable experiment system that allows users to:
- Define experiments with multiple variants (each variant overrides
  agent config: model, system_prompt, temperature, etc.)
- Assign requests to variants using strategies (round_robin, random,
  weighted)
- Record outcomes (success/failure, duration, output text)
- Query statistics (win rate, average duration, sample count)

Pluggable storage:
- ``InMemoryExperimentProvider`` (default) — zero-config, in-process
- ``NullExperimentProvider`` — no-op, zero overhead when disabled
- Register custom providers with ``@register_experiment_provider("name")``

Usage::

    from agentbase.core.experiment import ExperimentManager, Experiment, Variant

    manager = ExperimentManager(provider="memory", enabled=True)

    exp = manager.create_experiment(
        name="model_comparison",
        description="Compare gpt-4.1-mini vs gpt-4.1",
        variants=[
            Variant(name="control", weight=1),
            Variant(name="treatment", weight=1, model_override={
                "provider": "openai", "name": "gpt-4.1",
            }),
        ],
    )

    assignment = manager.assign("model_comparison")
    # ... run agent with assignment.variant config overrides ...
    manager.record_result(
        experiment_name="model_comparison",
        variant_name=assignment.variant_name,
        success=True,
        duration_ms=1234.5,
        output_text="...",
    )

    stats = manager.get_stats("model_comparison")
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, runtime_checkable

from agentbase.runtime.errors import RegistryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Variant:
    """A single experiment variant — defines config overrides for an agent.

    Attributes:
        name: Unique variant name within the experiment.
        weight: Relative weight for weighted assignment (default 1).
        model_override: Agent model config overrides (provider, name, etc.).
        system_prompt_override: Alternative system prompt.
        metadata: Extra config overrides (temperature, max_tokens, etc.).
    """

    name: str
    weight: int = 1
    model_override: dict[str, Any] | None = None
    system_prompt_override: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "weight": self.weight,
            "model_override": self.model_override,
            "system_prompt_override": self.system_prompt_override,
            "metadata": self.metadata,
        }


@dataclass
class Experiment:
    """An A/B test experiment definition.

    Attributes:
        name: Unique experiment name.
        description: Human-readable description.
        variants: List of variants to compare.
        strategy: Assignment strategy — ``"round_robin"`` | ``"random"`` | ``"weighted"``.
        enabled: Whether the experiment is active.
        created_at: ISO 8601 UTC timestamp.
    """

    name: str
    description: str = ""
    variants: list[Variant] = field(default_factory=list)
    strategy: str = "round_robin"
    enabled: bool = True
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "variants": [v.to_dict() for v in self.variants],
            "strategy": self.strategy,
            "enabled": self.enabled,
            "created_at": self.created_at,
        }


@dataclass
class Assignment:
    """Result of assigning a request to a variant.

    Attributes:
        experiment_name: The experiment that assigned this variant.
        variant_name: The selected variant name.
        reason: Why this variant was selected (e.g. "round_robin", "random").
        request_id: Optional request ID for correlation.
        timestamp: ISO 8601 UTC timestamp.
    """

    experiment_name: str
    variant_name: str
    reason: str = ""
    request_id: str | None = None
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_name": self.experiment_name,
            "variant_name": self.variant_name,
            "reason": self.reason,
            "request_id": self.request_id,
            "timestamp": self.timestamp,
        }


@dataclass
class ExperimentResult:
    """A recorded outcome for a variant invocation.

    Attributes:
        experiment_name: The experiment name.
        variant_name: The variant that was tested.
        success: Whether the invocation succeeded.
        duration_ms: Time taken in milliseconds.
        output_text: The agent's output text (optional, may be truncated).
        error: Error message if the invocation failed.
        request_id: Optional request ID for correlation.
        timestamp: ISO 8601 UTC timestamp.
        id: Auto-assigned record ID.
    """

    experiment_name: str
    variant_name: str
    success: bool = True
    duration_ms: float = 0.0
    output_text: str = ""
    error: str = ""
    request_id: str | None = None
    timestamp: str = field(default_factory=_now)
    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "experiment_name": self.experiment_name,
            "variant_name": self.variant_name,
            "success": self.success,
            "duration_ms": self.duration_ms,
            "output_text": self.output_text[:500] if self.output_text else "",
            "error": self.error,
            "request_id": self.request_id,
            "timestamp": self.timestamp,
        }


@dataclass
class VariantStats:
    """Aggregate statistics for a single variant.

    Attributes:
        variant_name: The variant name.
        total: Total number of recorded results.
        successes: Number of successful invocations.
        failures: Number of failed invocations.
        success_rate: Success rate (0.0–1.0).
        avg_duration_ms: Average duration in milliseconds.
        min_duration_ms: Minimum duration.
        max_duration_ms: Maximum duration.
    """

    variant_name: str
    total: int = 0
    successes: int = 0
    failures: int = 0
    success_rate: float = 0.0
    avg_duration_ms: float = 0.0
    min_duration_ms: float = 0.0
    max_duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant_name": self.variant_name,
            "total": self.total,
            "successes": self.successes,
            "failures": self.failures,
            "success_rate": round(self.success_rate, 4),
            "avg_duration_ms": round(self.avg_duration_ms, 2),
            "min_duration_ms": round(self.min_duration_ms, 2),
            "max_duration_ms": round(self.max_duration_ms, 2),
        }


@dataclass
class ExperimentStats:
    """Aggregate statistics for an entire experiment.

    Attributes:
        experiment_name: The experiment name.
        variant_stats: Per-variant statistics.
        total_results: Total results across all variants.
        created_at: When the experiment was created.
    """

    experiment_name: str
    variant_stats: list[VariantStats] = field(default_factory=list)
    total_results: int = 0
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_name": self.experiment_name,
            "variant_stats": [v.to_dict() for v in self.variant_stats],
            "total_results": self.total_results,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ExperimentProvider(Protocol):
    """Protocol for experiment storage providers.

    Implementations must be thread-safe.
    """

    def create_experiment(self, experiment: Experiment) -> Experiment:
        """Create a new experiment. Returns the stored experiment."""
        ...

    def get_experiment(self, name: str) -> Experiment | None:
        """Get an experiment by name. Returns None if not found."""
        ...

    def list_experiments(self) -> list[Experiment]:
        """List all experiments."""
        ...

    def assign(self, experiment_name: str, request_id: str | None = None) -> Assignment:
        """Assign a request to a variant. Returns the assignment."""
        ...

    def record_result(self, result: ExperimentResult) -> ExperimentResult:
        """Record an experiment result. Returns the result with ID."""
        ...

    def get_stats(self, experiment_name: str) -> ExperimentStats:
        """Get aggregate statistics for an experiment."""
        ...

    def delete_experiment(self, name: str) -> bool:
        """Delete an experiment and its results. Returns True if deleted."""
        ...

    def close(self) -> None:
        """Release resources."""
        ...


# ---------------------------------------------------------------------------
# Null provider (no-op, zero overhead)
# ---------------------------------------------------------------------------


class NullExperimentProvider:
    """No-op experiment provider — all operations return empty/None.

    Used when experiments are disabled (``experiment.enabled=false``).
    """

    def create_experiment(self, experiment: Experiment) -> Experiment:
        return experiment

    def get_experiment(self, name: str) -> Experiment | None:
        return None

    def list_experiments(self) -> list[Experiment]:
        return []

    def assign(self, experiment_name: str, request_id: str | None = None) -> Assignment:
        return Assignment(
            experiment_name=experiment_name,
            variant_name="default",
            reason="disabled",
            request_id=request_id,
        )

    def record_result(self, result: ExperimentResult) -> ExperimentResult:
        return result

    def get_stats(self, experiment_name: str) -> ExperimentStats:
        return ExperimentStats(experiment_name=experiment_name)

    def delete_experiment(self, name: str) -> bool:
        return False

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# In-memory provider (default, zero-config)
# ---------------------------------------------------------------------------


class InMemoryExperimentProvider:
    """In-memory experiment provider — zero-config, in-process storage.

    All data is lost on process restart. Thread-safe via ``threading.RLock``.

    Suitable for development, testing, and single-process deployments.
    For persistent storage, register a custom provider (e.g. SQLite/Redis).
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._experiments: dict[str, Experiment] = {}
        self._results: list[ExperimentResult] = []
        self._next_id = 1
        self._round_robin_index: dict[str, int] = {}

    def create_experiment(self, experiment: Experiment) -> Experiment:
        with self._lock:
            if experiment.name in self._experiments:
                raise RegistryError(
                    f"Experiment already exists: {experiment.name}"
                )
            if not experiment.variants:
                raise RegistryError(
                    f"Experiment '{experiment.name}' must have at least one variant"
                )
            self._experiments[experiment.name] = experiment
            logger.info(
                "Experiment created: name=%s variants=%d strategy=%s",
                experiment.name,
                len(experiment.variants),
                experiment.strategy,
                extra={
                    "event": "experiment.created",
                    "experiment_name": experiment.name,
                    "variants": len(experiment.variants),
                    "strategy": experiment.strategy,
                },
            )
            return experiment

    def get_experiment(self, name: str) -> Experiment | None:
        with self._lock:
            return self._experiments.get(name)

    def list_experiments(self) -> list[Experiment]:
        with self._lock:
            return list(self._experiments.values())

    def assign(self, experiment_name: str, request_id: str | None = None) -> Assignment:
        import random

        with self._lock:
            exp = self._experiments.get(experiment_name)
            if exp is None:
                raise RegistryError(
                    f"Experiment not found: {experiment_name}"
                )
            if not exp.enabled:
                return Assignment(
                    experiment_name=experiment_name,
                    variant_name=exp.variants[0].name,
                    reason="experiment_disabled",
                    request_id=request_id,
                )

            variants = exp.variants
            strategy = exp.strategy

            if strategy == "round_robin":
                idx = self._round_robin_index.get(experiment_name, 0)
                selected = variants[idx % len(variants)]
                self._round_robin_index[experiment_name] = (idx + 1) % len(variants)
                reason = "round_robin"

            elif strategy == "weighted":
                total_weight = sum(max(0, v.weight) for v in variants) or 1
                target = random.randint(1, total_weight)
                cumulative = 0
                selected = variants[-1]
                for v in variants:
                    cumulative += max(0, v.weight)
                    if target <= cumulative:
                        selected = v
                        break
                reason = "weighted"

            elif strategy == "random":
                selected = random.choice(variants)
                reason = "random"

            else:
                logger.warning(
                    "Unknown strategy '%s', falling back to round_robin",
                    strategy,
                    extra={"event": "experiment.unknown_strategy", "strategy": strategy},
                )
                idx = self._round_robin_index.get(experiment_name, 0)
                selected = variants[idx % len(variants)]
                self._round_robin_index[experiment_name] = (idx + 1) % len(variants)
                reason = "round_robin_fallback"

            assignment = Assignment(
                experiment_name=experiment_name,
                variant_name=selected.name,
                reason=reason,
                request_id=request_id,
            )
            logger.debug(
                "Assignment: experiment=%s variant=%s reason=%s",
                experiment_name,
                selected.name,
                reason,
                extra={
                    "event": "experiment.assigned",
                    "experiment": experiment_name,
                    "variant": selected.name,
                    "reason": reason,
                },
            )
            return assignment

    def record_result(self, result: ExperimentResult) -> ExperimentResult:
        with self._lock:
            result.id = self._next_id
            self._next_id += 1
            self._results.append(result)
            logger.debug(
                "Result recorded: experiment=%s variant=%s success=%s id=%d",
                result.experiment_name,
                result.variant_name,
                result.success,
                result.id,
                extra={
                    "event": "experiment.result_recorded",
                    "experiment": result.experiment_name,
                    "variant": result.variant_name,
                    "success": result.success,
                    "result_id": result.id,
                },
            )
            return result

    def get_stats(self, experiment_name: str) -> ExperimentStats:
        with self._lock:
            exp = self._experiments.get(experiment_name)
            if exp is None:
                return ExperimentStats(experiment_name=experiment_name)

            # Collect results for this experiment
            exp_results = [
                r for r in self._results if r.experiment_name == experiment_name
            ]

            # Build per-variant stats
            variant_map: dict[str, list[ExperimentResult]] = {}
            for v in exp.variants:
                variant_map[v.name] = []
            for r in exp_results:
                if r.variant_name in variant_map:
                    variant_map[r.variant_name].append(r)

            variant_stats: list[VariantStats] = []
            for v in exp.variants:
                results = variant_map[v.name]
                total = len(results)
                if total == 0:
                    variant_stats.append(VariantStats(variant_name=v.name))
                    continue

                successes = sum(1 for r in results if r.success)
                failures = total - successes
                durations = [r.duration_ms for r in results if r.duration_ms > 0]
                avg_dur = sum(durations) / len(durations) if durations else 0.0
                min_dur = min(durations) if durations else 0.0
                max_dur = max(durations) if durations else 0.0

                variant_stats.append(VariantStats(
                    variant_name=v.name,
                    total=total,
                    successes=successes,
                    failures=failures,
                    success_rate=successes / total if total > 0 else 0.0,
                    avg_duration_ms=avg_dur,
                    min_duration_ms=min_dur,
                    max_duration_ms=max_dur,
                ))

            return ExperimentStats(
                experiment_name=experiment_name,
                variant_stats=variant_stats,
                total_results=len(exp_results),
                created_at=exp.created_at,
            )

    def delete_experiment(self, name: str) -> bool:
        with self._lock:
            if name not in self._experiments:
                return False
            del self._experiments[name]
            # Remove associated results
            self._results = [
                r for r in self._results if r.experiment_name != name
            ]
            self._round_robin_index.pop(name, None)
            logger.info(
                "Experiment deleted: name=%s",
                name,
                extra={"event": "experiment.deleted", "experiment_name": name},
            )
            return True

    def close(self) -> None:
        with self._lock:
            self._experiments.clear()
            self._results.clear()
            self._round_robin_index.clear()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ExperimentRegistry:
    """Thread-safe registry for experiment providers."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., ExperimentProvider]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        factory: Callable[..., ExperimentProvider],
        *,
        override: bool = False,
    ) -> None:
        key = name.strip().lower()
        with self._lock:
            if not key:
                raise RegistryError("Cannot register empty experiment provider name")
            if key in self._factories and not override:
                raise RegistryError(f"Experiment provider already registered: {key}")
            self._factories[key] = factory

    def create(self, name: str, **kwargs: Any) -> ExperimentProvider:
        key = name.strip().lower()
        with self._lock:
            if key not in self._factories:
                available = ", ".join(sorted(self._factories)) or "<empty>"
                raise RegistryError(
                    f"Unknown experiment provider: {key}. Available: {available}"
                )
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
experiment_registry = ExperimentRegistry()

# Register defaults
experiment_registry.register("null", NullExperimentProvider)
experiment_registry.register("memory", InMemoryExperimentProvider)


def register_experiment_provider(name: str, *, override: bool = False):
    """Decorator: register an experiment provider class.

    Usage::

        @register_experiment_provider("redis")
        class RedisExperimentProvider:
            def create_experiment(self, experiment: Experiment) -> Experiment: ...
    """

    def decorator(factory: Callable[..., ExperimentProvider]):
        experiment_registry.register(name, factory, override=override)
        return factory

    return decorator


# ---------------------------------------------------------------------------
# Manager — high-level facade
# ---------------------------------------------------------------------------


class ExperimentManager:
    """High-level experiment manager.

    Wraps an ``ExperimentProvider`` and provides convenience methods.
    When ``enabled=False``, uses ``NullExperimentProvider`` (no-op).

    Usage::

        manager = ExperimentManager(provider="memory", enabled=True)
        manager.create_experiment(
            name="model_comparison",
            variants=[Variant(name="control"), Variant(name="treatment")],
        )
        assignment = manager.assign("model_comparison")
    """

    def __init__(
        self,
        *,
        provider: str = "memory",
        enabled: bool = False,
        **provider_kwargs: Any,
    ) -> None:
        self._enabled = enabled
        if not enabled:
            self._provider: ExperimentProvider = NullExperimentProvider()
        else:
            self._provider = experiment_registry.create(provider, **provider_kwargs)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def create_experiment(
        self,
        *,
        name: str,
        description: str = "",
        variants: list[Variant] | None = None,
        strategy: str = "round_robin",
    ) -> Experiment:
        """Create a new experiment. No-op when disabled."""
        exp = Experiment(
            name=name,
            description=description,
            variants=variants or [],
            strategy=strategy,
        )
        return self._provider.create_experiment(exp)

    def get_experiment(self, name: str) -> Experiment | None:
        """Get an experiment by name."""
        return self._provider.get_experiment(name)

    def list_experiments(self) -> list[Experiment]:
        """List all experiments."""
        return self._provider.list_experiments()

    def assign(
        self, experiment_name: str, request_id: str | None = None
    ) -> Assignment:
        """Assign a request to a variant."""
        return self._provider.assign(experiment_name, request_id=request_id)

    def record_result(
        self,
        *,
        experiment_name: str,
        variant_name: str,
        success: bool = True,
        duration_ms: float = 0.0,
        output_text: str = "",
        error: str = "",
        request_id: str | None = None,
    ) -> ExperimentResult:
        """Record an experiment result. No-op when disabled."""
        result = ExperimentResult(
            experiment_name=experiment_name,
            variant_name=variant_name,
            success=success,
            duration_ms=duration_ms,
            output_text=output_text,
            error=error,
            request_id=request_id,
        )
        return self._provider.record_result(result)

    def get_stats(self, experiment_name: str) -> ExperimentStats:
        """Get aggregate statistics for an experiment."""
        return self._provider.get_stats(experiment_name)

    def delete_experiment(self, name: str) -> bool:
        """Delete an experiment and its results."""
        return self._provider.delete_experiment(name)

    def close(self) -> None:
        self._provider.close()
