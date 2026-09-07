# Security policy and fail-closed redlines

> **AgentBase is a backend scaffold.** Mechanisms below are provided for secondary developers. Defaults favour zero-config local use (`app.env: dev`). Production profiles must turn the gates on. See [docs/guardrails.md](docs/guardrails.md) for HITL / eval / reflection hook mapping.

Report vulnerabilities privately to the maintainer listed in [README](README.md). Do not open a public issue with exploit details.

## Production authentication (mandatory)

| Mode | How to enable | What happens if missing |
|------|----------------|-------------------------|
| API key | Non-empty `AGENTBASE_API_KEY`, **or** YAML `auth.api_key` (non-empty YAML **overrides** env) | `app.env` in `{prod, production}` with neither key nor JWT → process **refuses to start** (`AGENTBASE_CONFIG_004`) |
| JWT | `auth.type: jwt` + non-empty `AGENTBASE_AUTH__SECRET` / `auth.secret` | Empty secret → API **refuses to start** (`AGENTBASE_CONFIG_002`); `JWTAuth(secret="")` raises `ValueError` (no ephemeral key) |
| Dev open API | both keys empty **and** `app.env` not prod | Intentional fail-open for local clone-and-run |

`auth.type: none` turns JWT off. It does **not** skip HTTP/WebSocket checks when an env or YAML API key is set.

Compose sets `AGENTBASE_APP__ENV=prod` by default. Set `AGENTBASE_API_KEY` (or YAML `auth.api_key`, or JWT secret) before `docker compose up`.

Public paths (no auth even when auth is on): `/health`, `/docs`, `/redoc`, `/openapi.json`, `/`. Middleware also skips `/auth/oauth2/*` (authorize/callback must run before login). **`/metrics` is not a public path.** When API key or JWT is on, `GET /metrics` needs the same credentials as other routes (401 without them). `metrics.enabled: false` → 404. Fail-open local-dev (no key, not JWT) still serves `/metrics` without credentials — do not publish that process on the internet.

HTTP and WebSocket share `_verify_auth`. On the socket, query `token` is mapped to `Authorization: Bearer` when neither `Authorization` nor `X-API-Key` is present. Handshake failure closes with code **4001** (Starlette HTTP middleware does not run for WebSocket scope).

## 安全红线清单（fail-closed）

本项目作为脚手架，以下红线由使用者按实际场景配置后启用。表存在即满足工程验收 7.5.1 门槛；默认关闭不等于机制不存在。

| # | 红线项 | 检测方式 | 触发后行为 | 适用范围 |
|---|---|---|---|---|
| R1 | 策略绕过/越权 | Agent YAML `permissions` allow/deny/interrupt；默发 deny `.env` / `*.key` / `*.pem` / `secrets/**`。API：`_is_auth_enabled(app_config)` 在 YAML `auth.api_key` **或** `AGENTBASE_API_KEY` 非空时为真（YAML 覆盖 env）。`app.env=prod` 且无 API key / JWT secret 则启动失败。JWT 空 secret 启动失败。WebSocket 走同一套 `_verify_auth`，失败 `close(4001)`。工作区 `resolve_within_workspace` 拒绝目录穿越。`db_query` 仅 SELECT。 | 工具路径 deny = 拒绝调用；目录穿越 = `ValueError`；生产无鉴权 = 拒绝启动；JWT 无 secret = 拒绝启动；WS 无凭证 = 4001 | 所有工具调用、HTTP API 与 WebSocket |
| R2 | 危险操作无确认 | `interrupt_on.tool_call` 列表（见 `configs/agents/interrupt_demo.yaml`）。默发 `default` / `coder` 的 `interrupt_on: {}`（空）。只读画像见 `configs/agents/readonly.yaml`。触发点总表见 [docs/guardrails.md](docs/guardrails.md)。 | 配置了 interrupt 的工具在执行前暂停，须 `resume`（approve/edit/reject）；未配置则自动执行 | 写操作 / 外发 / 删除 / 代码执行 |
| R3 | 审计日志缺失 | `audit.enabled`（默认 `false`）+ `audit_log` 中间件。生产应打开并接存储。 | 开启后写入审计事件；未开启不阻断运行（脚手架默认关 = 由使用者启用） | 所有会话 |
| R4 | 跨租户数据泄漏 | 当前记忆按 `agent_name` 隔离，**无 `tenant_id`**。单用户零配置可接受。多租户必须自行在服务端注入身份并过滤存储键，不得信任 invoke metadata 中的 user 字段。 | 未实现多租户时不得按多租户上线；误用 = 数据面串读风险，须停用或自行加隔离 | 多租户场景 |
| R5 | 凭据暴露 | JWT 禁止空/默认 secret。日志脱敏模块可开。`redaction.enabled` / `redact_output` 中间件默认关。`.gitignore` 忽略 `.env` 与密钥文件。禁止把生产密钥写入 memory / KB / prompt。 | JWT 空 secret = 拒绝启动；生产应打开 redaction；密钥进记忆须人工轮换 | 所有 API 调用与模型上下文 |
| R6 | 不可逆高影响无确认 | 删除类工具：`skill_delete` / `memory_delete` / `kb_delete` / `write_file`。须列入 `interrupt_on` 或从默发工具表拿掉（改用 `readonly` 画像）。`code_execute` 为 subprocess 超时沙箱，**不是** syscall jail。 | 列入 interrupt 则转人工；未列入则自动执行。不可逆操作上线前必须有确认或从画像删除 | 不可逆操作 |

## Scaffold users: enable per scenario

Zero-config local loop: `app.env: dev`, empty API key, empty `interrupt_on`, `audit` / `redaction` off.

Production minimum:

1. `app.env: prod` (or `production`) + `AGENTBASE_API_KEY` or YAML `auth.api_key` or JWT secret.
2. Restrict CORS origins (`AGENTBASE_CORS_ORIGINS` wins when set; `*` disables credentials). `/metrics` follows API auth when a key/JWT is configured; scrape with credentials or keep it off the public internet. Set `metrics.enabled: false` if you do not need the endpoint.
3. Turn on `audit.enabled` and `redaction.enabled`; add `audit_log` / `redact_output` to the agent middleware list.
4. Point write/delete/email/code tools at `interrupt_on`, or run `readonly`.
5. Do not treat this scaffold as multi-tenant until you inject server-side identity.

Security tests live in [`tests/security/`](tests/security/) (defensive unit checks + `redlines.yaml`). Dynamic red-team is out of scope for the default profile.
