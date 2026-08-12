"""Tests for redaction service — covers PII types, no-false-positive, hot-registration."""
from __future__ import annotations

import re
import threading

import pytest

from agentbase.core.redaction import (
    NullRedactionProvider,
    RedactionManager,
    RedactionRegistry,
    RedactionRule,
    RegexRedactionProvider,
    redaction_registry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def provider():
    """Create a RegexRedactionProvider with default rules."""
    return RegexRedactionProvider()


# ---------------------------------------------------------------------------
# RedactionRule data class
# ---------------------------------------------------------------------------

class TestRedactionRule:
    def test_to_dict(self):
        """to_dict should include all fields."""
        rule = RedactionRule(
            name="test",
            pattern=re.compile(r"\d+"),
            replacement="***",
            description="test rule",
        )
        d = rule.to_dict()
        assert d["name"] == "test"
        assert d["pattern"] == r"\d+"
        assert d["replacement"] == "***"
        assert d["enabled"] is True

    def test_defaults(self):
        rule = RedactionRule(name="x", pattern=re.compile(r"x"))
        assert rule.replacement == "***"
        assert rule.description == ""
        assert rule.enabled is True


# ---------------------------------------------------------------------------
# NullRedactionProvider
# ---------------------------------------------------------------------------

class TestNullRedactionProvider:
    def test_redact_passthrough(self):
        provider = NullRedactionProvider()
        assert provider.redact("hello world") == "hello world"

    def test_mask_passthrough(self):
        provider = NullRedactionProvider()
        assert provider.mask("secret", "api_key") == "secret"

    def test_list_rules_empty(self):
        provider = NullRedactionProvider()
        assert provider.list_rules() == []


# ---------------------------------------------------------------------------
# RegexRedactionProvider — PII type tests
# ---------------------------------------------------------------------------

class TestRegexRedactionPII:
    def test_email_redacted(self, provider):
        """Email addresses should be masked."""
        text = "Contact me at alice@example.com for details"
        result = provider.redact(text)
        assert "alice@example.com" not in result
        assert "***@***.***" in result

    def test_multiple_emails(self, provider):
        """Multiple emails in one text."""
        text = "alice@example.com and bob@test.io"
        result = provider.redact(text)
        assert "alice@example.com" not in result
        assert "bob@test.io" not in result

    def test_phone_cn_redacted(self, provider):
        """Chinese mobile phone numbers should be masked."""
        text = "Call me at 13800138000"
        result = provider.redact(text)
        assert "13800138000" not in result
        assert "***PHONE***" in result

    def test_phone_intl_redacted(self, provider):
        """International phone numbers should be masked."""
        text = "Call +86 13800138000"
        result = provider.redact(text)
        assert "13800138000" not in result

    def test_api_key_redacted(self, provider):
        """API keys should be masked."""
        text = "My key is sk-abc123def456ghi789jkl012mno345pqr"
        result = provider.redact(text)
        assert "sk-abc123def456ghi789jkl012mno345pqr" not in result

    def test_bearer_token_redacted(self, provider):
        """Bearer tokens should be masked."""
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.test"
        result = provider.redact(text)
        assert "eyJhbGciOiJIUzI1NiJ9" not in result
        assert "Bearer ***REDACTED***" in result

    def test_id_card_cn_redacted(self, provider):
        """Chinese ID card numbers should be masked."""
        text = "ID: 110101199001011234"
        result = provider.redact(text)
        assert "110101199001011234" not in result

    def test_credit_card_redacted(self, provider):
        """Credit card numbers should be masked."""
        text = "Card: 1234-5678-9012-3456"
        result = provider.redact(text)
        assert "1234-5678-9012-3456" not in result

    def test_ssn_redacted(self, provider):
        """US SSN should be masked."""
        text = "SSN: 123-45-6789"
        result = provider.redact(text)
        assert "123-45-6789" not in result


# ---------------------------------------------------------------------------
# RegexRedactionProvider — no false positives
# ---------------------------------------------------------------------------

class TestRegexRedactionNoFalsePositive:
    def test_normal_text_unchanged(self, provider):
        """Normal text without PII should be unchanged."""
        text = "The quick brown fox jumps over the lazy dog."
        assert provider.redact(text) == text

    def test_short_numbers_not_redacted(self, provider):
        """Short numbers should not be mistaken as phone numbers."""
        text = "There are 42 items in 3 categories."
        # Phone regex requires 1[3-9]xxxxxxxxx format, so 42 and 3 are safe
        result = provider.redact(text)
        assert "42" in result
        assert "3" in result

    def test_non_email_at_sign(self, provider):
        """'@' in non-email context should not be redacted."""
        text = "We met @ 3pm yesterday."
        result = provider.redact(text)
        assert "@ 3pm" in result

    def test_version_number_not_redacted(self, provider):
        """Version numbers should not be mistaken as credit cards."""
        text = "Version 1.2.3 is released."
        result = provider.redact(text)
        assert "1.2.3" in result

    def test_empty_text(self, provider):
        """Empty string should return empty string."""
        assert provider.redact("") == ""


# ---------------------------------------------------------------------------
# RegexRedactionProvider — rule management
# ---------------------------------------------------------------------------

class TestRegexRedactionRuleManagement:
    def test_list_rules(self, provider):
        """Should list all built-in rules."""
        rules = provider.list_rules()
        assert len(rules) > 0
        names = {r.name for r in rules}
        assert "email" in names
        assert "api_key" in names
        assert "phone_cn" in names

    def test_add_custom_rule(self, provider):
        """Custom rule should be added and applied."""
        custom = RedactionRule(
            name="iban",
            pattern=re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}"),
            replacement="***IBAN***",
            description="IBAN codes",
        )
        provider.add_rule(custom)
        text = "My IBAN is GB29NWBK60161331926819"
        result = provider.redact(text)
        assert "GB29NWBK60161331926819" not in result
        assert "***IBAN***" in result

    def test_remove_rule(self, provider):
        """Removing a rule should stop it from being applied."""
        assert provider.remove_rule("email") is True
        text = "Email: alice@example.com"
        result = provider.redact(text)
        assert "alice@example.com" in result  # not redacted anymore

    def test_remove_nonexistent_rule(self, provider):
        assert provider.remove_rule("nonexistent") is False

    def test_disable_rule(self, provider):
        """Disabling a rule should stop it from being applied."""
        assert provider.disable_rule("phone_cn") is True
        text = "Call 13800138000"
        result = provider.redact(text)
        assert "13800138000" in result

    def test_enable_rule(self, provider):
        """Enabling a disabled rule should reactivate it."""
        provider.disable_rule("email")
        provider.enable_rule("email")
        text = "Email: alice@example.com"
        result = provider.redact(text)
        assert "alice@example.com" not in result

    def test_disable_nonexistent_rule(self, provider):
        assert provider.disable_rule("nonexistent") is False


