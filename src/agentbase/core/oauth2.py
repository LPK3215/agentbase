"""OAuth2 third-party login service — Google / GitHub authorization code flow.

Provides OAuth2 authorization code flow for third-party login:
- Google: ``GET /auth/oauth2/google/authorize`` → callback
- GitHub: ``GET /auth/oauth2/github/authorize`` → callback

After successful authorization, the user info is fetched from the
provider and either:
1. Matched to an existing user by email (in ``UserManager``)
2. Auto-registered as a new user

A JWT token is then issued for subsequent API authentication.

Config::

    oauth2:
      enabled: true
      providers:
        google:
          client_id: "xxx.apps.googleusercontent.com"
          client_secret: "${GOOGLE_OAUTH_SECRET}"
          redirect_uri: "http://localhost:8000/auth/oauth2/google/callback"
          scopes: ["openid", "email", "profile"]
          default_roles: ["user"]
        github:
          client_id: "Iv1.xxx"
          client_secret: "${GITHUB_OAUTH_SECRET}"
          redirect_uri: "http://localhost:8000/auth/oauth2/github/callback"
          scopes: ["user:email"]
          default_roles: ["user"]

Usage::

    from agentbase.core.oauth2 import OAuth2Manager

    mgr = OAuth2Manager(
        providers={
            "google": OAuth2ProviderConfig(
                name="google",
                client_id="xxx",
                client_secret="yyy",
                redirect_uri="http://localhost:8000/auth/oauth2/google/callback",
            ),
        },
        enabled=True,
    )
    url = mgr.get_authorize_url("google", state="random-state")
    token_data = mgr.exchange_code("google", code="auth-code")
    user_info = mgr.get_user_info("google", access_token=token_data["access_token"])
"""
from __future__ import annotations

import json
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agentbase.runtime.errors import AgentbaseError, ErrorCode
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------

@dataclass
class OAuth2ProviderConfig:
    """Configuration for a single OAuth2 provider.

    Attributes:
        name: Provider name (``"google"``, ``"github"``).
        client_id: OAuth2 client ID from the provider.
        client_secret: OAuth2 client secret.
        redirect_uri: Callback URL registered with the provider.
        scopes: List of OAuth2 scopes to request.
        default_roles: Roles assigned to auto-registered users.
    """

    name: str
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    scopes: list[str] = field(default_factory=list)
    default_roles: list[str] = field(default_factory=lambda: ["user"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "client_id": self.client_id,
            "client_secret": "***" if self.client_secret else "",
            "redirect_uri": self.redirect_uri,
            "scopes": self.scopes,
            "default_roles": self.default_roles,
        }


# ---------------------------------------------------------------------------
# User info from provider
# ---------------------------------------------------------------------------

@dataclass
class OAuth2UserInfo:
    """Normalized user info from an OAuth2 provider.

    Attributes:
        provider: Provider name (``"google"`` / ``"github"``).
        provider_user_id: User ID at the provider.
        email: User email (may be empty if scope not granted).
        name: Display name.
        avatar_url: URL to user avatar picture.
        raw: Raw user info dict from the provider.
    """

    provider: str
    provider_user_id: str
    email: str = ""
    name: str = ""
    avatar_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "provider_user_id": self.provider_user_id,
            "email": self.email,
            "name": self.name,
            "avatar_url": self.avatar_url,
            "raw": self.raw,
        }


# ---------------------------------------------------------------------------
# Provider Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class OAuth2Provider(Protocol):
    """Protocol for OAuth2 providers.

    Each provider implements the authorization code flow:
    1. ``get_authorize_url()`` — build the provider's authorize URL
    2. ``exchange_code()`` — exchange the auth code for an access token
    3. ``get_user_info()`` — fetch user info with the access token
    """

    @property
    def name(self) -> str:
        ...

    def get_authorize_url(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        scopes: list[str],
        state: str,
    ) -> str:
        """Build the provider's authorization URL."""
        ...

    def exchange_code(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        code: str,
    ) -> dict[str, Any]:
        """Exchange the authorization code for an access token.

        Returns a dict with at least ``access_token``.
        """
        ...

    def get_user_info(
        self,
        *,
        access_token: str,
    ) -> OAuth2UserInfo:
        """Fetch user info using the access token."""
        ...


# ---------------------------------------------------------------------------
# HTTP helper (lazy import to avoid hard dependency)
# ---------------------------------------------------------------------------

