"""Tests for API-layer RateLimiter per-role quota management.

Tests verify:
1. Default behavior (no role quotas) — backward compatible
2. Per-role quota setting and enforcement
3. Role-based bucket isolation
4. Dynamic quota updates
5. Stats reporting with role quotas
6. RateLimitConfig.quotas field and get_quota_for_role method
"""
from __future__ import annotations

from agentbase.api import RateLimiter


class TestRateLimiterDefaultBehavior:
    """Verify backward compatibility — no role quotas means global default."""

    def test_check_without_role_uses_default(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60, burst=0)
        for _ in range(5):
            assert limiter.check("1.2.3.4") is True
        assert limiter.check("1.2.3.4") is False

    def test_get_remaining_default(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60, burst=2)
        assert limiter.get_remaining("1.2.3.4") == 7
        limiter.check("1.2.3.4")
        assert limiter.get_remaining("1.2.3.4") == 6

    def test_reset_clears_buckets(self):
        limiter = RateLimiter(max_requests=2, window_seconds=60, burst=0)
        limiter.check("1.2.3.4")
        limiter.check("1.2.3.4")
        assert limiter.check("1.2.3.4") is False
        limiter.reset()
        assert limiter.check("1.2.3.4") is True


class TestRateLimiterRoleQuotas:
    """Test per-role quota functionality."""

    def test_set_role_quota(self):
        limiter = RateLimiter(max_requests=10, window_seconds=60, burst=0)
        limiter.set_role_quota("admin", max_requests=100, window_seconds=60, burst=0)

        quota = limiter.get_role_quota("admin")
        assert quota == (100, 60, 0)

    def test_get_role_quota_falls_back_to_default(self):
        limiter = RateLimiter(max_requests=10, window_seconds=60, burst=5)

        # Unknown role falls back to default
        quota = limiter.get_role_quota("unknown_role")
        assert quota == (10, 60, 5)

    def test_admin_has_higher_limit_than_user(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60, burst=0)
        limiter.set_role_quota("admin", max_requests=20, window_seconds=60, burst=0)

        # Admin can make 20 calls
        for _ in range(20):
            assert limiter.check("1.2.3.4", role="admin") is True
        assert limiter.check("1.2.3.4", role="admin") is False

        # User can only make 5
        for _ in range(5):
            assert limiter.check("1.2.3.4", role="user") is True
        assert limiter.check("1.2.3.4", role="user") is False

    def test_role_buckets_are_isolated(self):
        """Admin and user from same IP have separate buckets."""
        limiter = RateLimiter(max_requests=2, window_seconds=60, burst=0)
        limiter.set_role_quota("admin", max_requests=5, window_seconds=60, burst=0)

        # Exhaust user quota
        assert limiter.check("1.2.3.4", role="user") is True
        assert limiter.check("1.2.3.4", role="user") is True
        assert limiter.check("1.2.3.4", role="user") is False

        # Admin from same IP should still have full quota
        assert limiter.check("1.2.3.4", role="admin") is True

    def test_dynamic_quota_update(self):
        """Quota can be updated mid-flight."""
        limiter = RateLimiter(max_requests=5, window_seconds=60, burst=0)

        # Make 3 calls as user
        for _ in range(3):
            assert limiter.check("1.2.3.4", role="user") is True

        # Increase quota dynamically
        limiter.set_role_quota("user", max_requests=10, window_seconds=60, burst=0)

        # Should now allow more calls (3 used, 7 remaining in new window)
        for _ in range(7):
            assert limiter.check("1.2.3.4", role="user") is True

    def test_get_remaining_per_role(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60, burst=2)
        limiter.set_role_quota("admin", max_requests=10, window_seconds=60, burst=0)

        # Admin: 10 capacity
        assert limiter.get_remaining("1.2.3.4", role="admin") == 10
        limiter.check("1.2.3.4", role="admin")
        assert limiter.get_remaining("1.2.3.4", role="admin") == 9

        # User: 7 capacity (5+2)
        assert limiter.get_remaining("1.2.3.4", role="user") == 7

    def test_stats_includes_role_quotas(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60, burst=2)
        limiter.set_role_quota("admin", max_requests=100, window_seconds=30, burst=10)

        stats = limiter.stats
        assert "role_quotas" in stats
        assert "admin" in stats["role_quotas"]
        assert stats["role_quotas"]["admin"]["max_requests"] == 100
        assert stats["role_quotas"]["admin"]["window_seconds"] == 30
        assert stats["role_quotas"]["admin"]["burst"] == 10

    def test_stats_per_key_uses_role_prefix(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60, burst=0)
        limiter.check("1.2.3.4", role="admin")
        limiter.check("1.2.3.4", role="user")

        stats = limiter.stats
        assert "admin:1.2.3.4" in stats["per_key"]
        assert "user:1.2.3.4" in stats["per_key"]
        assert stats["per_key"]["admin:1.2.3.4"] == 1
        assert stats["per_key"]["user:1.2.3.4"] == 1


class TestRateLimitConfigQuotas:
    """Test RateLimitConfig.quotas field."""

    def test_default_quotas_empty(self):
        from agentbase.config.schema import RateLimitConfig

        config = RateLimitConfig()
        assert config.quotas == {}

    def test_quotas_with_roles(self):
        from agentbase.config.schema import RateLimitConfig

        config = RateLimitConfig(
            quotas={
                "admin": {"max_requests": 200, "window_seconds": 60, "burst": 20},
                "readonly": {"max_requests": 10, "window_seconds": 60, "burst": 0},
            }
        )
        assert "admin" in config.quotas
        assert "readonly" in config.quotas

    def test_get_quota_for_role_admin(self):
        from agentbase.config.schema import RateLimitConfig

        config = RateLimitConfig(
            max_requests=60,
            window_seconds=60,
            burst=10,
            quotas={
                "admin": {"max_requests": 200, "burst": 20},
            }
        )
        max_req, window, burst = config.get_quota_for_role("admin")
        assert max_req == 200
        assert window == 60  # Falls back to global default
        assert burst == 20

    def test_get_quota_for_role_unknown_falls_back(self):
        from agentbase.config.schema import RateLimitConfig

        config = RateLimitConfig(max_requests=30, window_seconds=60, burst=5)
        max_req, window, burst = config.get_quota_for_role("unknown")
        assert max_req == 30
        assert window == 60
        assert burst == 5

    def test_get_quota_for_role_partial_config(self):
        """Role quota with only max_requests set, others fall back to global."""
        from agentbase.config.schema import RateLimitConfig

        config = RateLimitConfig(
            max_requests=60,
            window_seconds=120,
            burst=10,
            quotas={
                "user": {"max_requests": 30},
            }
        )
        max_req, window, burst = config.get_quota_for_role("user")
        assert max_req == 30  # From role quota
        assert window == 120  # From global default
        assert burst == 10   # From global default
