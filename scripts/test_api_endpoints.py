"""Persistent test script: verify all API endpoints end-to-end.

Run: python scripts/test_api_endpoints.py
Requires: API server running on http://127.0.0.1:8000
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


# ── GET endpoints ──────────────────────────────────────────────────────

print("\n=== GET Endpoints ===")

r = requests.get(f"{BASE}/health")
test("GET /health", r.status_code == 200, f"status={r.json().get('status')}")

r = requests.get(f"{BASE}/metrics")
test("GET /metrics", r.status_code == 200 and "agentbase" in r.text, f"{len(r.text)} bytes")

r = requests.get(f"{BASE}/agents")
test("GET /agents", r.status_code == 200 and len(r.json()) >= 1, f"{len(r.json())} agents")

r = requests.get(f"{BASE}/agents/default")
test("GET /agents/default", r.status_code == 200, f"keys={list(r.json().keys())[:5]}")

r = requests.get(f"{BASE}/agents/default/configurable")
test("GET /agents/default/configurable", r.status_code == 200)

r = requests.get(f"{BASE}/docs")
test("GET /docs (Swagger)", r.status_code == 200)

r = requests.get(f"{BASE}/redoc")
test("GET /redoc", r.status_code == 200)

# ── Document upload + search ───────────────────────────────────────────

print("\n=== Documents ===")

# Upload a test file
test_file = "workspace/test_api_upload.txt"
import pathlib
pathlib.Path(test_file).parent.mkdir(parents=True, exist_ok=True)
pathlib.Path(test_file).write_text(
    "Python is a high-level programming language. "
    "It is widely used for web development, data science, and artificial intelligence.",
    encoding="utf-8",
)

with open(test_file, "rb") as f:
    r = requests.post(f"{BASE}/documents/upload", files={"file": f})
test("POST /documents/upload", r.status_code in (200, 201), f"status={r.status_code}")

r = requests.get(f"{BASE}/documents")
docs = r.json()
if isinstance(docs, dict):
    docs = docs.get("documents", docs.get("items", []))
test("GET /documents", r.status_code == 200 and isinstance(docs, list), f"{len(docs)} docs")

if docs and isinstance(docs, list):
    doc_id = docs[0].get("id") or docs[0].get("doc_id")
    r = requests.get(f"{BASE}/documents/{doc_id}")
    test("GET /documents/{id}", r.status_code == 200, f"doc_id={doc_id}")

    r = requests.delete(f"{BASE}/documents/{doc_id}")
    test("DELETE /documents/{id}", r.status_code in (200, 204), f"status={r.status_code}")
else:
    test("GET /documents/{id}", False, "no documents to test")
    test("DELETE /documents/{id}", False, "no documents to test")

# Search
r = requests.post(f"{BASE}/documents/search", json={"query": "Python", "top_k": 3})
test("POST /documents/search", r.status_code == 200, f"status={r.status_code}, results={len(r.json()) if r.status_code == 200 else 'N/A'}")

# ── Queue ──────────────────────────────────────────────────────────────

print("\n=== Queue ===")

r = requests.post(f"{BASE}/queue/submit", json={"agent_name": "default", "message": "test task"})
test("POST /queue/submit", r.status_code in (200, 201), f"task_id={r.json().get('id', 'N/A')[:8]}...")

task_id = r.json().get("id") if r.status_code in (200, 201) else None

if task_id:
    r = requests.get(f"{BASE}/queue/{task_id}")
    test("GET /queue/{task_id}", r.status_code == 200, f"status={r.json().get('status')}")

r = requests.get(f"{BASE}/queue")
test("GET /queue", r.status_code == 200, f"{len(r.json())} tasks")

# Cancel the task BEFORE processing (can't cancel after it's done)
if task_id:
    r = requests.delete(f"{BASE}/queue/{task_id}")
    test("DELETE /queue/{task_id}", r.status_code in (200, 204), f"status={r.status_code}")

r = requests.post(f"{BASE}/queue/process", json={"limit": 1})
test("POST /queue/process", r.status_code == 200, f"status={r.status_code}")

# ── Agent invoke (may fail due to API balance) ─────────────────────────

print("\n=== Agent Invoke ===")

r = requests.post(f"{BASE}/agents/default/invoke", json={"message": "Say hello"}, timeout=30)
test("POST /agents/default/invoke", r.status_code == 200, f"status={r.status_code}")

# ── Summary ───────────────────────────────────────────────────────────

passed = sum(1 for _, s in results if s == PASS)
failed = sum(1 for _, s in results if s == FAIL)

print(f"\n{'=' * 50}")
print(f"  API Endpoint Tests: {passed} passed, {failed} failed / {len(results)}")
print(f"{'=' * 50}")
sys.exit(0 if failed == 0 else 1)
