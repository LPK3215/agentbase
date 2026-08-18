"""Tests for the A/B testing experiment framework.

Covers:
- InMemoryExperimentProvider: create, assign, record, stats, delete
- Assignment strategies: round_robin, weighted, random, failover(disabled)
- NullExperimentProvider: no-op behavior
- ExperimentManager: enabled/disabled, delegation
- Registry: register, create, has, names, unregister
"""
from __future__ import annotations

import threading

import pytest

from agentbase.core.experiment import (
    Assignment,
    Experiment,
    ExperimentManager,
    ExperimentRegistry,
    ExperimentResult,
    InMemoryExperimentProvider,
    NullExperimentProvider,
    Variant,
    experiment_registry,
    register_experiment_provider,
)

# ---------------------------------------------------------------------------
# Data model tests
# ---------------------------------------------------------------------------


class TestVariant:
    def test_to_dict(self):
        v = Variant(name="control", weight=3, model_override={"name": "gpt-4.1"})
        d = v.to_dict()
        assert d["name"] == "control"
        assert d["weight"] == 3
        assert d["model_override"] == {"name": "gpt-4.1"}

    def test_defaults(self):
        v = Variant(name="test")
        assert v.weight == 1
        assert v.model_override is None
        assert v.system_prompt_override is None
        assert v.metadata == {}


class TestExperiment:
    def test_to_dict(self):
        exp = Experiment(
            name="test_exp",
            description="A test",
            variants=[Variant(name="a"), Variant(name="b")],
            strategy="random",
        )
        d = exp.to_dict()
        assert d["name"] == "test_exp"
        assert d["description"] == "A test"
        assert len(d["variants"]) == 2
        assert d["strategy"] == "random"
        assert d["enabled"] is True

    def test_defaults(self):
        exp = Experiment(name="minimal")
        assert exp.description == ""
        assert exp.variants == []
        assert exp.strategy == "round_robin"
        assert exp.enabled is True
        assert exp.created_at != ""


class TestExperimentResult:
    def test_to_dict_truncates_output(self):
        r = ExperimentResult(
            experiment_name="exp",
            variant_name="v1",
            output_text="x" * 1000,
        )
        d = r.to_dict()
        assert len(d["output_text"]) == 500

    def test_to_dict_empty_output(self):
        r = ExperimentResult(experiment_name="exp", variant_name="v1")
        d = r.to_dict()
        assert d["output_text"] == ""


# ---------------------------------------------------------------------------
# InMemoryExperimentProvider tests
# ---------------------------------------------------------------------------


class TestInMemoryCreate:
    def test_create_and_get(self):
        provider = InMemoryExperimentProvider()
        exp = Experiment(
            name="test",
            variants=[Variant(name="a"), Variant(name="b")],
        )
        provider.create_experiment(exp)
        assert provider.get_experiment("test") is not None
        assert provider.get_experiment("nonexistent") is None

    def test_duplicate_name_raises(self):
        provider = InMemoryExperimentProvider()
        exp = Experiment(name="dup", variants=[Variant(name="a")])
        provider.create_experiment(exp)
        with pytest.raises(Exception, match="already exists"):
            provider.create_experiment(exp)

    def test_no_variants_raises(self):
        provider = InMemoryExperimentProvider()
        exp = Experiment(name="empty", variants=[])
        with pytest.raises(Exception, match="at least one variant"):
            provider.create_experiment(exp)

    def test_list_experiments(self):
        provider = InMemoryExperimentProvider()
        provider.create_experiment(Experiment(name="a", variants=[Variant(name="v")]))
        provider.create_experiment(Experiment(name="b", variants=[Variant(name="v")]))
        exps = provider.list_experiments()
        assert len(exps) == 2


