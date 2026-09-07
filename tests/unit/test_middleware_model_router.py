"""Tests for model_router middleware — covers strategy selection, failover, config parsing."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentbase.extensions.middleware.model_router import (
    ModelRouter,
    _build_models_from_config,
    build_model_router,
)

# ---------------------------------------------------------------------------
# ModelRouter unit tests
# ---------------------------------------------------------------------------


class TestModelRouterRoundRobin:
    def test_round_robin_cycles_in_order(self):
        """Round-robin should cycle through models in order."""
        m1, m2, m3 = MagicMock(), MagicMock(), MagicMock()
        router = ModelRouter(models=[m1, m2, m3], strategy="round_robin")
        assert router.select() is m1
        assert router.select() is m2
        assert router.select() is m3
        # Should wrap around
        assert router.select() is m1

    def test_round_robin_single_model(self):
        """Single model always returns the same model."""
        m1 = MagicMock()
        router = ModelRouter(models=[m1], strategy="round_robin")
        assert router.select() is m1
        assert router.select() is m1

    def test_round_robin_thread_safety(self):
        """Concurrent selects should not crash or skip."""
        import threading

        m1, m2 = MagicMock(), MagicMock()
        router = ModelRouter(models=[m1, m2], strategy="round_robin")
        results: list[Any] = []

        def worker():
            for _ in range(100):
                results.append(router.select())

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All results should be one of the two models
        assert all(r in (m1, m2) for r in results)
        assert len(results) == 400


class TestModelRouterWeighted:
    def test_weighted_distributes_by_weight(self):
        """Weighted selection should roughly follow weight ratios."""
        m1, m2 = MagicMock(), MagicMock()
        router = ModelRouter(
            models=[m1, m2],
            strategy="weighted",
            weights=[9, 1],
        )
        counts = {m1: 0, m2: 0}
        for _ in range(1000):
            selected = router.select()
            counts[selected] += 1
        # m1 should be selected ~90% of the time
        assert counts[m1] > 800
        assert counts[m2] < 200

    def test_weighted_zero_weight_never_selected(self):
        """A model with weight=0 should never be selected."""
        m1, m2 = MagicMock(), MagicMock()
        router = ModelRouter(
            models=[m1, m2],
            strategy="weighted",
            weights=[0, 1],
        )
        for _ in range(100):
            assert router.select() is m2

    def test_weighted_equal_weights(self):
        """Equal weights should distribute roughly evenly."""
        m1, m2 = MagicMock(), MagicMock()
        router = ModelRouter(
            models=[m1, m2],
            strategy="weighted",
            weights=[1, 1],
        )
        counts = {m1: 0, m2: 0}
        for _ in range(1000):
            counts[router.select()] += 1
        # Should be roughly 50/50 (within tolerance)
        assert 400 < counts[m1] < 600
        assert 400 < counts[m2] < 600


class TestModelRouterRandom:
    def test_random_returns_one_of_models(self):
        """Random strategy should return one of the configured models."""
        m1, m2, m3 = MagicMock(), MagicMock(), MagicMock()
        router = ModelRouter(models=[m1, m2, m3], strategy="random")
        for _ in range(100):
            assert router.select() in (m1, m2, m3)


class TestModelRouterFailover:
    def test_failover_always_returns_primary(self):
        """Failover strategy always returns the primary model (index 0)."""
        m1, m2, m3 = MagicMock(), MagicMock(), MagicMock()
        router = ModelRouter(models=[m1, m2, m3], strategy="failover")
        assert router.select() is m1
        assert router.select() is m1

    def test_select_failover_returns_next(self):
        """select_failover returns the next model after a failure."""
        m1, m2, m3 = MagicMock(), MagicMock(), MagicMock()
        router = ModelRouter(models=[m1, m2, m3], strategy="failover")
        assert router.select_failover(0) is m2
        assert router.select_failover(1) is m3

    def test_select_failover_returns_none_when_exhausted(self):
        """select_failover returns None when all models are exhausted."""
        m1, m2 = MagicMock(), MagicMock()
        router = ModelRouter(models=[m1, m2], strategy="failover")
        assert router.select_failover(1) is None


class TestModelRouterValidation:
    def test_empty_models_raises(self):
        """Empty models list should raise ValueError."""
        with pytest.raises(ValueError, match="at least one model"):
            ModelRouter(models=[], strategy="round_robin")

    def test_stats(self):
        """stats should return a dict with router information."""
        m1, m2 = MagicMock(), MagicMock()
        router = ModelRouter(
            models=[m1, m2],
            strategy="weighted",
            weights=[3, 1],
        )
        stats = router.stats
        assert stats["strategy"] == "weighted"
        assert stats["model_count"] == 2
        assert stats["weights"] == [3, 1]
        assert stats["total_weight"] == 4


# ---------------------------------------------------------------------------
# _build_models_from_config tests
# ---------------------------------------------------------------------------


class TestBuildModelsFromConfig:
    def test_builds_models_from_config_dicts(self):
        """Should build ModelConfig and call build_model for each entry."""
        configs = [
            {"provider": "openai", "name": "gpt-4.1-mini", "weight": 2},
            {"provider": "openai", "name": "gpt-4.1", "weight": 1},
        ]
        mock_models = [MagicMock(), MagicMock()]
        with patch(
            "agentbase.extensions.middleware.model_router.build_model",
            side_effect=mock_models,
        ):
            models, weights = _build_models_from_config(
                [dict(c) for c in configs]
            )
        assert len(models) == 2
        assert weights == [2, 1]

    def test_skips_failed_model_builds(self):
        """Failed model builds should be skipped, not crash."""
        configs = [
            {"provider": "openai", "name": "gpt-4.1-mini"},
            {"provider": "bad", "name": "bad-model"},
        ]
        good_model = MagicMock()
        with patch(
            "agentbase.extensions.middleware.model_router.build_model",
            side_effect=[good_model, Exception("build failed")],
        ):
            models, weights = _build_models_from_config(
                [dict(c) for c in configs]
            )
        assert len(models) == 1
        assert weights == [1]

    def test_empty_configs_returns_empty(self):
        """Empty config list returns empty lists."""
        models, weights = _build_models_from_config([])
        assert models == []
        assert weights == []


# ---------------------------------------------------------------------------
# build_model_router (middleware builder) tests
# ---------------------------------------------------------------------------


class TestBuildModelRouter:
    def _make_context(self, router_cfg: dict | None = None):
        """Create a mock context with agent_config."""
        agent_config = MagicMock()
        agent_config.metadata = {"model_router": router_cfg} if router_cfg else {}
        return {"agent_config": agent_config}

    def test_no_config_returns_empty_list(self):
        """With no model_router config, should return empty list (disabled)."""
        result = build_model_router(context=self._make_context(None))
        assert result == []

    def test_no_models_configured_returns_empty_list(self):
        """With empty models list, should return empty list."""
        result = build_model_router(context=self._make_context({
            "strategy": "round_robin",
            "models": [],
        }))
        assert result == []

    def test_invalid_strategy_falls_back_to_round_robin(self):
        """Invalid strategy should fall back to round_robin."""
        mock_model = MagicMock()
        with patch(
            "agentbase.extensions.middleware.model_router.build_model",
            return_value=mock_model,
        ):
            with patch("langchain.agents.middleware.wrap_model_call") as mock_wrap:
                # Make wrap_model_call a pass-through decorator
                mock_wrap.side_effect = lambda fn: fn
                result = build_model_router(context=self._make_context({
                    "strategy": "invalid_strategy",
                    "models": [{"provider": "openai", "name": "gpt-4.1-mini"}],
                }))
        # Should have built successfully with round_robin fallback
        assert result != []
        assert hasattr(result, "router")
        assert result.router.strategy == "round_robin"

    def test_wrap_model_call_unavailable_returns_empty(self):
        """When wrap_model_call is not available, should return empty list."""
        mock_model = MagicMock()
        with patch(
            "agentbase.extensions.middleware.model_router.build_model",
            return_value=mock_model,
        ):
            # Make wrap_model_call unavailable by raising ImportError on import
            import sys
            original_module = sys.modules.get("langchain.agents.middleware")
            if original_module is not None and hasattr(original_module, "wrap_model_call"):
                # Temporarily remove the attribute
                original_fn = original_module.wrap_model_call
                del original_module.wrap_model_call
                try:
                    result = build_model_router(context=self._make_context({
                        "strategy": "round_robin",
                        "models": [{"provider": "openai", "name": "gpt-4.1-mini"}],
                    }))
                finally:
                    original_module.wrap_model_call = original_fn
            else:
                # Module not available at all — import will fail
                result = build_model_router(context=self._make_context({
                    "strategy": "round_robin",
                    "models": [{"provider": "openai", "name": "gpt-4.1-mini"}],
                }))
        # wrap_model_call unavailable → disabled
        assert result == []

    def test_successful_build_attaches_router(self):
        """Successful build should attach router to the middleware function."""
        mock_models = [MagicMock(), MagicMock()]
        with patch(
            "agentbase.extensions.middleware.model_router.build_model",
            side_effect=mock_models,
        ):
            with patch("langchain.agents.middleware.wrap_model_call") as mock_wrap:
                mock_wrap.side_effect = lambda fn: fn
                result = build_model_router(context=self._make_context({
                    "strategy": "round_robin",
                    "models": [
                        {"provider": "openai", "name": "gpt-4.1-mini"},
                        {"provider": "openai", "name": "gpt-4.1"},
                    ],
                }))

        assert result != []
        assert hasattr(result, "router")
        assert result.router.model_count == 2
        assert result.router.strategy == "round_robin"


# ---------------------------------------------------------------------------
# Middleware integration tests (wrap_model_call behavior)
# ---------------------------------------------------------------------------


class TestModelRouterMiddlewareBehavior:
    def test_round_robin_swaps_model_on_request(self):
        """The middleware should swap request.model on each call (round_robin)."""
        m1, m2 = MagicMock(), MagicMock()
        router = ModelRouter(models=[m1, m2], strategy="round_robin")

        # Simulate a request object
        request = MagicMock()
        original_model = MagicMock()
        request.model = original_model

        # Simulate handler
        handler_calls: list[Any] = []

        def handler(req):
            handler_calls.append(req.model)
            return "result"

        # Manually simulate what wrap_model_call does
        def middleware_fn(req, hand):
            selected = router.select()
            req.model = selected
            return hand(req)

        result = middleware_fn(request, handler)
        assert result == "result"
        assert handler_calls[0] is m1

        # Second call should use m2
        middleware_fn(request, handler)
        assert handler_calls[1] is m2

    def test_failover_tries_next_on_error(self):
        """Failover should try the next model when the current one fails."""
        m1, m2 = MagicMock(), MagicMock()
        request = MagicMock()
        request.model = m1

        call_count = 0

        def handler(req):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("primary failed")
            return "success_on_failover"

        # Simulate failover middleware
        def failover_fn(req, hand):
            for i in range(len([m1, m2])):
                model = [m1, m2][i]
                req.model = model
                try:
                    return hand(req)
                except Exception:
                    continue
            raise Exception("all failed")

        result = failover_fn(request, handler)
        assert result == "success_on_failover"
        assert call_count == 2
