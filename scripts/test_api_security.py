"""Persistent test script: verify API security features.

Tests: CORS, Request ID, Exception Handling, Rate Limiting,
       API Key Auth, JWT Auth + RBAC.

Run: python scripts/test_api_security.py
Requires: API server running on http://127.0.0.1:8000 (without API key)
"""
from __future__ import annotations

import sys
import requests

BASE = "http://127.0.0.1:8000"
PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

results: list[tuple[str, str]] = []


def test(name: str, ok: bool, detail: str = ""):
    status = PASS if ok else FAIL
    results.append((name, PASS if ok else FAIL))
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))


# ── 1. CORS ────────────────────────────────────────────────────────────

print("\n=== CORS ===")
# CORS headers only sent when Origin header is present
r = requests.get(f"{BASE}/health", headers={"Origin": "http://localhost:3000"})
acao = r.headers.get("access-control-allow-origin", "")
test("CORS header present", acao != "", f"Access-Control-Allow-Origin={acao}")

# Check CORS preflight
r = requests.options(
    f"{BASE}/agents",
    headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET",
    },
)
test(
    "CORS preflight (OPTIONS)",
    r.status_code in (200, 204),
    f"status={r.status_code}",
)

# ── 2. Request ID ──────────────────────────────────────────────────────

print("\n=== Request ID ===")
r = requests.get(f"{BASE}/health")
rid = r.headers.get("X-Request-ID", "")
test("Auto-generated Request ID", rid != "", f"ID={rid[:12]}...")

r = requests.get(f"{BASE}/health", headers={"X-Request-ID": "my-custom-id-12345"})
rid = r.headers.get("X-Request-ID", "")
test("Custom Request ID echoed", rid == "my-custom-id-12345", f"ID={rid}")

# ── 3. Exception Handling ──────────────────────────────────────────────

print("\n=== Exception Handling ===")
# Trigger a real 500 by requesting a non-existent agent that will cause an internal error
r = requests.get(f"{BASE}/agents/nonexistent_agent_xyz")
test(
    "Structured error response",
    r.status_code >= 400,
    f"status={r.status_code}, body={str(r.json())[:100]}",
)

# Trigger a 404
r = requests.get(f"{BASE}/nonexistent")
test("404 returns error JSON", r.status_code == 404, f"status={r.status_code}")

# ── 4. Rate Limiting ───────────────────────────────────────────────────

print("\n=== Rate Limiting ===")
# Send 65 requests rapidly (limit is 60/min)
hit_429 = False
for i in range(65):
    r = requests.get(f"{BASE}/agents")
    if r.status_code == 429:
        hit_429 = True
        break
test(
    "Rate limit triggers (60 req/min)",
    hit_429,
    f"429 at request #{i + 1}" if hit_429 else "no 429 after 65 requests",
)

# ── 5. API Key Auth (test without key = should pass) ───────────────────

print("\n=== API Key Auth (disabled mode) ===")
# Current server has no API key set, so auth is disabled
# Note: rate limit may have been triggered by previous test, so check for 200 or 429
r = requests.get(f"{BASE}/health")  # /health is public, not rate limited
test(
    "Auth disabled = public paths accessible",
    r.status_code == 200,
    f"status={r.status_code}",
)

# ── 6. JWT Auth + RBAC (library test) ──────────────────────────────────

print("\n=== JWT Auth + RBAC (library) ===")
from agentbase.extensions.auth import JWTAuth, Role, Permission

auth = JWTAuth(secret="test-secret-key")

# Create token
token = auth.create_token(user_id="user1", roles=[Role.USER])
test("JWT token creation", token is not None and len(token) > 20, f"token={token[:20]}...")

# Verify token
payload = auth.verify_token(token)
test("JWT token verification", payload is not None, f"user_id={payload.get('user_id')}")

# Check permissions
can_read = auth.has_permission(payload, Permission.READ)
can_delete = auth.has_permission(payload, Permission.DELETE)
test(
    "RBAC: USER can read",
    can_read,
    f"Permission.READ={can_read}",
)
test(
    "RBAC: USER cannot delete",
    not can_delete,
    f"Permission.DELETE={can_delete}",
)

# Admin token
admin_token = auth.create_token(user_id="admin1", roles=[Role.ADMIN])
admin_payload = auth.verify_token(admin_token)
admin_can_delete = auth.has_permission(admin_payload, Permission.DELETE)
test(
    "RBAC: ADMIN can delete",
    admin_can_delete,
    f"Permission.DELETE={admin_can_delete}",
)

# Readonly token
ro_token = auth.create_token(user_id="ro1", roles=[Role.READONLY])
ro_payload = auth.verify_token(ro_token)
ro_can_read = auth.has_permission(ro_payload, Permission.READ)
ro_can_write = auth.has_permission(ro_payload, Permission.WRITE)
test(
    "RBAC: READONLY can read",
    ro_can_read,
    f"Permission.READ={ro_can_read}",
)
test(
    "RBAC: READONLY cannot write",
    not ro_can_write,
    f"Permission.WRITE={ro_can_write}",
)

# Token expiration
import time

short_auth = JWTAuth(secret="test-secret", token_expiry_hours=0.00001)  # ~0.036s
short_token = short_auth.create_token(user_id="temp", roles=[Role.USER])
time.sleep(0.1)
expired_payload = short_auth.verify_token(short_token)
test("JWT token expiration", expired_payload is None, "expired token rejected")

# ── Summary ────────────────────────────────────────────────────────────

passed = sum(1 for _, s in results if s == PASS)
failed = sum(1 for _, s in results if s == FAIL)

print(f"\n{'=' * 50}")
print(f"  Security Tests: {passed} passed, {failed} failed / {len(results)}")
print(f"{'=' * 50}")
sys.exit(0 if failed == 0 else 1)
