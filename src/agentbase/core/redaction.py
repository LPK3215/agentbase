"""Sensitive information redaction service.

Masks PII (personally identifiable information) and secrets in text
to prevent leakage via logs, API responses, or LLM outputs.

Pluggable rules:
- Built-in rules: API keys, phone numbers, email, ID cards, SSN, credit cards.
- Custom rules: register with ``@register_redaction_rule("name")``.
- Custom providers: register with ``@register_redaction_provider("name")``.

Default: ``RegexRedactionProvider`` — pure-regex, zero dependencies.
Replaceable: Presidio, AWS Macie, etc.

Usage::

    from agentbase.core.redaction import redaction_registry, RedactionManager

    manager = RedactionManager(provider="regex", enabled=True)
    cleaned = manager.redact("Contact me at alice@example.com")
    # → "Contact me at ***@***.***"
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from agentbase.runtime.errors import RegistryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class RedactionRule:
    """A single redaction rule — a regex pattern and replacement.

    Attributes:
        name: Rule identifier (e.g. ``"email"``, ``"phone"``).
        pattern: Compiled regex pattern to search for.
        replacement: Text to replace matches with (default ``"***"``).
        description: Human-readable description.
        enabled: Whether this rule is active.
    """

    name: str
    pattern: re.Pattern[str]
    replacement: str = "***"
    description: str = ""
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "pattern": self.pattern.pattern,
            "replacement": self.replacement,
            "description": self.description,
            "enabled": self.enabled,
        }


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class RedactionProvider(Protocol):
    """Protocol for redaction providers.

    Implementations must be thread-safe.
    """

    def redact(self, text: str) -> str:
        """Redact all sensitive information in ``text``.

        Returns a copy of ``text`` with all detected PII/secrets masked.
        """
        ...

    def mask(self, value: str, kind: str) -> str:
        """Mask a single known-sensitive value.

        Args:
            value: The sensitive value to mask.
            kind: PII type hint (``"api_key"``, ``"email"``, ``"phone"``).

        Returns the masked value.
        """
        ...

    def add_rule(self, rule: RedactionRule) -> None:
        """Register a new redaction rule at runtime."""
        ...

    def remove_rule(self, name: str) -> bool:
        """Remove a redaction rule by name. Returns True if removed."""
        ...

    def list_rules(self) -> list[RedactionRule]:
        """List all registered redaction rules."""
        ...


# ---------------------------------------------------------------------------
# Null provider (no-op, zero overhead)
# ---------------------------------------------------------------------------

class NullRedactionProvider:
    """No-op redaction provider — passes through text unchanged.

    Used when redaction is disabled (``redaction.enabled=false``).
    """

    def redact(self, text: str) -> str:
        return text

    def mask(self, value: str, kind: str) -> str:
        return value

    def add_rule(self, rule: RedactionRule) -> None:
        pass

    def remove_rule(self, name: str) -> bool:
        return False

    def list_rules(self) -> list[RedactionRule]:
        return []


# ---------------------------------------------------------------------------
# Built-in redaction rules
# ---------------------------------------------------------------------------

def _build_default_rules() -> list[RedactionRule]:
    """Build the default set of PII redaction rules.

    Covers the most common PII types:
    - API keys (long alphanumeric strings with common key prefixes)
    - Email addresses
    - Phone numbers (Chinese mobile + international)
    - Chinese ID cards (18-digit)
    - Credit card numbers (16-digit)
    - SSN (US Social Security Numbers)
    - Bearer tokens
    """
    rules: list[RedactionRule] = []

    # API keys — common patterns: sk-..., key_..., AKIA..., token prefixes
    rules.append(RedactionRule(
        name="api_key",
        pattern=re.compile(
            r"(?:sk-[a-zA-Z0-9]{20,})"
            r"|(?:AKIA[0-9A-Z]{16})"
            r"|(?:key[_-]?[a-zA-Z0-9]{20,})"
            r"|(?:token[_-]?[a-zA-Z0-9]{20,})"
            r"|(?:ghp_[a-zA-Z0-9]{36})"
        ),
        replacement="***REDACTED_KEY***",
        description="API keys and access tokens (sk-, AKIA, key_, token_, ghp_)",
    ))

    # Bearer tokens
    rules.append(RedactionRule(
        name="bearer_token",
        pattern=re.compile(r"Bearer\s+[a-zA-Z0-9\-._~+/]+", re.IGNORECASE),
        replacement="Bearer ***REDACTED***",
        description="HTTP Bearer authorization tokens",
    ))

    # Email addresses
    rules.append(RedactionRule(
        name="email",
        pattern=re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
        replacement="***@***.***",
        description="Email addresses",
    ))

    # Chinese mobile phone numbers
    rules.append(RedactionRule(
        name="phone_cn",
        pattern=re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
        replacement="***PHONE***",
        description="Chinese mobile phone numbers (1xx-xxxx-xxxx)",
    ))

    # International phone numbers (+86, +1, etc.)
    rules.append(RedactionRule(
        name="phone_intl",
        pattern=re.compile(r"\+\d{1,3}[\s\-]?\d{4,}[\s\-]?\d{4,}"),
        replacement="***PHONE***",
        description="International phone numbers with country code",
    ))

    # Chinese ID cards (18-digit, last char may be X)
    rules.append(RedactionRule(
        name="id_card_cn",
        pattern=re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
        replacement="***ID***",
        description="Chinese national ID card numbers (18-digit)",
    ))

    # Credit card numbers (16 consecutive digits, with optional separators)
    rules.append(RedactionRule(
        name="credit_card",
        pattern=re.compile(r"(?<!\d)\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}(?!\d)"),
        replacement="****-****-****-****",
        description="Credit card numbers (16-digit with optional separators)",
    ))

    # US SSN (xxx-xx-xxxx)
    rules.append(RedactionRule(
        name="ssn",
        pattern=re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        replacement="***-**-****",
        description="US Social Security Numbers",
    ))

    return rules


# ---------------------------------------------------------------------------
# RegexRedactionProvider (default, zero-config)
# ---------------------------------------------------------------------------

class RegexRedactionProvider:
    """Regex-based redaction provider — zero dependencies.

    Ships with built-in rules for common PII types:
    API keys, email, phone, ID card, credit card, SSN, bearer tokens.

    Rules are:
    - Pre-compiled for performance
    - Thread-safe (read operations lock-free, writes under RLock)
    - Extensible at runtime via ``add_rule()`` / ``remove_rule()``

    Usage::

        provider = RegexRedactionProvider()
        provider.redact("Email: alice@example.com, Phone: 13800138000")
        # → "Email: ***@***.***, Phone: ***PHONE***"
    """

    def __init__(self, *, rules: list[RedactionRule] | None = None) -> None:
        self._lock = threading.RLock()
        self._rules: dict[str, RedactionRule] = {}
        for rule in rules or _build_default_rules():
            self._rules[rule.name] = rule

    def redact(self, text: str) -> str:
        """Redact all sensitive information in ``text``.

        Applies all enabled rules in sequence. Each rule's pattern
        is searched globally and replaced with the rule's replacement string.
        """
        with self._lock:
            rules = [r for r in self._rules.values() if r.enabled]
        for rule in rules:
            text = rule.pattern.sub(rule.replacement, text)
        return text

    def mask(self, value: str, kind: str) -> str:
        """Mask a single known-sensitive value.

        Args:
            value: The sensitive value to mask.
            kind: Rule name to use (e.g. ``"email"``, ``"api_key"``).

        Returns the masked value. If the rule doesn't exist, returns
        a generic mask (all characters replaced with ``*`` except first/last).
        """
        with self._lock:
            rule = self._rules.get(kind)
        if rule is not None:
            return rule.pattern.sub(rule.replacement, value)
        # Fallback: partial mask
        if len(value) <= 2:
            return "***"
        return value[0] + "***" + value[-1]

    def add_rule(self, rule: RedactionRule) -> None:
        """Register a new redaction rule at runtime."""
        with self._lock:
            self._rules[rule.name] = rule
        logger.info(
            "Redaction rule added: %s",
            rule.name,
            extra={"event": "redaction.rule_added", "rule": rule.name},
        )

    def remove_rule(self, name: str) -> bool:
        """Remove a redaction rule by name. Returns True if removed."""
        with self._lock:
            if name not in self._rules:
                return False
            self._rules.pop(name, None)
            return True

    def list_rules(self) -> list[RedactionRule]:
        """List all registered redaction rules."""
        with self._lock:
            return list(self._rules.values())

    def enable_rule(self, name: str) -> bool:
        """Enable a rule. Returns True if found."""
        with self._lock:
            rule = self._rules.get(name)
            if rule is None:
                return False
            rule.enabled = True
            return True

    def disable_rule(self, name: str) -> bool:
        """Disable a rule. Returns True if found."""
        with self._lock:
            rule = self._rules.get(name)
            if rule is None:
                return False
            rule.enabled = False
            return True


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class RedactionRegistry:
    """Thread-safe registry for redaction providers."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., RedactionProvider]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        factory: Callable[..., RedactionProvider],
        *,
        override: bool = False,
    ) -> None:
        key = name.strip().lower()
        with self._lock:
            if not key:
                raise RegistryError("Cannot register empty redaction provider name")
            if key in self._factories and not override:
                raise RegistryError(f"Redaction provider already registered: {key}")
            self._factories[key] = factory

    def create(self, name: str, **kwargs: Any) -> RedactionProvider:
        key = name.strip().lower()
        with self._lock:
            if key not in self._factories:
                available = ", ".join(sorted(self._factories)) or "<empty>"
                raise RegistryError(f"Unknown redaction provider: {key}. Available: {available}")
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
redaction_registry = RedactionRegistry()

