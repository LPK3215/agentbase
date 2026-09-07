# Security test extension point

Defensive checks for the fail-closed redlines in [`SECURITY.md`](../../SECURITY.md) and [`redlines.yaml`](redlines.yaml).

This directory is a skeleton for secondary developers:

- Add injection / privilege cases as **assertions of secure behaviour** (reject, raise, refuse to start).
- Do not commit exploit payloads, attack scripts, or reproduction procedures aimed at bypassing controls.
- Dynamic red-team remains out of scope for the default profile.

`redlines.yaml` maps R1–R6. A meta-test requires every `must_pass: true` item to list a non-empty `tests` array (empty `must_pass` cannot go green). R2/R6 assert `configs/agents/interrupt_demo.yaml` and `readonly.yaml` — they do **not** flip the shipped `default` profile. R3 (audit default off) and R4 (no `tenant_id`) are `must_pass: false` until those products exist. Do not treat `agent_name` uniqueness as tenancy.

Run:

```bash
pytest tests/security/ -q
```
