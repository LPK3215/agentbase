# AgentBase 功能路线图（ROADMAP）

> 配合 `docs/PROJECT_DRIVER_PROMPT.md` 使用。
> AI 每次推进会话读取本文件，选择**最高优先级、未完成**的模块推进。
> 状态：`done` / `in_progress` / `pending`。完成后必须更新状态。

---

## 已完成里程碑（v0.4.0）

| 模块 | 状态 | 说明 |
|------|------|------|
| 核心服务（16） | done | memory / knowledge / queue / skill / workspace / agent factory / session / mcp / tracing / graph / config / registry / checkpointer / audit / redaction / secrets |
| 9 大可插拔注册表 | done | parser / embedding / search / mcp / queue / tracer / graph / storage / checkpointer / audit / redaction / secrets |
| 扩展体系 | done | tools(32) / middleware(6) / subagents / parsers(9)，装饰器注册 + 自动发现 |
| API 层 | done | 21 条路由，含 agents / memory / kb / queue / skills / workspace / health |
| CLI 层 | done | 14 条命令，含 init / run / backup / restore / resume / doctor / add-extension |
| 测试基座 | done | 708 测试全绿，conftest 统一 fixture |
| 部署 | done | Docker / K8s Helm / Nginx / Bare metal 四套方案 |

---

## 待推进模块（按优先级排序）

### A. 核心服务增强

#### A1. 审计日志服务（AuditLogService）
- **状态**：done ｜ **优先级**：P1
- **定位**：结构化记录关键操作（谁/何时/做了什么/结果），满足企业合规。
- **接口**：`AuditLogProvider` Protocol（`record(event)` / `query(filter)` / `export()`）。
- **默认实现**：SQLite 审计表（复用 storage backend）；可替换为 PostgreSQL / 文件。
- **注册**：`audit_registry`，挂 factory 上下文。
- **开关**：config `audit.enabled=false`（默认关，但必须实现）。
- **错误码**：复用 `agentbase_*` 域。
- **测试**：record→query→export 全链路；enabled/disabled 分支；并发写。

#### A2. 敏感信息脱敏服务（RedactionService）
- **状态**：done ｜ **优先级**：P1
- **定位**：对文本中的 API Key / 手机号 / 邮箱 / 身份证做可配置掩码，防日志与响应泄漏。
- **接口**：`RedactionProvider` Protocol（`redact(text)` / `mask(value, kind)`）。
- **默认实现**：正则规则集 + 可扩展规则注册；可替换为 Presidio 等（optional dep）。
- **注册**：`redaction_registry`。
- **开关**：config `redaction.enabled`；中间件 `redaction`（对模型请求/响应生效）。
- **测试**：各 PII 类型掩码；不误伤普通文本；规则热注册。

#### A3. 配置机密加密（SecretsStore）
- **状态**：done ｜ **优先级**：P2
- **定位**：config 中的密钥（API Key 等）支持密文存储与读取，避免明文落盘。
- **接口**：`SecretsProvider` Protocol（`set(key, val)` / `get(key)` / `exists`）。
- **默认实现**：Fernet 对称加密 + 本地 key 文件；可替换为环境变量 / Vault（optional）。
- **注册**：`secrets_registry`。
- **开关**：config `secrets.enabled=false`。
- **测试**：加密→解密往返；坏 key 报错；未启用时透明直读。

#### A4. 会话隔离增强（SessionStore 完善）
- **状态**：done ｜ **优先级**：P2
- **定位**：会话 TTL、自动清理、跨线程安全；目前 session 仅基础 CRUD。
- **接口**：复用现有 `SessionManager`，补齐 `ttl` / `cleanup_expired` / `touch`。
- **测试**：过期清理；并发 touch 无竞态。

### B. 工具层扩充（extensions/tools）

#### B1. `http_request` 工具
- **状态**：pending ｜ **优先级**：P2
- **定位**：Agent 发起 HTTP 请求（GET/POST），带超时、响应大小上限、重定向限制。
- **注册**：`@register_tool("http_request")`，`default_enabled=false`。
- **错误**：统一错误码；非 2xx 结构化返回。
- **测试**：mock 服务器正常/超时/大响应/4xx。

