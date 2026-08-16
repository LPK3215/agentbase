# AgentBase 功能路线图（ROADMAP）

> 配合 `docs/提示词.md` 使用。
> AI 每次推进会话读取本文件，选择**最高优先级、未完成**的模块推进。
> 状态：`done` / `in_progress` / `pending`。完成后必须更新状态。

---

## 已完成里程碑（v0.4.0）

| 模块 | 状态 | 说明 |
|------|------|------|
| 核心服务（30） | done | memory / knowledge / queue / queue_celery / skills / workspace / storage / storage_mongodb / mcp / tracer / graph / audit / redaction / secrets / experiment / migration / model_manager / prompt / user_manager / apikey_manager / oauth2 / usage / webhook / feedback / notification / conversation / evaluation / parsers / embeddings / search |
| 可插拔注册表 | done | parser / embedding / search / mcp / queue / tracer / graph / storage / checkpointer / audit / redaction / secrets / experiment / model_manager / prompt_manager / user_manager / apikey_manager / usage / webhook / feedback / notification / conversation + tool / subagent / middleware（25 个注册表） |
| 扩展体系 | done | tools(37) / middleware(9) / subagents / parsers(9)，装饰器注册 + 自动发现 |
| API 层 | done | 100 条路由，含 agents / memory / kb / queue / skills / workspace / health / audit / experiments / models / prompts / users / auth / sessions / apikeys / usage / webhooks / feedback / notifications / conversations / admin(rate-limit) |
| CLI 层 | done | 20 条命令，含 run / stream / resume / serve / doctor / version / config(validate/show) / backup / restore / worker / db(init/upgrade/downgrade/current/heads/history/stamp) |
| 测试基座 | done | 2686 测试全绿，conftest 统一 fixture |
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
- **状态**：done ｜ **优先级**：P2
- **定位**：Agent 发起 HTTP 请求（GET/POST/PUT/PATCH/DELETE），带超时、响应大小上限、重定向限制。
- **注册**：`@register_tool("http_request")`，`default_enabled=false`。
- **错误**：统一错误码；非 2xx 结构化返回。
- **测试**：mock 服务器正常/超时/大响应/4xx。

#### B2. `db_query` 只读工具
- **状态**：done ｜ **优先级**：P3
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
- **状态**：done ｜ **优先级**：P2
- **定位**：基于 A2，对模型输出做脱敏后再返回，防止密钥经 LLM 泄漏。
- **注册**：`@register_middleware("redact_output")`，`default_enabled=false`。
- **测试**：输出含密钥被掩码；正常文本不变。

#### C3. 多模型路由中间件（`model_router`）
- **状态**：done ｜ **优先级**：P5
- **定位**：按策略（round_robin / weighted / random / failover）在多个模型间路由调用，支持成本优化与故障转移。
- **注册**：`@register_middleware("model_router")`，`default_enabled=false`。
- **配置**：`agent_config.metadata.model_router.strategy` + `models` 列表。
- **测试**：4 种策略选择逻辑；failover 错误转移；配置缺失降级；线程安全。

### D. API / 运维层

#### D1. 健康检查增强（`/health` 组件探活）
- **状态**：done ｜ **优先级**：P2
- **定位**：返回各依赖组件状态（storage / queue / embedding / search / tracer），供 K8s readiness。
- **测试**：组件正常 / 组件故障时响应结构正确且非 200。

#### D2. 审计查询 API
- **状态**：done ｜ **优先级**：P3
- **定位**：配合 A1，暴露只读审计查询端点（分页、按时间/操作类型过滤），需鉴权。
- **测试**：鉴权 / 分页 / 过滤。

### E. 模板 / 脚手架（agentbase 的核心卖点）

> **已移除**：`agentbase init` 和 `agentbase add-extension` 命令已删除。
> 项目定位为仓库型脚手架，用户直接克隆仓库二次开发，不需要模板生成工具。
> 详见 `docs/project-positioning.md` 中的功能边界说明。

### F. 文档 / 示例完备化

#### F1. Cookbook 示例库
- **状态**：done ｜ **优先级**：P2
- **定位**：每个注册表一个完整可运行示例（如"从 SQLite 切到 PostgreSQL"、"加自定义 embedding"），进 `examples/` 目录。
- **测试**：示例脚本有 smoke test 或至少 `--help` 可跑。

#### F2. 迁移指南
- **状态**：done ｜ **优先级**：P3
- **定位**：`docs/migrations/`：SQLite→PostgreSQL、Memory→Redis、Null→Langfuse 的逐步指南。

### G. 实验与质量保障