def _http_post_json(
    url: str,
    *,
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 10,
) -> dict[str, Any]:
    """POST to a URL and return JSON response.

    Uses ``urllib`` from the standard library to avoid requiring ``httpx``
    or ``requests`` for the OAuth2 flow.  In production, you may want to
    use a proper HTTP client for connection pooling.
    """
    import urllib.request
    import urllib.error

    body = urllib.parse.urlencode(data or {}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read().decode("utf-8")
            if "json" in content_type:
                return json.loads(raw)
            # Some providers return urlencoded data
            return dict(urllib.parse.parse_qs(raw))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8") if exc.fp else ""
        logger.error("OAuth2 HTTP error %s: %s", exc.code, error_body)
        raise AgentbaseError(
            f"OAuth2 token exchange failed: HTTP {exc.code}",
            code=ErrorCode.AUTH_INVALID_TOKEN,
        ) from exc
    except urllib.error.URLError as exc:
        logger.error("OAuth2 connection error: %s", exc)
        raise AgentbaseError(
            f"OAuth2 connection failed: {exc}",
            code=ErrorCode.AUTH_INVALID_TOKEN,
        ) from exc


def _http_get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 10,
) -> dict[str, Any]:
    """GET a URL and return JSON response."""
    import urllib.request
    import urllib.error

    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8") if exc.fp else ""
        logger.error("OAuth2 HTTP error %s: %s", exc.code, error_body)
        raise AgentbaseError(
            f"OAuth2 user info fetch failed: HTTP {exc.code}",
            code=ErrorCode.AUTH_INVALID_TOKEN,
        ) from exc
    except urllib.error.URLError as exc:
        logger.error("OAuth2 connection error: %s", exc)
        raise AgentbaseError(
            f"OAuth2 connection failed: {exc}",
            code=ErrorCode.AUTH_INVALID_TOKEN,
        ) from exc


# ---------------------------------------------------------------------------
# Google OAuth2 provider
# ---------------------------------------------------------------------------

class GoogleOAuth2Provider:
    """Google OAuth2 provider (authorization code flow).

    Endpoints:
    - Authorize: ``https://accounts.google.com/o/oauth2/v2/auth``
    - Token:     ``https://oauth2.googleapis.com/token``
    - UserInfo:  ``https://www.googleapis.com/oauth2/v2/userinfo``
    """

    AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

    @property
    def name(self) -> str:
        return "google"

    def get_authorize_url(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        scopes: list[str],
        state: str,
    ) -> str:
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes) if scopes else "openid email profile",
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{self.AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        code: str,
    ) -> dict[str, Any]:
        data = {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        return _http_post_json(self.TOKEN_URL, data=data)

    def get_user_info(
        self,
        *,
        access_token: str,
    ) -> OAuth2UserInfo:
        data = _http_get_json(
            self.USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        return OAuth2UserInfo(
            provider="google",
            provider_user_id=str(data.get("id", "")),
            email=data.get("email", ""),
            name=data.get("name", ""),
            avatar_url=data.get("picture", ""),
            raw=data,
        )


# ---------------------------------------------------------------------------
# GitHub OAuth2 provider
# ---------------------------------------------------------------------------

class GitHubOAuth2Provider:
    """GitHub OAuth2 provider (authorization code flow).

    Endpoints:
    - Authorize: ``https://github.com/login/oauth/authorize``
    - Token:     ``https://github.com/login/oauth/access_token``
    - UserInfo:  ``https://api.github.com/user``
    """

    AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
    TOKEN_URL = "https://github.com/login/oauth/access_token"
    USERINFO_URL = "https://api.github.com/user"
    EMAILS_URL = "https://api.github.com/user/emails"

    @property
    def name(self) -> str:
        return "github"

    def get_authorize_url(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        scopes: list[str],
        state: str,
    ) -> str:
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes) if scopes else "user:email",
            "state": state,
        }
        return f"{self.AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        code: str,
    ) -> dict[str, Any]:
        data = {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        }
        return _http_post_json(
            self.TOKEN_URL,
            data=data,
            headers={"Accept": "application/json"},
        )

    def get_user_info(
        self,
        *,
        access_token: str,
    ) -> OAuth2UserInfo:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
        }
        data = _http_get_json(self.USERINFO_URL, headers=headers)

        # GitHub requires a separate API call for emails (if scope granted)
        email = data.get("email") or ""
        if not email:
            try:
                emails = _http_get_json(self.EMAILS_URL, headers=headers)
                if isinstance(emails, list):
                    for e in emails:
                        if e.get("primary"):
                            email = e.get("email", "")
                            break
            except AgentbaseError:
                pass  # Email scope may not have been granted

        return OAuth2UserInfo(
            provider="github",
            provider_user_id=str(data.get("id", "")),
            email=email,
            name=data.get("name") or data.get("login", ""),
            avatar_url=data.get("avatar_url", ""),
            raw=data,
        )


# ---------------------------------------------------------------------------
# Null provider (disabled)
# ---------------------------------------------------------------------------

