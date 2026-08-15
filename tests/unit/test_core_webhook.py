"""Unit tests for the webhook event notification service (core.webhook).

Covers:
- WebhookEndpoint dataclass (to_dict / matches / wildcard events)
- WebhookDelivery dataclass (to_dict)
- WebhookDeliveryFilter dataclass
- WebhookStats dataclass (to_dict)
- InMemoryWebhookProvider (register / get / list / delete / record / query / stats)
- NullWebhookProvider (no-op behaviour)
- WebhookManager (enabled / disabled / register / update / delete / dispatch / test / stats)
- Registry (register_webhook_provider / create / has / names / unregister)
- Singleton (get_webhook_manager / set_webhook_manager / reset_webhook_manager)
- HMAC payload signing (_sign_payload)
- Protocol compliance
- Concurrency (thread-safe operations)
"""
from __future__ import annotations

import threading

import pytest

from agentbase.core.webhook import (
    InMemoryWebhookProvider,
    NullWebhookProvider,
    WebhookDelivery,
    WebhookDeliveryFilter,
    WebhookEndpoint,
    WebhookManager,
    WebhookProvider,
    WebhookRegistry,
    WebhookStats,
    _apply_delivery_filter,
    _sign_payload,
    get_webhook_manager,
    register_webhook_provider,
    reset_webhook_manager,
    set_webhook_manager,
)
from agentbase.runtime.errors import RegistryError


# ---------------------------------------------------------------------------
# WebhookEndpoint
# ---------------------------------------------------------------------------

class TestWebhookEndpoint:
    """Test WebhookEndpoint dataclass."""

    def test_to_dict_basic(self):
        ep = WebhookEndpoint(url="https://example.com/hook", events=["*"])
        d = ep.to_dict()
        assert d["url"] == "https://example.com/hook"
        assert d["events"] == ["*"]
        assert d["active"] is True
        assert d["secret"] == ""
        assert d["id"] != ""

    def test_to_dict_with_secret_masks(self):
        ep = WebhookEndpoint(url="https://example.com/hook", secret="super-secret")
        d = ep.to_dict()
        assert d["secret"] == "***"

    def test_to_dict_no_secret_shows_empty(self):
        ep = WebhookEndpoint(url="https://example.com/hook", secret="")
        d = ep.to_dict()
        assert d["secret"] == ""

    def test_id_auto_generated(self):
        ep = WebhookEndpoint(url="https://example.com/hook")
        assert ep.id != ""
        assert len(ep.id) >= 8

    def test_matches_wildcard_all(self):
        ep = WebhookEndpoint(url="https://example.com/hook", events=["*"])
        assert ep.matches("agent.invoke.completed") is True
        assert ep.matches("any.event") is True

    def test_matches_exact_event(self):
        ep = WebhookEndpoint(url="https://example.com/hook", events=["agent.invoke.completed"])
        assert ep.matches("agent.invoke.completed") is True
        assert ep.matches("agent.stream.completed") is False

    def test_matches_prefix_wildcard(self):
        ep = WebhookEndpoint(url="https://example.com/hook", events=["agent.invoke.*"])
        assert ep.matches("agent.invoke.completed") is True
        assert ep.matches("agent.invoke.failed") is True
        assert ep.matches("agent.stream.completed") is False

    def test_matches_inactive_endpoint(self):
        ep = WebhookEndpoint(url="https://example.com/hook", events=["*"], active=False)
        assert ep.matches("any.event") is False

    def test_matches_multiple_events(self):
        ep = WebhookEndpoint(
            url="https://example.com/hook",
            events=["agent.invoke.completed", "agent.stream.completed"],
        )
        assert ep.matches("agent.invoke.completed") is True
        assert ep.matches("agent.stream.completed") is True
        assert ep.matches("agent.resume.completed") is False


# ---------------------------------------------------------------------------
# WebhookDelivery
# ---------------------------------------------------------------------------