#### G1. A/B 测试框架（Experiment）
- **状态**：done ｜ **优先级**：P5
- **定位**：对比不同 Agent 策略效果（模型/system_prompt/temperature），支持 round_robin / weighted / random 分配策略和统计报告。
- **接口**：`ExperimentProvider` Protocol（create / assign / record_result / get_stats / delete）。
- **默认实现**：`InMemoryExperimentProvider`（零配置，进程内存储）；`NullExperimentProvider`（禁用时 no-op）。
- **注册**：`experiment_registry`，`@register_experiment_provider("name")`。
- **开关**：config `experiment.enabled=false`（默认关）。
- **API**：`/experiments` CRUD + `/assign` + `/results` + `/stats`（7 条路由）。
- **测试**：create / assign（4 种策略）/ record / stats / delete / API 全链路。

#### G2. MongoDB 存储 Provider
- **状态**：done ｜ **优先级**：P5
- **定位**：文档型 NoSQL 存储，通过 SQL→MongoDB 适配层实现与 SQLite/PostgreSQL/MySQL 统一接口。
- **实现**：`MongoDBBackend`（`src/agentbase/core/storage_mongodb.py`），实现 `StorageBackend` Protocol 全部 7 个方法。
- **适配**：`INSERT` → `insert_one`、`SELECT` → `find`、`UPDATE` → `update_many`、`DELETE` → `delete_many`、`COUNT(*)` → `count_documents`。
- **注册**：`create_storage(dsn="mongodb://...")` 自动路由。
- **配置**：`storage.type: mongodb` + `storage.dsn: mongodb://host:port/db`。
- **依赖**：`pip install agentbase[mongodb]`（`pymongo>=4.6.0`）。
- **测试**：SQL 解析器（26）+ Protocol 合规（3）+ mock 集成（14）+ 配置（2）+ Row helper（3）= 51 测试。

#### G3. Celery 分布式队列 Provider
- **状态**：done ｜ **优先级**：P5
- **定位**：Celery 分布式任务队列，支持 RabbitMQ/Redis broker，实现多进程、多节点分布式 Agent 任务处理。
- **实现**：`CeleryRequestQueue`（`src/agentbase/core/queue_celery.py`），实现 `RequestQueue` Protocol 全部 5 个方法。
- **适配**：Celery 状态映射（PENDING→pending、SUCCESS→completed、FAILURE→failed、REVOKED→cancelled）；`submit` → `apply_async`；`cancel` → `revoke`。
- **注册**：`queue_registry`，`@register_queue_provider("celery")`。
- **配置**：`queue.provider: celery` + `queue.options.broker_url`。
- **依赖**：`pip install agentbase[celery]`（`celery>=5.3.0`）。
- **测试**：Protocol 合规（6）+ 状态映射（7）+ 反序列化（3）+ submit（3）+ get_task（4）+ list（3）+ cancel（3）+ update（2）+ stats（2）+ clear（2）+ handler（2）+ health_check（2）+ registry（1）+ import_error（1）= 41 测试。

#### G4. 审计日志中间件（`audit_log`）
- **状态**：done ｜ **优先级**：P5
- **定位**：自动记录模型调用的审计事件，复用 AuditManager，实现合规审计。
- **注册**：`@register_middleware("audit_log")`，`default_enabled=false`。
- **行为**：通过 `wrap_model_call` 拦截模型调用，在成功/失败后记录 `AuditEvent`（actor=agent_name、action=`model.call`、resource=`model:<name>`、result=success/failure、detail={duration_ms, thread_id, error}）。
- **依赖**：`audit.enabled=true` + `middleware: [audit_log]`。
- **测试**：注册验证（3）+ 构建行为（5）+ 中间件行为（8）= 16 测试。

#### G5. 邮件发送工具（`email_sender`）
- **状态**：done ｜ **优先级**：P5
- **定位**：Agent 通过 SMTP 发送电子邮件，支持纯文本/HTML、多收件人（to/cc/bcc）、SSL/TLS 加密认证。
- **注册**：`@register_tool("email_sender")`，`default_enabled=false`。
- **配置**：`agent_config.metadata.email`（`smtp_host` / `smtp_port` / `use_tls` / `use_ssl` / `username` / `password` / `from_addr` / `timeout`）。
- **安全**：收件人上限 50、正文上限 500K 字符、主题上限 200 字符、超时上限 60s。
- **依赖**：无外部依赖（Python 标准库 `smtplib` + `email.mime`）。
- **测试**：注册（3）+ 验证（4）+ 截断（2）+ 构建（2）+ 发送（7）+ 错误处理（4）= 22 测试。

#### G6. 模型管理服务（ModelProvider）
- **状态**：done ｜ **优先级**：P5
- **定位**：多模型注册、CRUD、连通性测试——标准 AI 后台系统的核心基础能力。
- **接口**：`ModelProvider` Protocol（`register` / `get` / `list` / `update` / `delete` / `close`）。
- **默认实现**：`InMemoryModelProvider`（零配置，进程内存储，线程安全）；`NullModelProvider`（禁用时 no-op）。
- **注册**：`model_registry`，`@register_model_provider("name")`。
- **开关**：config `model_manager.enabled=false`（默认关）。
- **API**：`/models` CRUD + `/models/{name}/test`（6 条路由）。
- **测试**：数据模型（3）+ InMemory CRUD（14）+ Null（5）+ Manager（8）+ Registry（5）+ Singleton（2）+ Protocol（2）= 39 测试。

