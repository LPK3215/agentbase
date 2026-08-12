# Error Code Registry

All user-facing errors carry a stable error code in the format `agentbase_<domain>_<nnn>`.

## Domains

| Domain | Prefix | Description |
|--------|--------|-------------|
| Config | `AGENTBASE_CONFIG` | Configuration loading and validation |
| Registry | `AGENTBASE_REG` | Extension registration and lookup |
| Factory | `AGENTBASE_FACTORY` | Component assembly |
| Runtime | `AGENTBASE_RT` | Agent execution (invoke/stream/resume) |
| Doctor | `AGENTBASE_DOC` | Health check diagnostics |
| Test | `AGENTBASE_TEST` | Test infrastructure |
| CI | `AGENTBASE_CI` | CI/CD pipeline |

## Error Codes

### Config Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_CONFIG_001` | Configuration missing or invalid | Config file not found, invalid YAML, missing required field |
| `AGENTBASE_CONFIG_002` | Model API key missing | No API key resolved from environment |
| `AGENTBASE_CONFIG_003` | Agent configuration invalid | Invalid agent YAML |

### Registry Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_REG_001` | Registry operation failed | Duplicate registration, unknown name, empty name |

### Factory Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_FACTORY_001` | Component assembly failed | Factory build error |
| `AGENTBASE_FACTORY_002` | Optional dependency missing | Required package not installed |
| `AGENTBASE_FACTORY_003` | Agent assembly failed | `create_deep_agent` raised during doctor probe |

### Runtime Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_RT_001` | Operation timed out | `TimeoutError` during model call |
| `AGENTBASE_RT_002` | Session not found | `resume` called with non-existent `thread_id` |
| `AGENTBASE_RT_999` | Unknown runtime error | Unclassified exception during execution |

### Doctor Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_DOC_001` | Documentation inconsistency | CLI args mismatch with README |
| `AGENTBASE_DOC_002` | Broken documentation link | Unreachable link in docs |

### Test Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_TEST_001` | Test coverage below threshold | Coverage < required minimum |
| `AGENTBASE_TEST_002` | Test isolation violation | Network call detected in offline test |

### CI Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_CI_001` | Lint check failed | ruff/mypy/isort violation |
| `AGENTBASE_CI_002` | Security audit failed | pip-audit found vulnerability |

## Stability Guarantee

Error codes are stable API. Once published, a code's meaning will not change. New codes may be added with new sequence numbers.