class TestWebhookDelivery:
    """Test WebhookDelivery dataclass."""

    def test_to_dict(self):
        d = WebhookDelivery(
            endpoint_id="ep-1",
            endpoint_url="https://example.com/hook",
            event="agent.invoke.completed",
            payload={"agent": "default"},
            status="success",
            status_code=200,
            attempts=1,
        )
        result = d.to_dict()
        assert result["endpoint_id"] == "ep-1"
        assert result["endpoint_url"] == "https://example.com/hook"
        assert result["event"] == "agent.invoke.completed"
        assert result["status"] == "success"
        assert result["status_code"] == 200
        assert result["attempts"] == 1
        assert result["id"] != ""

    def test_id_auto_generated(self):
        d = WebhookDelivery(
            endpoint_id="ep-1",
            endpoint_url="https://example.com/hook",
            event="test",
        )
        assert d.id != ""
        assert len(d.id) >= 8

    def test_default_status_pending(self):
        d = WebhookDelivery(
            endpoint_id="ep-1",
            endpoint_url="https://example.com/hook",
            event="test",
        )
        assert d.status == "pending"
        assert d.attempts == 0
        assert d.error == ""


# ---------------------------------------------------------------------------
# WebhookStats
# ---------------------------------------------------------------------------

class TestWebhookStats:
    """Test WebhookStats dataclass."""

    def test_to_dict_empty(self):
        s = WebhookStats()
        d = s.to_dict()
        assert d["total_endpoints"] == 0
        assert d["active_endpoints"] == 0
        assert d["total_deliveries"] == 0
        assert d["successful_deliveries"] == 0
        assert d["failed_deliveries"] == 0
        assert d["success_rate"] == 0.0

    def test_to_dict_with_data(self):
        s = WebhookStats(
            total_endpoints=5,
            active_endpoints=3,
            total_deliveries=100,
            successful_deliveries=90,
            failed_deliveries=10,
            success_rate=0.9,
            by_event={"agent.invoke.completed": 60, "agent.stream.completed": 40},
        )
        d = s.to_dict()
        assert d["total_endpoints"] == 5
        assert d["active_endpoints"] == 3
        assert d["total_deliveries"] == 100
        assert d["successful_deliveries"] == 90
        assert d["failed_deliveries"] == 10
        assert d["success_rate"] == 0.9
        assert d["by_event"]["agent.invoke.completed"] == 60


# ---------------------------------------------------------------------------
# InMemoryWebhookProvider
# ---------------------------------------------------------------------------