#### G7. 提示词模板管理服务（PromptProvider）
- **状态**：done ｜ **优先级**：P5
- **定位**：提示词模板 CRUD、变量替换渲染——标准 AI 后台系统的核心基础能力。
- **接口**：`PromptProvider` Protocol（`register` / `get` / `list` / `update` / `delete` / `close`）。
- **默认实现**：`InMemoryPromptProvider`（零配置，进程内存储，线程安全）；`NullPromptProvider`（禁用时 no-op）。
- **注册**：`prompt_registry`，`@register_prompt_provider("name")`。
- **开关**：config `prompt_manager.enabled=false`（默认关）。
- **API**：`/prompts` CRUD + `/prompts/{name}/render`（6 条路由）。
- **测试**：数据模型（4）+ InMemory CRUD（14）+ Null（6）+ Manager（16）+ Registry（5）+ Singleton（2）+ Protocol（3）= 55 测试。

#### G8. 用户管理服务（UserProvider）
- **状态**：done ｜ **优先级**：P5
- **定位**：用户 CRUD、密码哈希、认证登录——标准 AI 后台系统的核心基础能力。
- **接口**：`UserProvider` Protocol（`register` / `get` / `get_by_email` / `list` / `update` / `delete` / `close`）。
- **默认实现**：`InMemoryUserProvider`（零配置，进程内存储，线程安全）；`NullUserProvider`（禁用时 no-op）。
- **注册**：`user_registry`，`@register_user_provider("name")`。
- **开关**：config `user_manager.enabled=false`（默认关）。
- **API**：`/users` CRUD + `/auth/register` + `/auth/login`（7 条路由）。
- **密码**：PBKDF2-HMAC-SHA256，100k rounds，随机 salt，常数时间比较。
- **测试**：密码哈希（12）+ 数据模型（8）+ InMemory CRUD（18）+ Null（7）+ Manager（31）+ Registry（7）+ Singleton（3）+ 并发（4）+ Protocol（3）= 95 测试。

#### G9. Token 用量追踪服务（UsageProvider）
- **状态**：done ｜ **优先级**：P5
- **定位**：记录每次模型调用的 prompt/completion/total tokens + 成本估算，按 Agent/模型/用户/时间聚合统计——标准 AI 后台系统的核心运维能力。
- **接口**：`UsageProvider` Protocol（`record` / `query` / `stats` / `count` / `clear` / `close`）。
- **默认实现**：`InMemoryUsageProvider`（零配置，进程内存储，线程安全，FIFO 淘汰）；`NullUsageProvider`（禁用时 no-op）。
- **注册**：`usage_registry`，`@register_usage_provider("name")`。
- **开关**：config `usage.enabled=false`（默认关）。
- **成本估算**：内置 30+ 主流模型定价表（OpenAI/Anthropic/DeepSeek/Google），支持自定义 `pricing` 配置，未知模型使用回退费率。
- **集成**：AgentRunner.invoke/stream/resume 自动提取 `usage_metadata` / `response_metadata` 中的 token 用量并记录。
- **API**：`/usage/stats` + `/usage/records` + `/usage/summary` + `DELETE /usage/records`（4 条路由）。
- **错误码**：`AGENTBASE_USAGE_001`/`002`/`003`。
- **测试**：成本估算（10）+ 数据模型（6）+ InMemory（21）+ Null（6）+ Manager（16）+ Registry（10）+ Singleton（3）+ Token 提取（10）+ Filter/Stats（5）+ Protocol（2）= 89 核心 + 19 API = 108 测试。

#### G10. Webhook 事件通知服务（WebhookProvider）
- **状态**：done ｜ **优先级**：P5
- **定位**：注册 Webhook 端点并投递实时 HTTP POST 事件通知——标准 AI 后台系统的核心集成能力。
- **接口**：`WebhookProvider` Protocol（`register_endpoint` / `update_endpoint` / `delete_endpoint` / `dispatch` / `query_deliveries` / `get_stats` / `test_endpoint`）。
- **默认实现**：`InMemoryWebhookProvider`（零配置，进程内存储，线程安全，FIFO 淘汰）；`NullWebhookProvider`（禁用时 no-op）。
- **注册**：`webhook_registry`，`@register_webhook_provider("name")`。
- **开关**：config `webhook.enabled=false`（默认关）。
- **特性**：通配符事件订阅、HMAC-SHA256 签名、指数退避重试、背景线程非阻塞投递。
- **集成**：AgentRunner.invoke/stream/resume 自动触发 `agent.invoke.completed` / `agent.stream.completed` / `agent.resume.completed` 事件。
- **API**：`/webhooks` CRUD + `/webhooks/{id}/test` + `/webhooks/deliveries` + `/webhooks/stats`（8 条路由）。
- **错误码**：`AGENTBASE_WEBHOOK_001`/`002`/`003`。
- **测试**：70 核心 + 31 API = 101 测试。