#### B2. `db_query` 只读工具
- **状态**：pending ｜ **优先级**：P3
- **定位**：Agent 对配置的只读数据源执行 SELECT（白名单、行数上限、超时）。
- **注册**：`@register_tool("db_query")`，`default_enabled=false`。
- **安全**：仅允许 SELECT；禁止 DDL/DML；结果上限。
- **测试**：正常查询 / 注入拦截 / 超时 / 超行数。

### C. 中间件层扩充

#### C1. 模型调用限流中间件（`rate_limit`）
- **状态**：done ｜ **优先级**：P1
- **定位**：复用 API 层 RateLimiter 语义，对模型调用按 agent / 全局限流。
- **注册**：`@register_middleware("rate_limit")`，`default_enabled=false`。
- **契约**：`burst` 短突发语义与现有 RateLimiter 一致，勿改变默认值语义。
- **测试**：精确上限（burst=0）/ 突发容忍 / 冷却恢复。

#### C2. 响应脱敏中间件（`redact_output`）
- **状态**：pending ｜ **优先级**：P2
- **定位**：基于 A2，对模型输出做脱敏后再返回，防止密钥经 LLM 泄漏。
- **注册**：`@register_middleware("redact_output")`，`default_enabled=false`。
- **测试**：输出含密钥被掩码；正常文本不变。

### D. API / 运维层

#### D1. 健康检查增强（`/health` 组件探活）
- **状态**：pending ｜ **优先级**：P2
- **定位**：返回各依赖组件状态（storage / queue / embedding / search），供 K8s readiness。
- **测试**：组件正常 / 组件故障时响应结构正确且非 200。

#### D2. 审计查询 API
- **状态**：pending ｜ **优先级**：P3
- **定位**：配合 A1，暴露只读审计查询端点（分页、按时间/操作类型过滤），需鉴权。
- **测试**：鉴权 / 分页 / 过滤。

### E. 模板 / 脚手架（agentbase 的核心卖点）

#### E1. `agentbase init` 增强（交互式引导）
- **状态**：done ｜ **优先级**：P1
- **定位**：交互式选择数据库 / embedding / queue / tracer 组合，生成可运行项目骨架。
- **测试**：不同组合生成的骨架可导入、可跑 `doctor`。

#### E2. 扩展骨架生成器 `agentbase add-extension`
- **状态**：done ｜ **优先级**：P1
- **定位**：`agentbase add-extension tool --name my_tool` 生成标准 tool 骨架（含 meta、注册、测试模板、docs 章节模板），让"加一个新功能"成为半自动流程。
- **测试**：生成的骨架文件齐全；导入无错；测试模板可运行。

#### E3. 模板渲染引擎统一
- **状态**：pending ｜ **优先级**：P2
- **定位**：将 init / add-extension 的模板渲染抽为统一引擎（变量替换 + 条件块 + 校验），消除复制粘贴式生成。

### F. 文档 / 示例完备化

#### F1. Cookbook 示例库
- **状态**：pending ｜ **优先级**：P2
- **定位**：每个注册表一个完整可运行示例（如"从 SQLite 切到 PostgreSQL"、"加自定义 embedding"），进 `examples/` 目录。
- **测试**：示例脚本有 smoke test 或至少 `--help` 可跑。

#### F2. 迁移指南
- **状态**：pending ｜ **优先级**：P3
- **定位**：`docs/migrations/`：SQLite→PostgreSQL、Memory→Redis、Null→Langfuse 的逐步指南。

---

## 推进规则

1. 每次只推进一个模块，完成后按 PROMPT 第 7 节自查清单逐项确认。
2. 模块完成 → 状态改 `done`，并在 `.codebuddy/memory/` 记录。
3. 用户可随时调整优先级 / 增删模块；调整后以 ROADMAP 为准。
4. 新模块候选须先过 `docs/project-positioning.md` 的边界三问：
   （是 Agent 后端基础设施？是否需要多种实现？是否涉及业务决策？）