class TestInMemoryWebhookProvider:
    """Test InMemoryWebhookProvider."""

    def test_register_and_get_endpoint(self):
        provider = InMemoryWebhookProvider()
        ep = WebhookEndpoint(url="https://example.com/hook", events=["*"])
        stored = provider.register_endpoint(ep)
        assert stored.id == ep.id
        fetched = provider.get_endpoint(ep.id)
        assert fetched is not None
        assert fetched.url == "https://example.com/hook"

    def test_get_endpoint_not_found(self):
        provider = InMemoryWebhookProvider()
        assert provider.get_endpoint("nonexistent") is None

    def test_list_endpoints(self):
        provider = InMemoryWebhookProvider()
        ep1 = WebhookEndpoint(url="https://example.com/hook1", active=True)
        ep2 = WebhookEndpoint(url="https://example.com/hook2", active=False)
        provider.register_endpoint(ep1)
        provider.register_endpoint(ep2)
        all_endpoints = provider.list_endpoints()
        assert len(all_endpoints) == 2
        active_only = provider.list_endpoints(active_only=True)
        assert len(active_only) == 1
        assert active_only[0].url == "https://example.com/hook1"

    def test_delete_endpoint(self):
        provider = InMemoryWebhookProvider()
        ep = WebhookEndpoint(url="https://example.com/hook")
        provider.register_endpoint(ep)
        assert provider.delete_endpoint(ep.id) is True
        assert provider.get_endpoint(ep.id) is None
        assert provider.delete_endpoint("nonexistent") is False

    def test_record_and_query_deliveries(self):
        provider = InMemoryWebhookProvider()
        d1 = WebhookDelivery(
            endpoint_id="ep1",
            endpoint_url="https://example.com/hook",
            event="agent.invoke.completed",
            status="success",
        )
        d2 = WebhookDelivery(
            endpoint_id="ep1",
            endpoint_url="https://example.com/hook",
            event="agent.stream.completed",
            status="failed",
        )
        provider.record_delivery(d1)
        provider.record_delivery(d2)
        all_d = provider.query_deliveries()
        assert len(all_d) == 2
        filtered = provider.query_deliveries(
            WebhookDeliveryFilter(event="agent.invoke.completed")
        )
        assert len(filtered) == 1
        assert filtered[0].event == "agent.invoke.completed"

    def test_stats(self):
        provider = InMemoryWebhookProvider()
        ep1 = WebhookEndpoint(url="https://example.com/hook1", active=True)
        ep2 = WebhookEndpoint(url="https://example.com/hook2", active=False)
        provider.register_endpoint(ep1)
        provider.register_endpoint(ep2)
        provider.record_delivery(WebhookDelivery(
            endpoint_id=ep1.id, endpoint_url=ep1.url, event="test", status="success"
        ))
        provider.record_delivery(WebhookDelivery(
            endpoint_id=ep1.id, endpoint_url=ep1.url, event="test", status="failed"
        ))
        stats = provider.get_stats()
        assert stats.total_endpoints == 2
        assert stats.active_endpoints == 1
        assert stats.total_deliveries == 2
        assert stats.successful_deliveries == 1
        assert stats.failed_deliveries == 1
        assert stats.success_rate == 0.5

    def test_fifo_eviction(self):
        provider = InMemoryWebhookProvider(max_deliveries=3)
        for i in range(5):
            provider.record_delivery(WebhookDelivery(
                endpoint_id="ep1",
                endpoint_url="https://example.com/hook",
                event=f"event.{i}",
                status="success",
            ))
        deliveries = provider.query_deliveries()
        assert len(deliveries) == 3
        # First 2 should be evicted (FIFO)
        assert deliveries[0].event == "event.2"
        assert deliveries[2].event == "event.4"

    def test_close(self):
        provider = InMemoryWebhookProvider()
        ep = WebhookEndpoint(url="https://example.com/hook")
        provider.register_endpoint(ep)
        provider.close()
        assert len(provider.list_endpoints()) == 0


# ---------------------------------------------------------------------------
# NullWebhookProvider
# ---------------------------------------------------------------------------

class TestNullWebhookProvider:
    """Test NullWebhookProvider."""

    def test_register_endpoint(self):
        provider = NullWebhookProvider()
        ep = WebhookEndpoint(url="https://example.com/hook")
        result = provider.register_endpoint(ep)
        assert result is ep

    def test_get_endpoint(self):
        provider = NullWebhookProvider()
        assert provider.get_endpoint("any") is None

    def test_list_endpoints(self):
        provider = NullWebhookProvider()
        assert provider.list_endpoints() == []
        assert provider.list_endpoints(active_only=True) == []

    def test_delete_endpoint(self):
        provider = NullWebhookProvider()
        assert provider.delete_endpoint("any") is False

    def test_record_delivery(self):
        provider = NullWebhookProvider()
        d = WebhookDelivery(
            endpoint_id="ep1", endpoint_url="https://example.com/hook", event="test"
        )
        result = provider.record_delivery(d)
        assert result is d

    def test_query_deliveries(self):
        provider = NullWebhookProvider()
        assert provider.query_deliveries() == []

    def test_get_stats(self):
        provider = NullWebhookProvider()
        stats = provider.get_stats()
        assert stats.total_endpoints == 0
        assert stats.total_deliveries == 0

    def test_close(self):
        provider = NullWebhookProvider()
        provider.close()


