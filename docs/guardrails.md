# Guardrails, HITL, eval, and reflection hooks

> **AgentBase** — mapping of scaffold hooks to the usual before-tool / after-output / eval / on-failure extension points. Mechanisms exist; most are **default-off**. This document is the documented example that secondary developers copy, not a claim that the default agent is production-gated.

**Documentation index:** [README](../README.md) · [SECURITY](../SECURITY.md) · [Quick Start](quickstart.md) · [Configuration](configuration.md) · [Extensions](extensions.md) · [Backend Boundaries](backend-boundaries.md)

Existing APIs do **not** need to be renamed to `beforeToolExecute` / `runEval` / `onFailure`. The names below are the real ones.

---

## HITL trigger table (interrupt_on)

| Action class | Typical tools | Default `default` agent | Recommended production |
|--------------|---------------|-------------------------|------------------------|
| Workspace write | `write_file` | **no interrupt** (`interrupt_on: {}`) | `interrupt_on.tool_call: [write_file]` (see `configs/agents/interrupt_demo.yaml`) |
| Delete | `skill_delete`, `memory_delete`, `kb_delete` | **no interrupt**; tools are on the default list | interrupt or drop from the profile; or use `readonly` |
| Outbound | `email_sender`, `web_fetch` (side effects vary) | `email_sender` not on default; `web_fetch` is | interrupt email; consider allowlists for fetch |
| Code execution | `code_execute` | not on default | interrupt + never treat subprocess sandbox as a jail |
| Read | `read_file`, `grep`, `kb_search`, `memory_get`, … | allowed | keep; optionally use `configs/agents/readonly.yaml` |

YAML shape (already wired through `AgentFactory` → `create_deep_agent`):

```yaml
interrupt_on:
  tool_call:
    - write_file
    - skill_delete
    - memory_delete
    - kb_delete
```

Resume:

```bash
agentbase resume --thread-id <id> --decision approve
# API: POST /agents/{name}/resume  {"thread_id": "...", "decision": "approve"}
```

**Default-agent risk:** `configs/agents/default.yaml` includes `write_file` and several delete tools with an empty interrupt map. That is a **development profile**, not a safety profile. Use `interrupt_demo` as the HITL example, or `readonly` when the agent must not mutate state.

Isomorphic hook: **`interrupt_on` = beforeToolExecute / requireApproval**. PermissionRule `mode: interrupt` is the same family.

---

## After-output / content filter

| Hook | Where | Default |
|------|--------|---------|
| `redact_output` middleware | agent YAML `middleware:` | not on default agent |
| `redaction.enabled` | `configs/default.yaml` | `false` |

Isomorphic hook: **`redact_output` / redaction provider = beforeOutput**. ROADMAP N4 covers a fuller output guardrail (denylist / schema / abstention) and stays default-off.

---

## Eval hook (CI-shaped, not yet in CI)

```bash
agentbase eval --suite examples/eval_suite.yaml -o eval_report.json
# exit 1 if any case fails
```

- Entry: CLI `agentbase eval` (`src/agentbase/cli.py` `cmd_eval`) + `EvaluationRunner`.
- Suite template: `examples/eval_suite.yaml` (capability smoke cases, **not** a safety/refusal set).
- Threshold: a case passes when the average selected metric score is ≥ 0.5.
- CI: hanging the suite on GitHub Actions is ROADMAP **N2** (`.github/` may be local-only depending on gitignore/token scope). Document the command in your own workflow:

```yaml
- run: agentbase eval --suite examples/eval_suite.yaml
```

Isomorphic hook: **`agentbase eval` = runEval**. Do not treat keyword-match toy suites as task-success or injection-resistance evidence.

---

## Failure / reflection-adjacent hooks (G1 — no auto-reflection product)

There is no separate “reflection loop” service. These existing pieces are the documented onFailure / experience sink:

| Need | Existing mechanism | How to turn on |
|------|--------------------|----------------|
| Log every model call | `request_logger` middleware | already on default agent |
| Retry transient failures | `retry` middleware | add to agent `middleware:` |
| Timeout | `timeout` middleware | add to agent `middleware:` |
| Persist failures for humans | `audit_log` middleware + `audit.enabled` | production combo in [SECURITY.md](../SECURITY.md) |
| Regression after a bug | add a case to `examples/eval_suite.yaml` or your own suite; `agentbase eval` exit 1 | ROADMAP N2 to gate CI |
| Compact long threads | `summary` middleware | commented in default YAML |

Isomorphic hook: **middleware + eval suite + audit = onFailure / reflectionTriggers**. G1 scaffolds are not required to auto-rewrite prompts from failures.

---

## Audit / redaction / metrics (defaults stay off except metrics)

| Switch | Default | Production note |
|--------|---------|-----------------|
| `audit.enabled` | `false` | turn on; pair with `audit_log` middleware |
| `redaction.enabled` | `false` | turn on; pair with `redact_output` |
| `secrets.enabled` | `false` | optional envelope for stored secrets |
| `GET /metrics` | enabled; **not** a public path | Auth on → same credentials as other routes (401 without). `metrics.enabled=false` → 404. Fail-open local-dev still serves it. |
| `session_ttl_seconds` | `null` (never expire) | `AgentRunner` invoke/stream/resume pass it into `Session.create`. Set a TTL in prod; expired sessions still need `POST /sessions/cleanup` (no background sweeper). |

---

## Eval vs security suites

`examples/eval_suite.yaml` is a capability smoke suite. A case named for “capabilities” must not be read as a refusal test. Injection / privilege / path-escape checks belong in [`tests/security/`](../tests/security/) and [SECURITY.md](../SECURITY.md) R1–R6, not in the toy keyword suite.