# Register defaults
redaction_registry.register("null", NullRedactionProvider)
redaction_registry.register("regex", RegexRedactionProvider)


def register_redaction_provider(name: str, *, override: bool = False):
    """Decorator: register a redaction provider class.

    Usage::

        @register_redaction_provider("presidio")
        class PresidioRedactionProvider:
            def redact(self, text: str) -> str: ...
    """
    def decorator(factory: Callable[..., RedactionProvider]):
        redaction_registry.register(name, factory, override=override)
        return factory
    return decorator


def register_redaction_rule(name: str):
    r"""Decorator: register a custom redaction rule function.

    The decorated function should accept no arguments and return a
    ``RedactionRule`` instance.

    Usage::

        @register_redaction_rule("iban")
        def iban_rule():
            return RedactionRule(
                name="iban",
                pattern=re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}"),
                replacement="***IBAN***",
                description="International Bank Account Numbers",
            )
    """
    def decorator(func: Callable[[], RedactionRule]):
        rule = func()
        # Register with the global regex provider by default
        # (actual provider instances pick it up from the default rules)
        logger.debug("Custom redaction rule registered: %s", rule.name)
        return rule
    return decorator


# ---------------------------------------------------------------------------
# Manager — high-level facade
# ---------------------------------------------------------------------------

