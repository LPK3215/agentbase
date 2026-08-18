"""Unit tests for model_router middleware — ModelRouter, _build_models_from_config,
build_model_router, and inner middleware functions.

Tests cover:
- ModelRouter: init, select (round_robin/weighted/random/failover), stats, select_failover
- _build_models_from_config: success, failure, weight extraction
- build_model_router: config, invalid strategy, no models, wrap_model_call unavailable
- Inner middleware: round_robin select + handler, failover success, failover all-fail
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from agentbase.extensions.middleware.model_router import (
    ModelRouter,
    _build_models_from_config,
    build_model_router,
)


def _passthrough_decorator(func):
    """A stand-in for wrap_model_call that returns the function unchanged."""
    return func


# ---------------------------------------------------------------------------
# ModelRouter
# ---------------------------------------------------------------------------


class TestModelRouter:
    def test_init_requires_models(self):
        with pytest.raises(ValueError, match="at least one model"):
            ModelRouter(models=[], strategy="round_robin")

    def test_init_defaults(self):
        router = ModelRouter(models=["m1", "m2"])
        assert router.strategy == "round_robin"
        assert router.model_count == 2

    def test_stats(self):
        router = ModelRouter(models=["m1", "m2"], strategy="weighted", weights=[3, 1])
        stats = router.stats
        assert stats["strategy"] == "weighted"
        assert stats["model_count"] == 2
        assert stats["weights"] == [3, 1]
        assert stats["total_weight"] == 4

    def test_select_round_robin(self):
        router = ModelRouter(models=["m1", "m2", "m3"], strategy="round_robin")
        results = [router.select() for _ in range(6)]
        assert results == ["m1", "m2", "m3", "m1", "m2", "m3"]

    def test_select_weighted(self):
        router = ModelRouter(models=["m1", "m2"], strategy="weighted", weights=[3, 1])
        # Run many times — m1 should appear more often than m2
        results = [router.select() for _ in range(100)]
        assert results.count("m1") > results.count("m2")

    def test_select_random(self):
        router = ModelRouter(models=["m1", "m2", "m3"], strategy="random")
        result = router.select()
        assert result in ["m1", "m2", "m3"]

    def test_select_failover_returns_primary(self):
        router = ModelRouter(models=["m1", "m2"], strategy="failover")
        assert router.select() == "m1"

    def test_select_failover_next(self):
        router = ModelRouter(models=["m1", "m2", "m3"], strategy="failover")
        assert router.select_failover(0) == "m2"
        assert router.select_failover(1) == "m3"

    def test_select_failover_exhausted(self):
        router = ModelRouter(models=["m1"], strategy="failover")
        assert router.select_failover(0) is None

    def test_negative_weight_treated_as_zero(self):
        router = ModelRouter(models=["m1", "m2"], strategy="weighted", weights=[-1, 1])
        assert router.stats["total_weight"] == 1


# ---------------------------------------------------------------------------
# _build_models_from_config
# ---------------------------------------------------------------------------


class TestBuildModelsFromConfig:
    def test_success(self):
        with patch("agentbase.extensions.middleware.model_router.build_model") as mock_build:
            mock_build.return_value = MagicMock()
            models, weights = _build_models_from_config([
                {"provider": "openai", "name": "gpt-4o", "weight": 3},
                {"provider": "deepseek", "name": "deepseek-chat", "weight": 1},
            ])
            assert len(models) == 2
            assert weights == [3, 1]

    def test_default_weight_1(self):
        with patch("agentbase.extensions.middleware.model_router.build_model") as mock_build:
            mock_build.return_value = MagicMock()
            _, weights = _build_models_from_config([
                {"provider": "openai", "name": "gpt-4o"},
            ])
            assert weights == [1]

    def test_build_failure_skipped(self):
        with patch("agentbase.extensions.middleware.model_router.build_model") as mock_build:
            mock_build.side_effect = RuntimeError("build failed")
            models, weights = _build_models_from_config([
                {"provider": "openai", "name": "broken"},
            ])
            assert models == []
            assert weights == []

    def test_partial_failure(self):
        with patch("agentbase.extensions.middleware.model_router.build_model") as mock_build:
            mock_build.side_effect = [MagicMock(), RuntimeError("fail"), MagicMock()]
            models, weights = _build_models_from_config([
                {"provider": "openai", "name": "m1"},
                {"provider": "openai", "name": "m2"},
                {"provider": "openai", "name": "m3"},
            ])
            assert len(models) == 2
            assert weights == [1, 1]


# ---------------------------------------------------------------------------
# build_model_router
# ---------------------------------------------------------------------------


class TestBuildModelRouter:
    def test_returns_empty_list_no_config(self):
        with patch("langchain.agents.middleware.wrap_model_call", _passthrough_decorator):
            result = build_model_router(None)
            assert result == []

    def test_returns_empty_list_no_models(self):
        agent_config = MagicMock()
        agent_config.metadata.get.return_value = {"strategy": "round_robin", "models": []}
        with patch("langchain.agents.middleware.wrap_model_call", _passthrough_decorator):
            result = build_model_router({"agent_config": agent_config})
            assert result == []

    def test_invalid_strategy_falls_back(self):
        agent_config = MagicMock()
        agent_config.metadata.get.return_value = {
            "strategy": "invalid_strategy",
            "models": [{"provider": "openai", "name": "gpt-4o"}],
        }
        with patch("agentbase.extensions.middleware.model_router.build_model") as mock_build:
            mock_build.return_value = MagicMock()
            with patch("langchain.agents.middleware.wrap_model_call", _passthrough_decorator):
                mw = build_model_router({"agent_config": agent_config})
                assert callable(mw)
                assert mw.router.strategy == "round_robin"

    def test_no_models_built_returns_empty(self):
        agent_config = MagicMock()
        agent_config.metadata.get.return_value = {
            "strategy": "round_robin",
            "models": [{"provider": "openai", "name": "gpt-4o"}],
        }
        with patch("agentbase.extensions.middleware.model_router.build_model") as mock_build:
            mock_build.side_effect = RuntimeError("build failed")
            with patch("langchain.agents.middleware.wrap_model_call", _passthrough_decorator):
                result = build_model_router({"agent_config": agent_config})
                assert result == []

    def test_wrap_model_call_unavailable_returns_empty(self):
        agent_config = MagicMock()
        agent_config.metadata.get.return_value = {
            "strategy": "round_robin",
            "models": [{"provider": "openai", "name": "gpt-4o"}],
        }
        with patch("agentbase.extensions.middleware.model_router.build_model") as mock_build:
            mock_build.return_value = MagicMock()
            with patch.dict(sys.modules, {"langchain.agents.middleware": None}):
                result = build_model_router({"agent_config": agent_config})
                assert result == []


class TestModelRouterMiddleware:
    def _build_mw(self, strategy="round_robin", models=None):
        if models is None:
            models = [{"provider": "openai", "name": f"m{i}"} for i in range(3)]
        agent_config = MagicMock()
        agent_config.metadata.get.return_value = {
            "strategy": strategy,
            "models": models,
        }
        with patch("agentbase.extensions.middleware.model_router.build_model") as mock_build:
            mock_build.side_effect = lambda cfg: MagicMock(name=cfg.name)
            with patch("langchain.agents.middleware.wrap_model_call", _passthrough_decorator):
                return build_model_router({"agent_config": agent_config})

    def test_round_robin_select_and_handler(self):
        mw = self._build_mw(strategy="round_robin")
        request = MagicMock()
        handler = MagicMock(return_value="ok")
        result = mw(request, handler)
        assert result == "ok"

    def test_failover_success_on_first(self):
        mw = self._build_mw(strategy="failover")
        request = MagicMock()
        handler = MagicMock(return_value="ok")
        result = mw(request, handler)
        assert result == "ok"

    def test_failover_success_on_second(self):
        mw = self._build_mw(strategy="failover")
        request = MagicMock()
        handler = MagicMock(side_effect=[RuntimeError("m0 fail"), "ok"])
        result = mw(request, handler)
        assert result == "ok"

    def test_failover_all_fail_raises(self):
        from agentbase.runtime.errors import RuntimeExecutionError
        mw = self._build_mw(strategy="failover", models=[{"provider": "openai", "name": "m0"}])
        request = MagicMock()
        handler = MagicMock(side_effect=RuntimeError("all fail"))
        with pytest.raises(RuntimeExecutionError, match="All.*models failed"):
            mw(request, handler)

    def test_round_robin_dict_request(self):
        mw = self._build_mw(strategy="round_robin")
        request = {"model": "old_model"}
        handler = MagicMock(return_value="ok")
        result = mw(request, handler)
        assert result == "ok"

    def test_failover_dict_request(self):
        mw = self._build_mw(strategy="failover")
        request = {"model": "old_model"}
        handler = MagicMock(side_effect=[RuntimeError("fail"), "ok"])
        result = mw(request, handler)
        assert result == "ok"