class NullOAuth2Provider:
    """No-op OAuth2 provider — used when OAuth2 is disabled."""

    @property
    def name(self) -> str:
        return "null"

    def get_authorize_url(self, **kwargs: Any) -> str:
        return ""

    def exchange_code(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    def get_user_info(self, **kwargs: Any) -> OAuth2UserInfo:
        return OAuth2UserInfo(provider="null", provider_user_id="")


# ---------------------------------------------------------------------------
# State store (CSRF protection)
# ---------------------------------------------------------------------------

class StateStore:
    """In-memory store for OAuth2 state parameters (CSRF protection).

    Each state is a random token that is:
    1. Generated before redirecting to the provider
    2. Validated when the callback comes back
    3. Expired after 10 minutes
    """

    def __init__(self, *, max_age_seconds: int = 600) -> None:
        self._states: dict[str, float] = {}
        self._lock = threading.Lock()
        self._max_age = max_age_seconds

    def generate(self) -> str:
        """Generate and store a new state token."""
        state = secrets.token_urlsafe(32)
        with self._lock:
            self._cleanup()
            self._states[state] = time.time()
        return state

    def validate(self, state: str) -> bool:
        """Validate and consume a state token (one-time use)."""
        if not state:
            return False
        with self._lock:
            self._cleanup()
            created = self._states.pop(state, None)
            if created is None:
                return False
            if time.time() - created > self._max_age:
                return False
            return True

    def _cleanup(self) -> None:
        """Remove expired states."""
        now = time.time()
        expired = [s for s, t in self._states.items() if now - t > self._max_age]
        for s in expired:
            self._states.pop(s, None)


# ---------------------------------------------------------------------------
# OAuth2Manager — high-level facade
# ---------------------------------------------------------------------------

class OAuth2Manager:
    """High-level OAuth2 management service.

    Wraps OAuth2 providers and provides:
    - Authorization URL generation
    - Code-to-token exchange
    - User info retrieval
    - State management (CSRF protection)

    When ``enabled=False``, all operations return empty/None.
    """

    def __init__(
        self,
        *,
        providers: dict[str, OAuth2ProviderConfig] | None = None,
        enabled: bool = False,
    ) -> None:
        self._enabled = enabled
        self._providers: dict[str, OAuth2ProviderConfig] = providers or {}
        self._provider_impls: dict[str, OAuth2Provider] = {
            "google": GoogleOAuth2Provider(),
            "github": GitHubOAuth2Provider(),
        }
        self._state_store = StateStore()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def providers(self) -> dict[str, OAuth2ProviderConfig]:
        return self._providers

    def has_provider(self, name: str) -> bool:
        """Check if a provider is configured."""
        return name.strip().lower() in self._providers

    def get_provider_config(self, name: str) -> OAuth2ProviderConfig:
        """Get provider configuration by name."""
        key = name.strip().lower()
        if key not in self._providers:
            raise AgentbaseError(
                f"OAuth2 provider not configured: {key}",
                code=ErrorCode.CONFIG_INVALID,
            )
        return self._providers[key]

    def generate_state(self) -> str:
        """Generate a state token for CSRF protection."""
        return self._state_store.generate()

    def validate_state(self, state: str) -> bool:
        """Validate a state token (one-time use)."""
        return self._state_store.validate(state)

    def get_authorize_url(self, provider: str, *, state: str) -> str:
        """Build the authorization URL for the given provider."""
        if not self._enabled:
            return ""
        key = provider.strip().lower()
        cfg = self.get_provider_config(key)
        impl = self._provider_impls.get(key)
        if impl is None:
            raise AgentbaseError(
                f"Unknown OAuth2 provider: {key}",
                code=ErrorCode.CONFIG_INVALID,
            )
        return impl.get_authorize_url(
            client_id=cfg.client_id,
            redirect_uri=cfg.redirect_uri,
            scopes=cfg.scopes,
            state=state,
        )

    def exchange_code(self, provider: str, *, code: str) -> dict[str, Any]:
        """Exchange authorization code for access token."""
        if not self._enabled:
            return {}
        key = provider.strip().lower()
        cfg = self.get_provider_config(key)
        impl = self._provider_impls.get(key)
        if impl is None:
            raise AgentbaseError(
                f"Unknown OAuth2 provider: {key}",
                code=ErrorCode.CONFIG_INVALID,
            )
        return impl.exchange_code(
            client_id=cfg.client_id,
            client_secret=cfg.client_secret,
            redirect_uri=cfg.redirect_uri,
            code=code,
        )

    def get_user_info(self, provider: str, *, access_token: str) -> OAuth2UserInfo:
        """Fetch user info from the provider using the access token."""
        if not self._enabled:
            return OAuth2UserInfo(provider=provider, provider_user_id="")
        key = provider.strip().lower()
        impl = self._provider_impls.get(key)
        if impl is None:
            raise AgentbaseError(
                f"Unknown OAuth2 provider: {key}",
                code=ErrorCode.CONFIG_INVALID,
            )
        return impl.get_user_info(access_token=access_token)

    def list_providers(self) -> list[str]:
        """List configured provider names."""
        return sorted(self._providers.keys())


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_oauth2_manager: OAuth2Manager | None = None
_oauth2_manager_lock = threading.Lock()


def get_oauth2_manager() -> OAuth2Manager | None:
    """Get the global OAuth2 manager singleton (may be None if not initialized)."""
    return _oauth2_manager


def set_oauth2_manager(mgr: OAuth2Manager) -> None:
    """Set the global OAuth2 manager singleton."""
    global _oauth2_manager
    _oauth2_manager = mgr


def reset_oauth2_manager() -> None:
    """Reset the global OAuth2 manager singleton (for testing)."""
    global _oauth2_manager
    _oauth2_manager = None