# ---------------------------------------------------------------------------
# WebhookManager
# ---------------------------------------------------------------------------

class TestWebhookManager:
    """Test WebhookManager."""

    def test_disabled_manager(self):
        mgr = WebhookManager(provider="memory", enabled=False)
        assert mgr.enabled is False
        # Operations should be no-op
        ep = mgr.register_endpoint(url="https://example.com/hook")
        assert mgr.list_endpoints() == []
        assert mgr.get_endpoint(ep.id) is None
        # dispatch_event returns 0 when disabled
        count = mgr.dispatch_event(event="test", payload={"key": "value"})
        assert count == 0
        stats = mgr.get_stats()
        assert stats.total_endpoints == 0

    def test_enabled_manager_register(self):
        mgr = WebhookManager(provider="memory", enabled=True)
        ep = mgr.register_endpoint(url="https://example.com/hook", events=["*"])
        assert ep.url == "https://example.com/hook"
        assert ep.id != ""
        fetched = mgr.get_endpoint(ep.id)
        assert fetched is not None
        assert fetched.url == "https://example.com/hook"

    def test_register_invalid_url(self):
        mgr = WebhookManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="Invalid webhook URL"):
            mgr.register_endpoint(url="ftp://bad-url.com")

    def test_register_no_scheme(self):
        mgr = WebhookManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="Invalid webhook URL"):
            mgr.register_endpoint(url="not-a-url")

    def test_update_endpoint(self):
        mgr = WebhookManager(provider="memory", enabled=True)
        ep = mgr.register_endpoint(url="https://example.com/hook")
        updated = mgr.update_endpoint(ep.id, description="Updated", active=False)
        assert updated is not None
        assert updated.description == "Updated"
        assert updated.active is False

    def test_update_endpoint_not_found(self):
        mgr = WebhookManager(provider="memory", enabled=True)
        result = mgr.update_endpoint("nonexistent", description="test")
        assert result is None

    def test_delete_endpoint(self):
        mgr = WebhookManager(provider="memory", enabled=True)
        ep = mgr.register_endpoint(url="https://example.com/hook")
        assert mgr.delete_endpoint(ep.id) is True
        assert mgr.get_endpoint(ep.id) is None
        assert mgr.delete_endpoint("nonexistent") is False

    def test_list_endpoints(self):
        mgr = WebhookManager(provider="memory", enabled=True)
        mgr.register_endpoint(url="https://example.com/hook1", description="One")
        mgr.register_endpoint(url="https://example.com/hook2", active=False, description="Two")
        all_endpoints = mgr.list_endpoints()
        assert len(all_endpoints) == 2
        active = mgr.list_endpoints(active_only=True)
        assert len(active) == 1

    def test_dispatch_no_matching_endpoints(self):
        mgr = WebhookManager(provider="memory", enabled=True)
        mgr.register_endpoint(
            url="https://example.com/hook",
            events=["agent.invoke.completed"],
        )
        count = mgr.dispatch_event(event="agent.stream.completed", payload={})
        assert count == 0

    def test_dispatch_with_matching_endpoints(self):
        mgr = WebhookManager(provider="memory", enabled=True)
        mgr.register_endpoint(url="https://example.com/hook", events=["*"])
        # dispatch runs in background thread, but should return count immediately
        count = mgr.dispatch_event(event="test.event", payload={"key": "value"})
        assert count == 1

    def test_query_deliveries_disabled(self):
        mgr = WebhookManager(provider="memory", enabled=False)
        assert mgr.query_deliveries() == []

    def test_get_stats_disabled(self):
        mgr = WebhookManager(provider="memory", enabled=False)
        stats = mgr.get_stats()
        assert stats.total_endpoints == 0

    def test_test_endpoint_not_found(self):
        mgr = WebhookManager(provider="memory", enabled=True)
        result = mgr.test_endpoint("nonexistent")
        assert result is None

    def test_close(self):
        mgr = WebhookManager(provider="memory", enabled=True)
        mgr.register_endpoint(url="https://example.com/hook")
        mgr.close()
        assert len(mgr.list_endpoints()) == 0


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestWebhookRegistry:
    """Test WebhookRegistry."""

    def test_register_and_create(self):
        registry = WebhookRegistry()
        registry.register("custom", InMemoryWebhookProvider)
        provider = registry.create("custom")
        assert isinstance(provider, InMemoryWebhookProvider)

    def test_register_empty_name(self):
        registry = WebhookRegistry()
        with pytest.raises(RegistryError, match="empty"):
            registry.register("", InMemoryWebhookProvider)

    def test_register_duplicate(self):
        registry = WebhookRegistry()
        registry.register("test", InMemoryWebhookProvider)
        with pytest.raises(RegistryError, match="already registered"):
            registry.register("test", InMemoryWebhookProvider)

    def test_register_override(self):
        registry = WebhookRegistry()
        registry.register("test", InMemoryWebhookProvider)
        registry.register("test", InMemoryWebhookProvider, override=True)

    def test_create_unknown(self):
        registry = WebhookRegistry()
        with pytest.raises(RegistryError, match="Unknown webhook provider"):
            registry.create("nonexistent")

    def test_has(self):
        registry = WebhookRegistry()
        registry.register("test", InMemoryWebhookProvider)
        assert registry.has("test") is True
        assert registry.has("nonexistent") is False

    def test_names(self):
        registry = WebhookRegistry()
        registry.register("alpha", InMemoryWebhookProvider)
        registry.register("beta", InMemoryWebhookProvider)
        names = registry.names()
        assert "alpha" in names
        assert "beta" in names

    def test_count(self):
        registry = WebhookRegistry()
        registry.register("a", InMemoryWebhookProvider)
        registry.register("b", InMemoryWebhookProvider)
        assert registry.count == 2

    def test_unregister(self):
        registry = WebhookRegistry()
        registry.register("test", InMemoryWebhookProvider)
        assert registry.unregister("test") is True
        assert registry.has("test") is False
        assert registry.unregister("test") is False


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

