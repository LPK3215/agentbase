"""Unit tests for the user management service (core.user_manager).

Covers:
- Password hashing (hash_password / verify_password)
- UserEntry dataclass (to_dict / from_dict)
- InMemoryUserProvider CRUD (register / get / get_by_email / list / update / delete)
- NullUserProvider (no-op behaviour)
- UserManager (enabled / disabled / register / authenticate / change_password)
- Registry (register_user_provider / get_user_provider / list_user_providers)
- Singleton (get_user_manager / set_user_manager)
- Concurrency (thread-safe operations)
- Protocol compliance
"""
from __future__ import annotations

import threading

import pytest

from agentbase.core.user_manager import (
    InMemoryUserProvider,
    NullUserProvider,
    UserEntry,
    UserManager,
    UserProvider,
    get_user_manager,
    get_user_provider,
    hash_password,
    list_user_providers,
    register_user_provider,
    set_user_manager,
    verify_password,
)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

class TestPasswordHashing:
    """Test password hashing and verification."""

    def test_hash_password_returns_string(self):
        h = hash_password("secret123")
        assert isinstance(h, str)
        assert h.startswith("pbkdf2_sha256$")

    def test_hash_password_does_not_contain_plaintext(self):
        h = hash_password("secret123")
        assert "secret123" not in h

    def test_hash_password_different_salts(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # different salts

    def test_verify_password_correct(self):
        h = hash_password("mypassword")
        assert verify_password("mypassword", h) is True

    def test_verify_password_wrong(self):
        h = hash_password("mypassword")
        assert verify_password("wrongpassword", h) is False

    def test_verify_password_empty_hash(self):
        assert verify_password("test", "") is False

    def test_verify_password_malformed_hash(self):
        assert verify_password("test", "not_a_hash") is False

    def test_verify_password_missing_parts(self):
        assert verify_password("test", "pbkdf2_sha256$100000") is False

    def test_verify_password_wrong_prefix(self):
        assert verify_password("test", "bcrypt$100000$salt$hash") is False

    def test_hash_password_empty_string(self):
        h = hash_password("")
        assert isinstance(h, str)
        assert verify_password("", h) is True

    def test_hash_password_custom_rounds(self):
        h = hash_password("test", rounds=1000)
        assert "1000" in h
        assert verify_password("test", h) is True

    def test_verify_password_constant_time_no_exception(self):
        # Should not raise on malformed input
        assert verify_password("test", "a$b$c$d") is False


# ---------------------------------------------------------------------------
# UserEntry dataclass
# ---------------------------------------------------------------------------

class TestUserEntry:
    """Test UserEntry dataclass behaviour."""

    def test_default_values(self):
        u = UserEntry(username="alice")
        assert u.username == "alice"
        assert u.email == ""
        assert u.password_hash == ""
        assert u.roles == ["user"]
        assert u.enabled is True
        assert u.created_at != ""
        assert u.updated_at != ""
        assert u.last_login_at == ""
        assert u.metadata == {}

    def test_to_dict_excludes_password_hash_by_default(self):
        u = UserEntry(username="alice", password_hash="secret_hash")
        d = u.to_dict()
        assert "password_hash" not in d
        assert d["username"] == "alice"

    def test_to_dict_includes_password_hash_when_requested(self):
        u = UserEntry(username="alice", password_hash="secret_hash")
        d = u.to_dict(include_hash=True)
        assert d["password_hash"] == "secret_hash"

    def test_from_dict_creates_entry(self):
        data = {
            "username": "bob",
            "email": "bob@test.com",
            "password_hash": "hashed",
            "roles": ["admin"],
            "enabled": False,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z",
            "last_login_at": "2024-01-03T00:00:00Z",
            "metadata": {"display_name": "Bob"},
        }
        u = UserEntry.from_dict(data)
        assert u.username == "bob"
        assert u.email == "bob@test.com"
        assert u.password_hash == "hashed"
        assert u.roles == ["admin"]
        assert u.enabled is False
        assert u.metadata == {"display_name": "Bob"}

    def test_from_dict_ignores_unknown_keys(self):
        data = {"username": "charlie", "unknown_field": "ignored"}
        u = UserEntry.from_dict(data)
        assert u.username == "charlie"

    def test_to_dict_roundtrip(self):
        u = UserEntry(
            username="alice",
            email="alice@test.com",
            password_hash="hashed",
            roles=["admin"],
            enabled=False,
            metadata={"key": "value"},
        )
        d = u.to_dict(include_hash=True)
        u2 = UserEntry.from_dict(d)
        assert u2.username == u.username
        assert u2.email == u.email
        assert u2.password_hash == u.password_hash
        assert u2.roles == u.roles
        assert u2.enabled == u.enabled
        assert u2.metadata == u.metadata

    def test_roles_default_not_shared(self):
        u1 = UserEntry(username="a")
        u2 = UserEntry(username="b")
        u1.roles.append("admin")
        assert "admin" not in u2.roles

    def test_metadata_default_not_shared(self):
        u1 = UserEntry(username="a")
        u2 = UserEntry(username="b")
        u1.metadata["key"] = "value"
        assert "key" not in u2.metadata


# ---------------------------------------------------------------------------
# InMemoryUserProvider
# ---------------------------------------------------------------------------

class TestInMemoryUserProvider:
    """Test InMemoryUserProvider CRUD operations."""

    def test_register_and_get(self):
        p = InMemoryUserProvider()
        u = UserEntry(username="alice", email="alice@test.com")
        stored = p.register(u)
        assert stored.username == "alice"
        got = p.get("alice")
        assert got is not None
        assert got.username == "alice"

    def test_register_empty_username_raises(self):
        p = InMemoryUserProvider()
        with pytest.raises(ValueError):
            p.register(UserEntry(username=""))

    def test_register_replaces_existing_preserves_created_at(self):
        p = InMemoryUserProvider()
        original = p.register(UserEntry(username="alice"))
        original_created = original.created_at
        # Replace
        updated = p.register(UserEntry(username="alice", email="new@test.com"))
        assert updated.created_at == original_created
        assert updated.email == "new@test.com"

    def test_get_nonexistent_returns_none(self):
        p = InMemoryUserProvider()
        assert p.get("nobody") is None

    def test_get_by_email(self):
        p = InMemoryUserProvider()
        p.register(UserEntry(username="alice", email="alice@test.com"))
        got = p.get_by_email("alice@test.com")
        assert got is not None
        assert got.username == "alice"

    def test_get_by_email_nonexistent_returns_none(self):
        p = InMemoryUserProvider()
        assert p.get_by_email("nobody@test.com") is None

    def test_get_by_email_empty_string(self):
        p = InMemoryUserProvider()
        p.register(UserEntry(username="alice", email=""))
        assert p.get_by_email("") is None

    def test_list_empty(self):
        p = InMemoryUserProvider()
        assert p.list() == []

    def test_list_multiple(self):
        p = InMemoryUserProvider()
        p.register(UserEntry(username="alice"))
        p.register(UserEntry(username="bob"))
        users = p.list()
        assert len(users) == 2
        names = {u.username for u in users}
        assert names == {"alice", "bob"}

    def test_update_existing(self):
        p = InMemoryUserProvider()
        p.register(UserEntry(username="alice", email="old@test.com"))
        updated = p.update("alice", {"email": "new@test.com", "enabled": False})
        assert updated is not None
        assert updated.email == "new@test.com"
        assert updated.enabled is False

    def test_update_nonexistent_returns_none(self):
        p = InMemoryUserProvider()
        assert p.update("nobody", {"email": "test@test.com"}) is None

    def test_update_ignores_username_field(self):
        p = InMemoryUserProvider()
        p.register(UserEntry(username="alice"))
        updated = p.update("alice", {"username": "changed"})
        assert updated is not None
        assert updated.username == "alice"  # username not changed

    def test_update_email_changes_email_index(self):
        p = InMemoryUserProvider()
        p.register(UserEntry(username="alice", email="old@test.com"))
        p.update("alice", {"email": "new@test.com"})
        assert p.get_by_email("old@test.com") is None
        assert p.get_by_email("new@test.com") is not None

    def test_delete_existing(self):
        p = InMemoryUserProvider()
        p.register(UserEntry(username="alice", email="alice@test.com"))
        assert p.delete("alice") is True
        assert p.get("alice") is None
        assert p.get_by_email("alice@test.com") is None

    def test_delete_nonexistent_returns_false(self):
        p = InMemoryUserProvider()
        assert p.delete("nobody") is False

    def test_close_clears_state(self):
        p = InMemoryUserProvider()
        p.register(UserEntry(username="alice"))
        p.close()
        assert p.list() == []

    def test_close_empty_no_error(self):
        p = InMemoryUserProvider()
        p.close()  # no error

    def test_register_with_email_updates_email_index(self):
        p = InMemoryUserProvider()
        p.register(UserEntry(username="alice", email="alice@test.com"))
        # Change email via re-register
        p.register(UserEntry(username="alice", email="new@test.com"))
        assert p.get_by_email("alice@test.com") is None
        assert p.get_by_email("new@test.com") is not None


# ---------------------------------------------------------------------------
# NullUserProvider
# ---------------------------------------------------------------------------

class TestNullUserProvider:
    """Test NullUserProvider no-op behaviour."""

    def test_register_returns_input(self):
        p = NullUserProvider()
        u = UserEntry(username="alice")
        result = p.register(u)
        assert result is u

    def test_get_returns_none(self):
        p = NullUserProvider()
        assert p.get("alice") is None

    def test_get_by_email_returns_none(self):
        p = NullUserProvider()
        assert p.get_by_email("alice@test.com") is None

    def test_list_returns_empty(self):
        p = NullUserProvider()
        assert p.list() == []

    def test_update_returns_none(self):
        p = NullUserProvider()
        assert p.update("alice", {"email": "test"}) is None

    def test_delete_returns_false(self):
        p = NullUserProvider()
        assert p.delete("alice") is False

    def test_close_no_error(self):
        p = NullUserProvider()
        p.close()


# ---------------------------------------------------------------------------
# UserManager
# ---------------------------------------------------------------------------

class TestUserManager:
    """Test UserManager high-level operations."""

    def test_disabled_uses_null_provider(self):
        mgr = UserManager(enabled=False)
        assert mgr.enabled is False
        assert isinstance(mgr.provider, NullUserProvider)

    def test_enabled_uses_memory_provider(self):
        mgr = UserManager(provider="memory", enabled=True)
        assert mgr.enabled is True
        assert isinstance(mgr.provider, InMemoryUserProvider)

    def test_unknown_provider_falls_back_to_null(self):
        mgr = UserManager(provider="nonexistent", enabled=True)
        assert mgr.enabled is True
        assert isinstance(mgr.provider, NullUserProvider)

    def test_register_hashes_password(self):
        mgr = UserManager(provider="memory", enabled=True)
        stored = mgr.register(username="alice", password="secret123")
        assert stored.password_hash != "secret123"
        assert stored.password_hash.startswith("pbkdf2_sha256$")

    def test_register_with_roles(self):
        mgr = UserManager(provider="memory", enabled=True)
        stored = mgr.register(username="admin1", password="pw", roles=["admin"])
        assert stored.roles == ["admin"]

    def test_register_with_metadata(self):
        mgr = UserManager(provider="memory", enabled=True)
        stored = mgr.register(username="alice", metadata={"display_name": "Alice"})
        assert stored.metadata == {"display_name": "Alice"}

    def test_register_empty_password(self):
        mgr = UserManager(provider="memory", enabled=True)
        stored = mgr.register(username="alice", password="")
        assert stored.password_hash == ""

    def test_register_default_roles(self):
        mgr = UserManager(provider="memory", enabled=True)
        stored = mgr.register(username="alice", password="pw")
        assert stored.roles == ["user"]

    def test_get(self):
        mgr = UserManager(provider="memory", enabled=True)
        mgr.register(username="alice", password="pw")
        got = mgr.get("alice")
        assert got is not None
        assert got.username == "alice"

    def test_get_nonexistent(self):
        mgr = UserManager(provider="memory", enabled=True)
        assert mgr.get("nobody") is None

    def test_get_by_email(self):
        mgr = UserManager(provider="memory", enabled=True)
        mgr.register(username="alice", email="alice@test.com", password="pw")
        got = mgr.get_by_email("alice@test.com")
        assert got is not None
        assert got.username == "alice"

    def test_list(self):
        mgr = UserManager(provider="memory", enabled=True)
        mgr.register(username="alice", password="pw")
        mgr.register(username="bob", password="pw")
        users = mgr.list()
        assert len(users) == 2

    def test_list_disabled_returns_empty(self):
        mgr = UserManager(enabled=False)
        assert mgr.list() == []

    def test_update(self):
        mgr = UserManager(provider="memory", enabled=True)
        mgr.register(username="alice", password="pw")
        updated = mgr.update("alice", {"email": "new@test.com", "enabled": False})
        assert updated is not None
        assert updated.email == "new@test.com"
        assert updated.enabled is False

    def test_update_nonexistent(self):
        mgr = UserManager(provider="memory", enabled=True)
        assert mgr.update("nobody", {"email": "test"}) is None

    def test_update_password_gets_hashed(self):
        mgr = UserManager(provider="memory", enabled=True)
        mgr.register(username="alice", password="old_pw")
        mgr.update("alice", {"password": "new_pw"})
        user = mgr.get("alice")
        assert user is not None
        assert user.password_hash != "new_pw"
        assert verify_password("new_pw", user.password_hash) is True

    def test_update_password_empty(self):
        mgr = UserManager(provider="memory", enabled=True)
        mgr.register(username="alice", password="old_pw")
        mgr.update("alice", {"password": ""})
        user = mgr.get("alice")
        assert user is not None
        assert user.password_hash == ""

    def test_delete(self):
        mgr = UserManager(provider="memory", enabled=True)
        mgr.register(username="alice", password="pw")
        assert mgr.delete("alice") is True
        assert mgr.get("alice") is None

    def test_delete_nonexistent(self):
        mgr = UserManager(provider="memory", enabled=True)
        assert mgr.delete("nobody") is False

    def test_authenticate_success(self):
        mgr = UserManager(provider="memory", enabled=True)
        mgr.register(username="alice", password="secret123")
        user = mgr.authenticate("alice", "secret123")
        assert user is not None
        assert user.username == "alice"
        assert user.last_login_at != ""

    def test_authenticate_wrong_password(self):
        mgr = UserManager(provider="memory", enabled=True)
        mgr.register(username="alice", password="secret123")
        assert mgr.authenticate("alice", "wrong") is None

    def test_authenticate_nonexistent_user(self):
        mgr = UserManager(provider="memory", enabled=True)
        assert mgr.authenticate("nobody", "pw") is None

    def test_authenticate_disabled_user(self):
        mgr = UserManager(provider="memory", enabled=True)
        mgr.register(username="alice", password="pw")
        mgr.update("alice", {"enabled": False})
        assert mgr.authenticate("alice", "pw") is None

    def test_authenticate_no_password_set(self):
        mgr = UserManager(provider="memory", enabled=True)
        mgr.register(username="alice", password="")
        assert mgr.authenticate("alice", "anything") is None

    def test_authenticate_updates_last_login(self):
        mgr = UserManager(provider="memory", enabled=True)
        mgr.register(username="alice", password="pw")
        user1 = mgr.authenticate("alice", "pw")
        assert user1 is not None
        first_login = user1.last_login_at
        assert first_login != ""
        # Authenticate again
        user2 = mgr.authenticate("alice", "pw")
        assert user2 is not None
        # last_login_at should be updated (or same if very fast)
        assert user2.last_login_at != ""

    def test_change_password_success(self):
        mgr = UserManager(provider="memory", enabled=True)
        mgr.register(username="alice", password="old_pw")
        assert mgr.change_password("alice", "new_pw") is True
        # Old password fails
        assert mgr.authenticate("alice", "old_pw") is None
        # New password works
        assert mgr.authenticate("alice", "new_pw") is not None

    def test_change_password_nonexistent(self):
        mgr = UserManager(provider="memory", enabled=True)
        assert mgr.change_password("nobody", "new_pw") is False

    def test_change_password_empty(self):
        mgr = UserManager(provider="memory", enabled=True)
        mgr.register(username="alice", password="pw")
        mgr.change_password("alice", "")
        user = mgr.get("alice")
        assert user is not None
        assert user.password_hash == ""

    def test_close(self):
        mgr = UserManager(provider="memory", enabled=True)
        mgr.register(username="alice", password="pw")
        mgr.close()
        assert mgr.list() == []

    def test_disabled_register_is_noop(self):
        mgr = UserManager(enabled=False)
        result = mgr.register(username="alice", password="pw")
        # NullUserProvider.register returns the input
        assert result.username == "alice"
        # But nothing stored
        assert mgr.get("alice") is None

    def test_disabled_authenticate_returns_none(self):
        mgr = UserManager(enabled=False)
        assert mgr.authenticate("alice", "pw") is None

    def test_disabled_delete_returns_false(self):
        mgr = UserManager(enabled=False)
        assert mgr.delete("alice") is False

    def test_disabled_change_password_returns_false(self):
        mgr = UserManager(enabled=False)
        assert mgr.change_password("alice", "pw") is False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    """Test user provider registry."""

    def test_default_providers_registered(self):
        names = list_user_providers()
        assert "memory" in names
        assert "null" in names

    def test_get_provider_memory(self):
        p = get_user_provider("memory")
        assert isinstance(p, InMemoryUserProvider)

    def test_get_provider_null(self):
        p = get_user_provider("null")
        assert isinstance(p, NullUserProvider)

    def test_get_provider_unknown_raises(self):
        with pytest.raises(Exception) as exc_info:
            get_user_provider("nonexistent_provider")
        assert "nonexistent_provider" in str(exc_info.value)

    def test_register_custom_provider(self):
        @register_user_provider("test_custom_v1")
        def make_provider(**kwargs):
            return InMemoryUserProvider()

        assert "test_custom_v1" in list_user_providers()
        p = get_user_provider("test_custom_v1")
        assert isinstance(p, InMemoryUserProvider)

    def test_register_custom_provider_class(self):
        @register_user_provider("test_custom_class_v1")
        class CustomProvider(InMemoryUserProvider):
            pass

        assert "test_custom_class_v1" in list_user_providers()
        p = get_user_provider("test_custom_class_v1")
        assert isinstance(p, CustomProvider)

    def test_list_providers_sorted(self):
        names = list_user_providers()
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    """Test singleton get/set behaviour."""

    def test_get_default_returns_disabled_manager(self):
        # Reset to default
        set_user_manager(UserManager(enabled=False))
        mgr = get_user_manager()
        assert mgr.enabled is False

    def test_set_then_get_returns_same_instance(self):
        custom = UserManager(provider="memory", enabled=True)
        set_user_manager(custom)
        assert get_user_manager() is custom

    def test_get_returns_same_instance_on_multiple_calls(self):
        set_user_manager(UserManager(enabled=False))
        mgr1 = get_user_manager()
        mgr2 = get_user_manager()
        assert mgr1 is mgr2

    def teardown_method(self, method):
        # Reset singleton after each test
        set_user_manager(UserManager(enabled=False))


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

class TestConcurrency:
    """Test thread-safe operations."""

    def test_concurrent_register(self):
        mgr = UserManager(provider="memory", enabled=True)
        threads = []
        errors = []

        def register_user(i):
            try:
                mgr.register(username=f"user_{i}", password=f"pw_{i}")
            except Exception as exc:
                errors.append(exc)

        for i in range(20):
            t = threading.Thread(target=register_user, args=(i,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        users = mgr.list()
        assert len(users) == 20

    def test_concurrent_authenticate(self):
        mgr = UserManager(provider="memory", enabled=True)
        mgr.register(username="alice", password="secret123")
        threads = []
        results = []

        def auth():
            result = mgr.authenticate("alice", "secret123")
            results.append(result is not None)

        for _ in range(10):
            t = threading.Thread(target=auth)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        assert all(results)
        assert len(results) == 10

    def test_concurrent_update(self):
        mgr = UserManager(provider="memory", enabled=True)
        mgr.register(username="alice", email="old@test.com", password="pw")
        threads = []
        errors = []

        def update_email(i):
            try:
                mgr.update("alice", {"metadata": {"update_count": i}})
            except Exception as exc:
                errors.append(exc)

        for i in range(10):
            t = threading.Thread(target=update_email, args=(i,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        user = mgr.get("alice")
        assert user is not None
        assert "update_count" in user.metadata

    def test_concurrent_delete(self):
        mgr = UserManager(provider="memory", enabled=True)
        for i in range(10):
            mgr.register(username=f"user_{i}", password="pw")

        threads = []
        results = []

        def delete_user(i):
            result = mgr.delete(f"user_{i}")
            results.append(result)

        for i in range(10):
            t = threading.Thread(target=delete_user, args=(i,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        assert all(results)
        assert mgr.list() == []


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------

class TestProtocolCompliance:
    """Test that providers comply with UserProvider protocol."""

    def test_in_memory_provider_is_protocol(self):
        p = InMemoryUserProvider()
        assert isinstance(p, UserProvider)

    def test_null_provider_is_protocol(self):
        p = NullUserProvider()
        assert isinstance(p, UserProvider)

    def test_protocol_has_all_methods(self):
        # Verify protocol interface
        assert hasattr(UserProvider, "register")
        assert hasattr(UserProvider, "get")
        assert hasattr(UserProvider, "get_by_email")
        assert hasattr(UserProvider, "list")
        assert hasattr(UserProvider, "update")
        assert hasattr(UserProvider, "delete")
        assert hasattr(UserProvider, "close")
