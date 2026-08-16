# Error Code Registry

> **AgentBase** — a configuration-driven AI Agent backend / LLM agent framework / 智能体脚手架. This document lists all stable error codes.

**Documentation index:** [README](../README.md) · [Quick Start](quickstart.md) · [Configuration](configuration.md) · [Core Services](core-services.md) · [Extensions](extensions.md) · [Backend Boundaries](backend-boundaries.md) · [Project Positioning](project-positioning.md)

All user-facing errors carry a stable error code in the format `agentbase_<domain>_<nnn>`.

## Domains

| Domain | Prefix | Description |
|--------|--------|-------------|
| Config | `AGENTBASE_CONFIG` | Configuration loading and validation |
| Registry | `AGENTBASE_REG` | Extension registration and lookup |
| Factory | `AGENTBASE_FACTORY` | Component assembly |
| Runtime | `AGENTBASE_RT` | Agent execution (invoke/stream/resume) |
| Auth | `AGENTBASE_AUTH` | Authentication and authorization |
| Rate | `AGENTBASE_RATE` | Rate limiting |
| Queue | `AGENTBASE_QUEUE` | Queue operations |
| Knowledge Base | `AGENTBASE_KB` | Knowledge base operations |
| Upload | `AGENTBASE_UPLOAD` | File upload operations |
| WebSocket | `AGENTBASE_WS` | WebSocket operations |
| Migration | `AGENTBASE_MIGRATION` | Database migration operations |
| Usage | `AGENTBASE_USAGE` | Token usage tracking operations |
| Webhook | `AGENTBASE_WEBHOOK` | Webhook notification operations |
| Feedback | `AGENTBASE_FEEDBACK` | User feedback operations |
| Notification | `AGENTBASE_NOTIFICATION` | Notification center operations |
| Conversation | `AGENTBASE_CONVERSATION` | Conversation history operations |

## Error Codes

### Config Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_CONFIG_001` | Configuration missing or invalid | Config file not found, invalid YAML, missing required field |
| `AGENTBASE_CONFIG_002` | Configuration validation error | Invalid config value (e.g., empty JWT secret when `type=jwt`) |
| `AGENTBASE_CONFIG_003` | Required environment variable missing | Expected env var (e.g. API key) not set |

### Registry Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_REG_001` | Registry lookup failed | Unknown name requested |
| `AGENTBASE_REG_002` | Duplicate registration | Name already registered |
| `AGENTBASE_REG_003` | Empty name | Registration with empty/null name |

### Factory Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_FACTORY_001` | Component assembly failed | Factory build error |
| `AGENTBASE_FACTORY_002` | Optional dependency missing | Required package not installed |
| `AGENTBASE_FACTORY_003` | Agent not found | Unknown agent name |
| `AGENTBASE_FACTORY_004` | Model initialization failed | Model provider error |

### Runtime Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_RT_001` | Operation timed out | `TimeoutError` during model call |
| `AGENTBASE_RT_002` | Resource not found | `resume` with non-existent `thread_id` |
| `AGENTBASE_RT_003` | Invoke failed | Agent invocation error |
| `AGENTBASE_RT_004` | Stream failed | Agent stream error |
| `AGENTBASE_RT_005` | Resume failed | Agent resume error |
| `AGENTBASE_RT_006` | Recursion limit | Agent exceeded max iterations |
| `AGENTBASE_RT_999` | Unknown runtime error | Unclassified exception during execution |

### Auth Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_AUTH_001` | API key missing | No API key provided |
| `AGENTBASE_AUTH_002` | Invalid token | JWT signature invalid or malformed |
| `AGENTBASE_AUTH_003` | Expired token | JWT token past expiry |
| `AGENTBASE_AUTH_004` | Forbidden | Insufficient role/permissions |

### Rate Limit Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_RATE_001` | Rate limit exceeded | Too many requests in time window |

### Queue Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_QUEUE_001` | Queue not initialized | Queue provider not configured |
| `AGENTBASE_QUEUE_002` | Task not found | Unknown `task_id` |
| `AGENTBASE_QUEUE_003` | Cancel failed | Task already completed/cancelled |

### Knowledge Base Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_KB_001` | Parse failed | Document parsing error |
| `AGENTBASE_KB_002` | Search failed | Vector search error |
| `AGENTBASE_KB_003` | Document not found | Unknown `doc_id` |

### Upload Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_UPLOAD_001` | File too large | Upload exceeds size limit |
| `AGENTBASE_UPLOAD_002` | Unsupported type | File extension not allowed |
| `AGENTBASE_UPLOAD_003` | Upload failed | I/O or processing error |

### WebSocket Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_WS_001` | Agent not found | WebSocket requested unknown agent |
| `AGENTBASE_WS_002` | Empty message | WebSocket received empty/null message |

### Migration Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_MIGRATION_001` | Migration failed | Alembic upgrade/downgrade error |
| `AGENTBASE_MIGRATION_002` | Scripts directory missing | Migration scripts not found |

### Usage Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_USAGE_001` | Record failed | Usage tracking write error |
| `AGENTBASE_USAGE_002` | Query failed | Usage query error |
| `AGENTBASE_USAGE_003` | Not initialized | Usage tracking disabled |

### Webhook Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_WEBHOOK_001` | Delivery failed | HTTP POST to endpoint failed |
| `AGENTBASE_WEBHOOK_002` | Endpoint not found | Unknown `endpoint_id` |
| `AGENTBASE_WEBHOOK_003` | Not initialized | Webhook service disabled |

### Feedback Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_FEEDBACK_001` | Record failed | Feedback write error |
| `AGENTBASE_FEEDBACK_002` | Not found | Unknown `record_id` |
| `AGENTBASE_FEEDBACK_003` | Not initialized | Feedback service disabled |

### Notification Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_NOTIFICATION_001` | Create failed | Notification write error |
| `AGENTBASE_NOTIFICATION_002` | Not found | Unknown `notification_id` |
| `AGENTBASE_NOTIFICATION_003` | Not initialized | Notification service disabled |

### Conversation Domain

| Code | Meaning | Trigger |
|------|---------|---------|
| `AGENTBASE_CONVERSATION_001` | Record failed | Conversation write error |
| `AGENTBASE_CONVERSATION_002` | Not found | Unknown `thread_id` |
| `AGENTBASE_CONVERSATION_003` | Not initialized | Conversation service disabled |

## Stability Guarantee

Error codes are stable API. Once published, a code's meaning will not change. New codes may be added with new sequence numbers.