class TestRegisterWebhookProviderDecorator:
    """Test register_webhook_provider decorator."""

    def test_decorator_registers(self):
        @register_webhook_provider("test_decorator_provider", override=True)
        class CustomProvider:
            def register_endpoint(self, endpoint):
                return endpoint

            def get_endpoint(self, endpoint_id):
                return None

            def list_endpoints(self, *, active_only=False):
                return []

            def delete_endpoint(self, endpoint_id):
                return False

            def record_delivery(self, delivery):
                return delivery

            def query_deliveries(self, filter=None):
                return []

            def get_stats(self):
                return WebhookStats()

            def close(self):
                pass

        from agentbase.core.webhook import webhook_registry
        assert webhook_registry.has("test_decorator_provider")


# ---------------------------------------------------------------------------
# Singleton management
# ---------------------------------------------------------------------------

class TestSingletonManagement:
    """Test singleton get/set/reset."""

    def test_get_without_set_raises(self):
        reset_webhook_manager()
        with pytest.raises(RuntimeError, match="not initialised"):
            get_webhook_manager()

    def test_set_and_get(self):
        mgr = WebhookManager(provider="memory", enabled=True)
        set_webhook_manager(mgr)
        assert get_webhook_manager() is mgr

    def test_reset(self):
        mgr = WebhookManager(provider="memory", enabled=True)
        set_webhook_manager(mgr)
        reset_webhook_manager()
        with pytest.raises(RuntimeError, match="not initialised"):
            get_webhook_manager()

    def teardown_method(self):
        reset_webhook_manager()


# ---------------------------------------------------------------------------
# HMAC payload signing
# ---------------------------------------------------------------------------