#### G11. 用户反馈收集服务（FeedbackProvider）
- **状态**：done ｜ **优先级**：P5
- **定位**：收集用户评分（1-5 星或 ±1 thumbs）+ 评论 + 标签，按 Agent/线程/情感聚合统计——标准 AI 后台系统的核心用户反馈能力。
- **接口**：`FeedbackProvider` Protocol（`create` / `update` / `get` / `list` / `delete` / `clear_all`）。
- **默认实现**：`InMemoryFeedbackProvider`（零配置，进程内存储，线程安全，FIFO 淘汰）；`NullFeedbackProvider`（禁用时 no-op）。
- **注册**：`feedback_registry`，`@register_feedback_provider("name")`。
- **开关**：config `feedback.enabled=false`（默认关）。
- **特性**：自动检测评分尺度（±1 thumbs vs 1-5 stars），自动情感分类（positive/neutral/negative），多维度过滤与聚合统计。
- **API**：`/feedback` CRUD + `/feedback/stats`（6 条路由）。
- **错误码**：`AGENTBASE_FEEDBACK_001`/`002`/`003`。
- **测试**：73 核心 + 33 API = 106 测试。

#### G12. 通知中心服务（NotificationProvider）
- **状态**：done ｜ **优先级**：P5
- **定位**：应用内通知管理（系统公告、配额告警、任务完成、阈值预警）——标准 AI 后台系统的核心通知能力。
- **接口**：`NotificationProvider` Protocol（`create` / `broadcast` / `get` / `list` / `update` / `mark_read` / `mark_unread` / `mark_all_read` / `delete` / `get_stats` / `get_unread_count` / `clear_all`）。
- **默认实现**：`InMemoryNotificationProvider`（零配置，进程内存储，线程安全，FIFO 淘汰）；`NullNotificationProvider`（禁用时 no-op）。
- **注册**：`notification_registry`，`@register_notification_provider("name")`。
- **开关**：config `notification.enabled=false`（默认关）。
- **特性**：广播通知（`user_id="*"`）、过期自动过滤、多维度过滤（用户/分类/严重度/已读）、聚合统计。
- **API**：`/notifications` CRUD + `/notifications/broadcast` + `/notifications/stats` + `/notifications/unread-count` + `/notifications/read-all` + `/notifications/{id}/read` + `/notifications/{id}/unread`（11 条路由）。
- **错误码**：`AGENTBASE_NOTIFICATION_001`/`002`/`003`。
- **测试**：90+ 核心 + 40+ API = 130+ 测试。

#### G13. 对话历史服务（ConversationProvider）
- **状态**：done ｜ **优先级**：P5
- **定位**：记录和查询 Agent 对话消息历史（invoke/stream/resume 自动记录）——标准 AI 后台系统的核心上下文管理能力。
- **接口**：`ConversationProvider` Protocol（`record` / `get_history` / `list` / `update` / `delete` / `get_stats` / `count` / `close`）。
- **默认实现**：`InMemoryConversationProvider`（零配置，进程内存储，线程安全，FIFO 淘汰）；`NullConversationProvider`（禁用时 no-op）。
- **注册**：`conversation_registry`，`@register_conversation_provider("name")`。
- **开关**：config `conversation.enabled=false`（默认关）。
- **特性**：自动从 LangChain/LangGraph 消息中提取对话记录，支持标题/标签/归档管理，多维度过滤（用户/Agent/时间/标签/归档）。
- **集成**：AgentRunner.invoke/stream/resume 自动提取消息并记录对话历史。
- **API**：`/conversations` list + `/conversations/stats` + `/conversations/{thread_id}` GET/PATCH/DELETE（5 条路由）。
- **错误码**：`AGENTBASE_CONVERSATION_001`/`002`/`003`。
- **测试**：90+ 核心 + 25+ API = 115+ 测试。

---

## 推进规则

1. 每次只推进一个模块，完成后按 `docs/提示词.md` 第 11 节自查清单逐项确认。
2. 模块完成 → 状态改 `done`，并在 `.codebuddy/memory/` 记录。
3. 用户可随时调整优先级 / 增删模块；调整后以 ROADMAP 为准。
4. 新模块候选须先过 `docs/project-positioning.md` 的边界三问：
   （是 Agent 后端基础设施？是否需要多种实现？是否涉及业务决策？）