class TestInMemoryAssign:
    def test_round_robin_assignment(self):
        provider = InMemoryExperimentProvider()
        provider.create_experiment(Experiment(
            name="rr",
            variants=[Variant(name="a"), Variant(name="b"), Variant(name="c")],
            strategy="round_robin",
        ))
        a1 = provider.assign("rr")
        a2 = provider.assign("rr")
        a3 = provider.assign("rr")
        a4 = provider.assign("rr")
        assert a1.variant_name == "a"
        assert a2.variant_name == "b"
        assert a3.variant_name == "c"
        assert a4.variant_name == "a"  # wraps around

    def test_random_assignment(self):
        provider = InMemoryExperimentProvider()
        provider.create_experiment(Experiment(
            name="rnd",
            variants=[Variant(name="a"), Variant(name="b"), Variant(name="c")],
            strategy="random",
        ))
        for _ in range(20):
            a = provider.assign("rnd")
            assert a.variant_name in ("a", "b", "c")

    def test_weighted_assignment(self):
        provider = InMemoryExperimentProvider()
        provider.create_experiment(Experiment(
            name="w",
            variants=[Variant(name="heavy", weight=99), Variant(name="light", weight=1)],
            strategy="weighted",
        ))
        counts = {"heavy": 0, "light": 0}
        for _ in range(200):
            a = provider.assign("w")
            counts[a.variant_name] += 1
        assert counts["heavy"] > 150

    def test_disabled_experiment_returns_first_variant(self):
        provider = InMemoryExperimentProvider()
        provider.create_experiment(Experiment(
            name="disabled",
            variants=[Variant(name="a"), Variant(name="b")],
            enabled=False,
        ))
        a = provider.assign("disabled")
        assert a.variant_name == "a"
        assert a.reason == "experiment_disabled"

    def test_not_found_raises(self):
        provider = InMemoryExperimentProvider()
        with pytest.raises(Exception, match="not found"):
            provider.assign("nonexistent")

    def test_unknown_strategy_falls_back_to_round_robin(self):
        provider = InMemoryExperimentProvider()
        provider.create_experiment(Experiment(
            name="unknown",
            variants=[Variant(name="a"), Variant(name="b")],
            strategy="invalid_strategy",
        ))
        a = provider.assign("unknown")
        assert a.variant_name == "a"
        assert a.reason == "round_robin_fallback"

    def test_assignment_has_request_id(self):
        provider = InMemoryExperimentProvider()
        provider.create_experiment(Experiment(
            name="rid",
            variants=[Variant(name="a")],
        ))
        a = provider.assign("rid", request_id="req-123")
        assert a.request_id == "req-123"

    def test_thread_safety(self):
        provider = InMemoryExperimentProvider()
        provider.create_experiment(Experiment(
            name="ts",
            variants=[Variant(name="a"), Variant(name="b")],
            strategy="round_robin",
        ))
        results: list[str] = []

        def worker():
            for _ in range(50):
                a = provider.assign("ts")
                results.append(a.variant_name)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 200
        assert all(r in ("a", "b") for r in results)


class TestInMemoryRecordResult:
    def test_record_and_id(self):
        provider = InMemoryExperimentProvider()
        provider.create_experiment(Experiment(
            name="r",
            variants=[Variant(name="a")],
        ))
        result = ExperimentResult(experiment_name="r", variant_name="a", success=True)
        recorded = provider.record_result(result)
        assert recorded.id is not None
        assert recorded.id == 1

        result2 = ExperimentResult(experiment_name="r", variant_name="a", success=False)
        recorded2 = provider.record_result(result2)
        assert recorded2.id == 2