class TestPayloadSigning:
    """Test _sign_payload function."""

    def test_sign_with_secret(self):
        payload = b'{"event": "test"}'
        secret = "my-secret"
        sig = _sign_payload(payload, secret)
        assert sig != ""
        assert len(sig) == 64  # SHA-256 hex digest

    def test_sign_empty_secret(self):
        payload = b'{"event": "test"}'
        sig = _sign_payload(payload, "")
        assert sig == ""

    def test_sign_different_secrets_different_sigs(self):
        payload = b'{"event": "test"}'
        sig1 = _sign_payload(payload, "secret1")
        sig2 = _sign_payload(payload, "secret2")
        assert sig1 != sig2

    def test_sign_same_payload_same_sig(self):
        payload = b'{"event": "test"}'
        secret = "my-secret"
        sig1 = _sign_payload(payload, secret)
        sig2 = _sign_payload(payload, secret)
        assert sig1 == sig2


# ---------------------------------------------------------------------------
# Delivery filter helper
# ---------------------------------------------------------------------------

class TestApplyDeliveryFilter:
    """Test _apply_delivery_filter."""

    def _make_deliveries(self):
        return [
            WebhookDelivery(
                endpoint_id="ep1", endpoint_url="https://a.com",
                event="agent.invoke.completed", status="success",
                timestamp="2024-01-01T00:00:00Z",
            ),
            WebhookDelivery(
                endpoint_id="ep2", endpoint_url="https://b.com",
                event="agent.stream.completed", status="failed",
                timestamp="2024-01-02T00:00:00Z",
            ),
        ]

    def test_no_filter_returns_all(self):
        records = self._make_deliveries()
        flt = WebhookDeliveryFilter(limit=0)
        result = _apply_delivery_filter(records, flt)
        assert len(result) == 2

    def test_filter_by_endpoint_id(self):
        records = self._make_deliveries()
        flt = WebhookDeliveryFilter(endpoint_id="ep1")
        result = _apply_delivery_filter(records, flt)
        assert len(result) == 1
        assert result[0].endpoint_id == "ep1"

    def test_filter_by_event(self):
        records = self._make_deliveries()
        flt = WebhookDeliveryFilter(event="agent.invoke.completed")
        result = _apply_delivery_filter(records, flt)
        assert len(result) == 1

    def test_filter_by_status(self):
        records = self._make_deliveries()
        flt = WebhookDeliveryFilter(status="failed")
        result = _apply_delivery_filter(records, flt)
        assert len(result) == 1
        assert result[0].status == "failed"

    def test_filter_with_limit(self):
        records = self._make_deliveries()
        flt = WebhookDeliveryFilter(limit=1)
        result = _apply_delivery_filter(records, flt)
        assert len(result) == 1

    def test_filter_with_offset(self):
        records = self._make_deliveries()
        flt = WebhookDeliveryFilter(offset=1, limit=10)
        result = _apply_delivery_filter(records, flt)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------

class TestProtocolCompliance:
    """Test that providers satisfy the WebhookProvider Protocol."""

    def test_inmemory_provider_is_compliant(self):
        provider = InMemoryWebhookProvider()
        assert isinstance(provider, WebhookProvider)

    def test_null_provider_is_compliant(self):
        provider = NullWebhookProvider()
        assert isinstance(provider, WebhookProvider)


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

class TestConcurrency:
    """Test thread-safe operations."""

    def test_concurrent_register_endpoints(self):
        provider = InMemoryWebhookProvider()
        threads = []
        for i in range(10):
            def register(idx):
                provider.register_endpoint(
                    WebhookEndpoint(url=f"https://example.com/hook{idx}")
                )
            t = threading.Thread(target=register, args=(i,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        assert len(provider.list_endpoints()) == 10

    def test_concurrent_record_deliveries(self):
        provider = InMemoryWebhookProvider()
        threads = []
        for i in range(20):
            def record(idx):
                provider.record_delivery(WebhookDelivery(
                    endpoint_id="ep1",
                    endpoint_url="https://example.com/hook",
                    event=f"event.{idx}",
                    status="success",
                ))
            t = threading.Thread(target=record, args=(i,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        assert len(provider.query_deliveries()) == 20
