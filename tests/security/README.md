# Security test extension point

Defensive checks for the fail-closed redlines in [`SECURITY.md`](../../SECURITY.md) and [`redlines.yaml`](redlines.yaml).

This directory is a skeleton for secondary developers:

- Add injection / privilege cases as **assertions of secure behaviour** (reject, raise, refuse to start).
- Do not commit exploit payloads, attack scripts, or reproduction procedures aimed at bypassing controls.
- Dynamic red-team remains out of scope for the default profile.

Run:

```bash
pytest tests/security/ -q
```
