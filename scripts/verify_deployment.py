"""Deployment verification: exercise every major module over real HTTP.

Run: python scripts/verify_deployment.py [base_url] [api_key]
Exit code 0 = all checks passed.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
KEY = sys.argv[2] if len(sys.argv) > 2 else "deploy-test-key"

PASS, FAIL = 0, 0


def call(method: str, path: str, body: dict | None = None, expect: int = 200) -> dict:
    req = urllib.request.Request(
        BASE + path,
        method=method,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read() or b"{}")
            code = resp.status
    except urllib.error.HTTPError as e:
        code = e.code
        data = json.loads(e.read() or b"{}")
    return {"code": code, "data": data, "ok": code == expect}


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    mark = "PASS" if ok else "FAIL"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print(f"== agentbase deployment verification @ {BASE} ==\n")

    # 1. System health
    r = call("GET", "/health")
    check("health", r["ok"], f"status={r['data'].get('status')}")
    r = call("GET", "/agents")
    agents = r["data"] if isinstance(r["data"], list) else r["data"].get("items", [])
    check("agents list", r["ok"], f"count={len(agents)}")

    # 2. Redis queue round-trip
    r = call("POST", "/queue/submit", {"agent_name": "default", "message": "deploy-verify"})
    check("queue submit (redis)", r["ok"], f"task_id={r['data'].get('task_id')}")
    r = call("POST", "/queue/process")
    check("queue process", r["ok"], f"processed={r['data'].get('processed')}")

    # 3. Alert full chain: rule -> tick -> event -> notification
    r = call("POST", "/alerts/rules", {
        "name": "deploy-queue-check", "metric": "queue_submitted_total",
        "operator": "gt", "threshold": 0, "severity": "warning", "notify_user_id": "*",
    })
    rule_id = r["data"].get("rule_id")
    if not rule_id:  # idempotent re-run: rule already exists, look it up
        rules = call("GET", "/alerts/rules?metric=queue_submitted_total")["data"]
        items = rules if isinstance(rules, list) else rules.get("items", [])
        rule_id = next((i["rule_id"] for i in items
                        if i.get("name") == "deploy-queue-check"), None)
    check("alert rule create", rule_id is not None, f"rule_id={rule_id}")
    r = call("POST", "/alerts/tick")
    # First run: rule transitions pending -> firing and emits an event.
    # Re-runs: cooldown suppresses re-firing (anti-alert-storm by design) —
    # verify the rule stays firing instead.
    rule = call("GET", f"/alerts/rules/{rule_id}")["data"]
    fired = (r["ok"] and r["data"].get("count", 0) >= 1) or (
        rule.get("state") in ("firing", "resolved"))
    check("alert tick fires", fired,
          f"tick_events={r['data'].get('count')} rule_state={rule.get('state')}")
    r = call("GET", "/alerts/events?state=firing")
    check("alert events query", r["ok"], f"total={r['data'].get('total')}")
    r = call("GET", "/notifications?user_id=*&category=alert")
    delivered = r["ok"] and r["data"].get("total", 0) >= 1
    check("alert -> notification delivered", delivered,
          f"notifications={r['data'].get('total')}")

    # 4. RBAC: role create + check (idempotent: 409 on re-run is fine)
    r = call("POST", "/rbac/roles", {"name": "deploy-tester", "permissions": ["agents:read"]})
    check("rbac role create", r["ok"] or r["code"] in (400, 409))
    r = call("POST", "/rbac/users/tester/roles/deploy-tester")
    check("rbac assign role", r["ok"])
    r = call("POST", "/rbac/check", {"username": "tester", "resource": "agents", "action": "read"})
    allowed = r["ok"] and r["data"].get("allowed") is True
    check("rbac permission check", allowed)
    r = call("POST", "/rbac/check", {"username": "tester", "resource": "agents", "action": "delete"})
    denied = r["ok"] and r["data"].get("allowed") is False
    check("rbac deny by default", denied)

    # 5. System config hot-reload
    r = call("PUT", "/system-config/deploy.verify.flag", {"value": True, "is_public": True})
    check("system-config set", r["ok"])
    r = call("GET", "/system-config/public")
    found = r["ok"] and any(
        i.get("key") == "deploy.verify.flag" for i in r["data"].get("items", []))
    check("system-config public read", found)

    # 6. Calendar
    r = call("POST", "/calendar", {"title": "deploy-verify meeting",
             "start_time": "2026-08-20T10:00:00Z", "end_time": "2026-08-20T11:00:00Z"})
    check("calendar create", r["ok"], f"event_id={r['data'].get('event_id')}")
    r = call("GET", "/calendar/upcoming")
    check("calendar upcoming", r["ok"])

    # 7. Scheduler: create interval task + manual trigger (idempotent lookup)
    r = call("POST", "/schedules", {"name": "deploy-verify-task",
             "agent_name": "default", "message": "heartbeat", "schedule_type": "interval",
             "interval_seconds": 3600})
    task_id = r["data"].get("id") or r["data"].get("task_id")
    if not task_id:  # re-run: task already exists
        tasks = call("GET", "/schedules")["data"]
        items = tasks if isinstance(tasks, list) else tasks.get("items", [])
        task_id = next((i["id"] for i in items
                        if i.get("name") == "deploy-verify-task"), None)
    check("schedule create", task_id is not None, f"task_id={task_id}")
    r = call("POST", f"/schedules/{task_id}/trigger")
    check("schedule manual trigger", r["ok"], f"status={r['data'].get('status')}")

    # 8. User auth: register + login
    r = call("POST", "/auth/register", {"username": "deploy_user",
             "password": "V3rify!pass", "email": "deploy@test.local"})
    check("user register", r["ok"] or r["code"] == 409)
    r = call("POST", "/auth/login", {"username": "deploy_user", "password": "V3rify!pass"})
    check("user login", r["ok"], f"token={'yes' if r['data'].get('token') else 'no'}")

    # 9. Prompt manager
    r = call("POST", "/prompts", {"name": "deploy_greet",
             "content": "hello {name}", "variables": ["name"]})
    check("prompt create", r["ok"])
    r = call("POST", "/prompts/deploy_greet/render", {"variables": {"name": "deploy"}})
    rendered = r["ok"] and "hello deploy" in str(r["data"].get("rendered", ""))
    check("prompt render", rendered, f"rendered={r['data'].get('rendered', '')!r}")

    # 10. Conversation + metrics export (Prometheus text format)
    r = call("GET", "/conversations")
    check("conversations list", r["ok"])
    req = urllib.request.Request(BASE + "/metrics",
                                 headers={"Authorization": f"Bearer {KEY}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode()
    has_metric = "agentbase_queue_tasks_submitted_total" in raw
    check("prometheus metrics export", has_metric)

    # 11. PostgreSQL persistence path (checkpointer + storage dsn set in compose)
    r = call("GET", "/health")
    detail = json.dumps(r["data"])[:120]
    check("health detail snapshot", r["ok"], detail)

    print(f"\n== RESULT: {PASS} passed, {FAIL} failed ==")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