class TestInMemoryStats:
    def test_stats_empty_experiment(self):
        provider = InMemoryExperimentProvider()
        stats = provider.get_stats("nonexistent")
        assert stats.experiment_name == "nonexistent"
        assert stats.total_results == 0

    def test_stats_with_results(self):
        provider = InMemoryExperimentProvider()
        provider.create_experiment(Experiment(
            name="s",
            variants=[Variant(name="a"), Variant(name="b")],
        ))
        # Record some results
        provider.record_result(ExperimentResult(
            experiment_name="s", variant_name="a", success=True, duration_ms=100.0
        ))
        provider.record_result(ExperimentResult(
            experiment_name="s", variant_name="a", success=True, duration_ms=200.0
        ))
        provider.record_result(ExperimentResult(
            experiment_name="s", variant_name="a", success=False, duration_ms=50.0
        ))
        provider.record_result(ExperimentResult(
            experiment_name="s", variant_name="b", success=True, duration_ms=300.0
        ))

        stats = provider.get_stats("s")
        assert stats.experiment_name == "s"
        assert stats.total_results == 4

        # Find variant stats
        a_stats = next(v for v in stats.variant_stats if v.variant_name == "a")
        assert a_stats.total == 3
        assert a_stats.successes == 2
        assert a_stats.failures == 1
        assert a_stats.success_rate == 2 / 3
        assert a_stats.avg_duration_ms == 350.0 / 3
        assert a_stats.min_duration_ms == 50.0
        assert a_stats.max_duration_ms == 200.0

        b_stats = next(v for v in stats.variant_stats if v.variant_name == "b")
        assert b_stats.total == 1
        assert b_stats.successes == 1

    def test_stats_no_results(self):
        provider = InMemoryExperimentProvider()
        provider.create_experiment(Experiment(
            name="nr",
            variants=[Variant(name="a")],
        ))
        stats = provider.get_stats("nr")
        assert stats.total_results == 0
        assert len(stats.variant_stats) == 1
        assert stats.variant_stats[0].total == 0


class TestInMemoryDelete:
    def test_delete_experiment(self):
        provider = InMemoryExperimentProvider()
        provider.create_experiment(Experiment(
            name="del",
            variants=[Variant(name="a")],
        ))
        provider.record_result(ExperimentResult(
            experiment_name="del", variant_name="a"
        ))
        assert provider.delete_experiment("del") is True
        assert provider.get_experiment("del") is None
        # Results should be gone too
        stats = provider.get_stats("del")
        assert stats.total_results == 0

    def test_delete_nonexistent(self):
        provider = InMemoryExperimentProvider()
        assert provider.delete_experiment("nonexistent") is False


# ---------------------------------------------------------------------------
# NullExperimentProvider tests
# ---------------------------------------------------------------------------


class TestNullProvider:
    def test_all_noops(self):
        provider = NullExperimentProvider()
        assert provider.get_experiment("x") is None
        assert provider.list_experiments() == []
        assert provider.delete_experiment("x") is False

        a = provider.assign("x")
        assert a.variant_name == "default"
        assert a.reason == "disabled"

        r = provider.record_result(ExperimentResult(
            experiment_name="x", variant_name="v"
        ))
        assert r is not None

        stats = provider.get_stats("x")
        assert stats.total_results == 0


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestExperimentRegistry:
    def test_register_and_create(self):
        registry = ExperimentRegistry()
        registry.register("test_provider", InMemoryExperimentProvider)
        provider = registry.create("test_provider")
        assert isinstance(provider, InMemoryExperimentProvider)

    def test_duplicate_register_raises(self):
        registry = ExperimentRegistry()
        registry.register("dup", InMemoryExperimentProvider)
        with pytest.raises(Exception, match="already registered"):
            registry.register("dup", InMemoryExperimentProvider)

    def test_override(self):
        registry = ExperimentRegistry()
        registry.register("over", InMemoryExperimentProvider)
        registry.register("over", InMemoryExperimentProvider, override=True)

    def test_unknown_provider(self):
        registry = ExperimentRegistry()
        with pytest.raises(Exception, match="Unknown"):
            registry.create("nonexistent")

    def test_has_and_names(self):
        registry = ExperimentRegistry()
        registry.register("a", InMemoryExperimentProvider)
        registry.register("b", NullExperimentProvider)
        assert registry.has("a") is True
        assert registry.has("c") is False
        assert "a" in registry.names()
        assert "b" in registry.names()

    def test_unregister(self):
        registry = ExperimentRegistry()
        registry.register("tmp", InMemoryExperimentProvider)
        assert registry.unregister("tmp") is True
        assert registry.has("tmp") is False
        assert registry.unregister("tmp") is False

    def test_global_registry_has_defaults(self):
        assert experiment_registry.has("null")
        assert experiment_registry.has("memory")