# ---------------------------------------------------------------------------
# RegexRedactionProvider — mask single value
# ---------------------------------------------------------------------------

class TestRegexRedactionMask:
    def test_mask_email(self, provider):
        result = provider.mask("alice@example.com", "email")
        assert "alice@example.com" not in result

    def test_mask_unknown_kind_fallback(self, provider):
        """Unknown kind should use generic mask."""
        result = provider.mask("secret_value", "nonexistent_kind")
        assert "secret_value" not in result
        assert "***" in result

    def test_mask_short_value(self, provider):
        """Short values should be fully masked."""
        result = provider.mask("ab", "nonexistent")
        assert result == "***"


# ---------------------------------------------------------------------------
# RegexRedactionProvider — concurrency
# ---------------------------------------------------------------------------

class TestRegexRedactionConcurrency:
    def test_concurrent_redact(self, provider):
        """Concurrent redact calls should be safe."""
        text = "Email: alice@example.com, Phone: 13800138000"
        results = []

        def worker():
            for _ in range(50):
                results.append(provider.redact(text))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 200
        for r in results:
            assert "alice@example.com" not in r
            assert "13800138000" not in r


# ---------------------------------------------------------------------------
# RedactionRegistry
# ---------------------------------------------------------------------------

class TestRedactionRegistry:
    def test_register_and_create(self):
        reg = RedactionRegistry()
        reg.register("test_reg", RegexRedactionProvider)
        assert reg.has("test_reg")
        provider = reg.create("test_reg")
        assert isinstance(provider, RegexRedactionProvider)

    def test_register_duplicate_raises(self):
        reg = RedactionRegistry()
        reg.register("dup", RegexRedactionProvider)
        with pytest.raises(Exception, match="already registered"):
            reg.register("dup", RegexRedactionProvider)

    def test_register_override(self):
        reg = RedactionRegistry()
        reg.register("over", RegexRedactionProvider)
        reg.register("over", RegexRedactionProvider, override=True)
        assert reg.has("over")

    def test_unregister(self):
        reg = RedactionRegistry()
        reg.register("tmp", RegexRedactionProvider)
        assert reg.unregister("tmp") is True
        assert not reg.has("tmp")
        assert reg.unregister("tmp") is False

    def test_create_unknown_raises(self):
        reg = RedactionRegistry()
        with pytest.raises(Exception, match="Unknown redaction provider"):
            reg.create("nonexistent")

    def test_count(self):
        reg = RedactionRegistry()
        assert reg.count == 0
        reg.register("a", RegexRedactionProvider)
        assert reg.count == 1

    def test_global_registry_has_defaults(self):
        assert redaction_registry.has("null")
        assert redaction_registry.has("regex")


# ---------------------------------------------------------------------------
# RedactionManager
# ---------------------------------------------------------------------------

class TestRedactionManager:
    def test_disabled_manager_passthrough(self):
        """When disabled, manager should pass text through unchanged."""
        mgr = RedactionManager(provider="regex", enabled=False)
        assert mgr.enabled is False
        text = "Email: alice@example.com"
        assert mgr.redact(text) == text

    def test_enabled_manager_redacts(self):
        """When enabled, manager should redact PII."""
        mgr = RedactionManager(provider="regex", enabled=True)
        text = "Email: alice@example.com"
        result = mgr.redact(text)
        assert "alice@example.com" not in result

    def test_manager_add_rule(self):
        """Manager should support adding custom rules."""
        mgr = RedactionManager(provider="regex", enabled=True)
        mgr.add_rule(RedactionRule(
            name="test_custom",
            pattern=re.compile(r"SECRET-\d+"),
            replacement="***SECRET***",
        ))
        result = mgr.redact("Found SECRET-12345 here")
        assert "SECRET-12345" not in result
        assert "***SECRET***" in result

    def test_manager_list_rules(self):
        mgr = RedactionManager(provider="regex", enabled=True)
        rules = mgr.list_rules()
        assert len(rules) > 0