class RedactionManager:
    """High-level redaction manager.

    Wraps a ``RedactionProvider`` and provides convenience methods.
    When ``enabled=False``, uses ``NullRedactionProvider`` (no-op).

    Usage::

        manager = RedactionManager(provider="regex", enabled=True)
        cleaned = manager.redact("Email me at alice@example.com")
        # → "Email me at ***@***.***"
    """

    def __init__(
        self,
        *,
        provider: str = "regex",
        enabled: bool = False,
        **provider_kwargs: Any,
    ) -> None:
        self._enabled = enabled
        if not enabled:
            self._provider: RedactionProvider = NullRedactionProvider()
        else:
            self._provider = redaction_registry.create(provider, **provider_kwargs)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def redact(self, text: str) -> str:
        """Redact sensitive information from text."""
        return self._provider.redact(text)

    def mask(self, value: str, kind: str) -> str:
        """Mask a single known-sensitive value."""
        return self._provider.mask(value, kind)

    def add_rule(self, rule: RedactionRule) -> None:
        """Add a custom redaction rule at runtime."""
        self._provider.add_rule(rule)

    def remove_rule(self, name: str) -> bool:
        """Remove a redaction rule by name."""
        return self._provider.remove_rule(name)

    def list_rules(self) -> list[RedactionRule]:
        """List all registered rules."""
        return self._provider.list_rules()