class TestRegisterDecorator:
    def test_decorator_registers(self):
        @register_experiment_provider("custom_test", override=True)
        class CustomProvider:
            def create_experiment(self, exp):
                return exp

            def get_experiment(self, name):
                return None

            def list_experiments(self):
                return []

            def assign(self, name, request_id=None):
                return Assignment(experiment_name=name, variant_name="x")

            def record_result(self, result):
                return result

            def get_stats(self, name):
                from agentbase.core.experiment import ExperimentStats
                return ExperimentStats(experiment_name=name)

            def delete_experiment(self, name):
                return False

            def close(self):
                pass

        assert experiment_registry.has("custom_test")
        provider = experiment_registry.create("custom_test")
        assert isinstance(provider, CustomProvider)


# ---------------------------------------------------------------------------
# ExperimentManager tests
# ---------------------------------------------------------------------------


class TestExperimentManager:
    def test_disabled_uses_null(self):
        manager = ExperimentManager(enabled=False)
        assert manager.enabled is False
        assert manager.list_experiments() == []
        a = manager.assign("test")
        assert a.variant_name == "default"

    def test_enabled_uses_provider(self):
        manager = ExperimentManager(provider="memory", enabled=True)
        assert manager.enabled is True
        manager.create_experiment(
            name="mgr_test",
            variants=[Variant(name="a"), Variant(name="b")],
        )
        exps = manager.list_experiments()
        assert len(exps) == 1
        assert exps[0].name == "mgr_test"

    def test_record_and_stats(self):
        manager = ExperimentManager(provider="memory", enabled=True)
        manager.create_experiment(
            name="mgr_stats",
            variants=[Variant(name="a")],
        )
        manager.record_result(
            experiment_name="mgr_stats",
            variant_name="a",
            success=True,
            duration_ms=100.0,
        )
        stats = manager.get_stats("mgr_stats")
        assert stats.total_results == 1
        assert stats.variant_stats[0].successes == 1

    def test_delete(self):
        manager = ExperimentManager(provider="memory", enabled=True)
        manager.create_experiment(
            name="mgr_del",
            variants=[Variant(name="a")],
        )
        assert manager.delete_experiment("mgr_del") is True
        assert manager.get_experiment("mgr_del") is None

    def test_get_experiment(self):
        manager = ExperimentManager(provider="memory", enabled=True)
        manager.create_experiment(
            name="mgr_get",
            description="test desc",
            variants=[Variant(name="a")],
        )
        exp = manager.get_experiment("mgr_get")
        assert exp is not None
        assert exp.description == "test desc"
        assert manager.get_experiment("nonexistent") is None


# ---------------------------------------------------------------------------
# Factory integration test
# ---------------------------------------------------------------------------


class TestFactoryExperimentManager:
    def test_factory_creates_experiment_manager(self):
        from agentbase.config.schema import AppConfig, ExperimentConfig
        from agentbase.factories.agent_factory import AgentFactory

        config = AppConfig()
        config.experiment = ExperimentConfig(enabled=True, provider="memory")
        factory = AgentFactory(app_config=config, root_dir=None)  # type: ignore[arg-type]
        manager = factory.experiment_manager
        assert manager.enabled is True

    def test_factory_disabled_by_default(self):
        from agentbase.config.schema import AppConfig
        from agentbase.factories.agent_factory import AgentFactory

        config = AppConfig()
        factory = AgentFactory(app_config=config, root_dir=None)  # type: ignore[arg-type]
        manager = factory.experiment_manager
        assert manager.enabled is False
