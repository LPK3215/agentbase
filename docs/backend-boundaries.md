# 后端功能边界

> 本文记录 `AgentBase` 后端脚手架的功能边界：**已实现什么、部分实现什么、未实现什么**。
> 供开源用户和二次开发者快速判断"这个项目能不能做 X"。

---

## 1. API 服务层

### 已实现（110 个路由 + 1 个 WebSocket + 3 个自动文档端点）

| 端点 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `/health` | GET | 公开 | 健康检查，返回版本、Agent 列表、认证状态 |
| `/metrics` | GET | 公开 | Prometheus 格式指标 |
| `/agents` | GET | 需要 | 列出所有 Agent 配置 |
| `/agents/{name}` | GET | 需要 | 获取 Agent 配置详情 |
| `/agents/{name}/configurable` | GET | 需要 | 获取可配置字段（Context Schema） |
| `/agents/{name}/invoke` | POST | 需要 | 同步调用 Agent |
| `/agents/{name}/stream` | POST | 需要 | SSE 流式调用 Agent |
| `/agents/{name}/resume` | POST | 需要 | 恢复中断的 Agent |
| `/queue/submit` | POST | 需要 | 提交异步任务 |
| `/queue/{task_id}` | GET | 需要 | 查询任务状态 |
| `/queue` | GET | 需要 | 列出任务（支持过滤） |
| `/queue/{task_id}` | DELETE | 需要 | 取消任务 |
| `/queue/process` | POST | 需要 | 批量处理待执行任务 |
| `/documents/upload` | POST | 需要 | 上传文件到知识库（multipart） |
| `/documents` | GET | 需要 | 列出知识库文档 |
| `/documents/{id}` | GET | 需要 | 获取文档详情 |
| `/documents/{id}` | DELETE | 需要 | 删除文档 |
| `/documents/search` | POST | 需要 | 搜索知识库 |
| `/audit/events` | GET | 需要 | 查询审计日志（分页、过滤） |
| `/audit/events/count` | GET | 需要 | 统计审计事件数量（支持过滤） |
| `/audit/events/export` | GET | 需要 | 导出审计日志（JSON/CSV/YAML，支持过滤，文件下载） |
| `/usage/stats` | GET | 需要 | 聚合用量统计（token 计数、成本、按模型/Agent/用户分组） |
| `/usage/records` | GET | 需要 | 查询用量记录（分页、过滤） |
| `/usage/summary` | GET | 需要 | 用量汇总（总调用数、总 token、总成本） |
| `/usage/records` | DELETE | 需要 | 清除所有用量记录 |
| `/webhooks` | GET | 需要 | 列出所有 Webhook 端点 |
| `/webhooks` | POST | 需要 | 注册 Webhook 端点 |
| `/webhooks/{endpoint_id}` | GET | 需要 | 获取 Webhook 端点详情 |
| `/webhooks/{endpoint_id}` | PATCH | 需要 | 更新 Webhook 端点字段 |
| `/webhooks/{endpoint_id}` | DELETE | 需要 | 删除 Webhook 端点 |
| `/webhooks/{endpoint_id}/test` | POST | 需要 | 发送测试事件到端点（同步） |
| `/webhooks/deliveries` | GET | 需要 | 查询投递记录（分页、过滤） |
| `/webhooks/stats` | GET | 需要 | Webhook 投递聚合统计 |
| `/feedback` | GET | 需要 | 查询用户反馈记录（分页、多维度过滤） |
| `/feedback` | POST | 需要 | 提交用户反馈（评分/评论/标签） |
| `/feedback/stats` | GET | 需要 | 聚合反馈统计（平均分/情感分布/按 Agent 分组） |
| `/feedback/{record_id}` | GET | 需要 | 获取反馈记录详情 |
| `/feedback/{record_id}` | PATCH | 需要 | 更新反馈字段（评分/评论/标签） |
| `/feedback/{record_id}` | DELETE | 需要 | 删除反馈记录 |
| `/notifications` | GET | 需要 | 列出通知（分页、多维度过滤） |
| `/notifications` | POST | 需要 | 创建通知（指定用户或广播） |
| `/notifications/stats` | GET | 需要 | 聚合通知统计（未读数/按分类/按严重度） |
| `/notifications/unread-count` | GET | 需要 | 获取用户未读通知数 |
| `/notifications/broadcast` | POST | 需要 | 广播通知给所有用户 |
| `/notifications/read-all` | POST | 需要 | 标记用户所有通知为已读 |
| `/notifications/{notification_id}` | GET | 需要 | 获取通知详情 |
| `/notifications/{notification_id}` | PATCH | 需要 | 更新通知字段 |
| `/notifications/{notification_id}/read` | POST | 需要 | 标记通知为已读 |
| `/notifications/{notification_id}/unread` | POST | 需要 | 标记通知为未读 |
| `/notifications/{notification_id}` | DELETE | 需要 | 删除通知 |
| `/conversations` | GET | 需要 | 列出对话历史（分页、多维度过滤） |
| `/conversations/stats` | GET | 需要 | 聚合对话统计（总数/消息数/按 Agent/用户分组） |
| `/conversations/{thread_id}` | GET | 需要 | 获取对话历史（含消息列表） |
| `/conversations/{thread_id}` | PATCH | 需要 | 更新对话元数据（标题/标签/归档） |
| `/conversations/{thread_id}` | DELETE | 需要 | 删除对话及其所有消息 |
| `/schedules` | GET | 需要 | 列出定时任务（分页、按 Agent/启用/名称过滤） |
| `/schedules` | POST | 需要 | 创建定时任务（interval 秒级 / cron 5 字段表达式） |
| `/schedules/stats` | GET | 需要 | 聚合调度统计（任务数/成功失败运行数/按 Agent 分组） |
| `/schedules/{task_id}` | GET | 需要 | 获取定时任务详情 |
| `/schedules/{task_id}` | PATCH | 需要 | 更新任务字段（改调度规则自动重算 next_run_at） |
| `/schedules/{task_id}` | DELETE | 需要 | 删除任务及其运行历史 |
| `/schedules/{task_id}/pause` | POST | 需要 | 暂停任务（保留配置） |
| `/schedules/{task_id}/resume` | POST | 需要 | 恢复任务（重算 next_run_at） |
| `/schedules/{task_id}/trigger` | POST | 需要 | 手动立即触发（暂停任务也可触发） |
| `/schedules/{task_id}/runs` | GET | 需要 | 查询运行历史（按状态/触发方式/时间过滤，分页） |
| `/experiments` | GET | 需要 | 列出所有 A/B 测试实验 |
| `/experiments` | POST | 需要 | 创建 A/B 测试实验 |
| `/experiments/{name}` | GET | 需要 | 获取实验详情 |
| `/experiments/{name}` | DELETE | 需要 | 删除实验及其结果 |
| `/experiments/{name}/assign` | POST | 需要 | 分配请求到变体 |
| `/experiments/{name}/results` | POST | 需要 | 记录实验结果 |
| `/experiments/{name}/stats` | GET | 需要 | 获取实验统计 |
| `/models` | GET | 需要 | 列出所有已注册模型配置 |
| `/models` | POST | 需要 | 注册/替换模型配置 |
| `/models/{name}` | GET | 需要 | 获取模型配置详情 |
| `/models/{name}` | PATCH | 需要 | 更新模型配置字段 |
| `/models/{name}` | DELETE | 需要 | 删除模型配置 |
| `/models/{name}/test` | POST | 需要 | 测试模型连通性（发送测试 prompt） |
| `/prompts` | GET | 需要 | 列出所有已注册提示词模板 |
| `/prompts` | POST | 需要 | 注册/替换提示词模板 |
| `/prompts/{name}` | GET | 需要 | 获取提示词模板详情 |
| `/prompts/{name}` | PATCH | 需要 | 更新提示词模板字段 |
| `/prompts/{name}` | DELETE | 需要 | 删除提示词模板 |
| `/prompts/{name}/render` | POST | 需要 | 渲染提示词模板（变量替换） |
| `/users` | GET | 需要 | 列出所有已注册用户 |
| `/users` | POST | 需要 | 注册/替换用户 |
| `/users/{username}` | GET | 需要 | 获取用户详情 |
| `/users/{username}` | PATCH | 需要 | 更新用户字段 |
| `/users/{username}` | DELETE | 需要 | 删除用户 |
| `/auth/register` | POST | 需要 | 用户注册（注册流程） |
| `/auth/login` | POST | 需要 | 用户登录认证 |
| `/auth/oauth2/{provider}/authorize` | GET | 公开 | OAuth2 授权重定向（Google/GitHub） |
| `/auth/oauth2/{provider}/callback` | GET | 公开 | OAuth2 回调（交换 Token、自动注册/匹配用户、签发 JWT） |
| `/auth/oauth2/providers` | GET | 公开 | 列出已配置的 OAuth2 提供商 |
| `/sessions` | GET | 需要 | 列出所有会话（支持 agent/status 过滤） |
| `/sessions/stats` | GET | 需要 | 会话统计（按状态计数） |
| `/sessions/{thread_id}` | GET | 需要 | 获取会话详情 |
| `/sessions/{thread_id}` | DELETE | 需要 | 取消会话（标记为 cancelled） |
| `/sessions/cleanup` | POST | 需要 | 清理会话（expired/stale/completed 模式） |
| `/admin/rate-limit` | GET | 需要 | 查看当前速率限制桶状态 |
| `/admin/rate-limit/buckets` | DELETE | 需要 | 清空所有速率限制桶 |
| `/admin/rate-limit/quotas/{role}` | POST | 需要 | 设置角色速率限制配额 |
| `/apikeys` | GET | 需要 | 列出所有 API Key（不含 hash） |
| `/apikeys` | POST | 需要 | 创建 API Key（返回原始 key） |
| `/apikeys/{key_id}` | GET | 需要 | 获取 API Key 详情 |
| `/apikeys/{key_id}` | PATCH | 需要 | 更新 API Key 字段 |
| `/apikeys/{key_id}` | DELETE | 需要 | 删除 API Key |
| `/apikeys/{key_id}/revoke` | POST | 需要 | 吊销（禁用）API Key |
| `/apikeys/verify` | POST | 需要 | 验证 API Key 有效性 |
| `/ws/agents/{name}` | WebSocket | Token | 实时双向 Agent 对话 |
| `/docs` | GET | 公开 | Swagger UI |
| `/redoc` | GET | 公开 | ReDoc 文档 |

### 安全功能

| 功能 | 状态 | 配置方式 |
|------|------|---------|
| API Key 认证 | ✅ 已实现 | `AGENTBASE_API_KEY` 环境变量，空值=禁用 |
| JWT 认证 | ✅ 已实现 | `auth.type: jwt`，HMAC-SHA256 签名，支持 Token 过期，已集成到 API 中间件 |
| RBAC 角色权限 | ✅ 已实现 | admin/user/readonly 三级角色，路径级权限控制，JWT payload 自动校验 |
| CORS 中间件 | ✅ 已实现 | `cors.allow_origins` 配置，默认 `*`；支持 `AGENTBASE_CORS_ORIGINS` 环境变量 |
| 速率限制 | ✅ 已实现 | `rate_limit` 配置段：`max_requests`/`window_seconds`/`burst`，可配置 |
| OAuth2 第三方登录 | ✅ 已实现 | `oauth2` 配置段：Google/GitHub 授权码流程，State CSRF 防护，自动注册/匹配用户 |
| 定时任务调度 | ✅ 已实现 | `scheduler` 配置段：interval 秒级 / cron 5 字段表达式定时调用 Agent，暂停/恢复/手动触发/运行历史，后台 tick 线程 + worker 池 |
| 全局异常处理 | ✅ 已实现 | 返回 `{"error": "...", "code": "...", "http_status": N, "request_id": "..."}` |
| 请求 ID 关联 | ✅ 已实现 | `X-Request-ID` 头，自动生成 UUID，传播到日志和 tracer |
| 分页 | ✅ 已实现 | `/queue`、`/documents`、`/audit/events`、`/feedback`、`/notifications`、`/conversations`、`/schedules`、`/schedules/{task_id}/runs`、`/webhooks/deliveries`、`/usage/records` 支持 `page`/`page_size` 参数 |
| WebSocket 心跳 | ✅ 已实现 | 30 秒心跳间隔，防止连接超时 |

### 未实现

- 无前端 UI（只有 Swagger API 文档）
- ~~OAuth2 第三方登录~~ → 已实现（Google/GitHub），详见上方端点表

---

## 2. 数据存储

### 已实现

| 存储层 | 后端 | 持久化 | 健康检查 | 重连 | 事务 | 配置 |
|--------|------|--------|---------|------|------|------|
| Agent 会话检查点 | PostgreSQL | ✅ | ✅ | ✅ | ✅ | `checkpointer.type: postgres` |
| Agent 会话检查点 | SQLite | ✅ | ✅ | — | ✅ | `checkpointer.type: sqlite` |
| Agent 会话检查点 | 内存 | ❌ | — | — | — | `checkpointer.type: memory` |
| Agent 会话检查点 | MySQL | ✅ | ✅ | ✅ | ✅ | `checkpointer.type: mysql` |
| 长期记忆 (Memory) | PostgreSQL | ✅ | ✅ | ✅ | ✅ | `storage.type: postgres` |
| 长期记忆 (Memory) | SQLite | ✅ | ✅ | — | ✅ | `storage.type: sqlite` |
| 长期记忆 (Memory) | MySQL | ✅ | ✅ | ✅ | ✅ | `storage.type: mysql` |
| 长期记忆 (Memory) | MongoDB | ✅ | ✅ | ✅ | ✅ | `storage.type: mongodb` |
| 知识库 (KB) | PostgreSQL + pgvector | ✅ | ✅ | ✅ | ✅ | `storage.type: postgres` |
| 知识库 (KB) | SQLite | ✅ | ✅ | — | ✅ | `storage.type: sqlite` |
| 知识库 (KB) | MySQL | ✅ | ✅ | ✅ | ✅ | `storage.type: mysql` |
| 知识库 (KB) | MongoDB | ✅ | ✅ | ✅ | ✅ | `storage.type: mongodb` |
| 技能 (Skills) | 文件系统 | ✅ | — | — | — | `workspace/skills/*.md` |
| 工作区文件 | 文件系统 | ✅ | — | — | — | `workspace/` 目录 |

### 自动 SQL 方言转换

PostgreSQL 和 MySQL 后端会自动将 SQLite 风格的 SQL 转换（`AUTOINCREMENT` → `SERIAL`/`AUTO_INCREMENT`，`?` → `%s`），上层代码无需感知数据库类型。MongoDB 后端通过内置 SQL→MongoDB 适配层，将 `INSERT`/`SELECT`/`UPDATE`/`DELETE` 翻译为 `insert_one`/`find`/`update_many`/`delete_many`，上层代码同样无需修改。

### 数据备份/恢复

| 功能 | 状态 | 命令 |
|------|------|------|
| 数据库备份 | ✅ 已实现 | `agentbase backup -o backup.sql --format sql` |
| 数据库备份 (JSON) | ✅ 已实现 | `agentbase backup -o backup.json --format json` |
| 数据库恢复 | ✅ 已实现 | `agentbase restore backup.sql --format sql` |
| 数据库恢复 (JSON) | ✅ 已实现 | `agentbase restore backup.json --format json` |

### 迁移指南

详细的 Provider 迁移步骤见 `docs/migrations/`：

- [SQLite → PostgreSQL](./migrations/sqlite-to-postgresql.md)
- [Memory Queue → Redis Queue](./migrations/memory-to-redis.md)
- [Null Tracer → Langfuse](./migrations/null-to-langfuse.md)

### 未实现

- 向量数据库（Milvus、Pinecone、Weaviate）—— 当前用 pgvector 替代

---

## 3. RAG 检索

### 文档解析（已实现 9 种）

| 格式 | 解析器 | 依赖 | 实现方式 | 输出 |
|------|--------|------|---------|------|
| txt, csv, json, yaml, py, js, sql, sh... | `TextParser` | 无 | 直接读取 | 原文 |
| md, markdown, rst | `MarkdownParser` | 无 | 直接读取 | 原文 |
| pdf | `PdfParser` | `pymupdf` | 规则提取文本 | 纯文本（每页拼接） |
| docx | `DocxParser` | `python-docx` | 规则提取段落+表格 | 纯文本（段落+表格） |
| html, htm | `HtmlParser` | `beautifulsoup4` | 去标签提取 | 纯文本 |
| xlsx, xls | `ExcelParser` | `openpyxl` | 逐行提取 | Markdown 表格格式 |
| pptx, ppt | `PptxParser` | `python-pptx` | 逐幻灯片提取文本+表格 | Markdown 格式 |
| 任意（虚拟扩展） | `LLMDocumentParser` | `openai` | 大模型 API 转结构化 Markdown | 高质量 Markdown |
| 任意（虚拟扩展） | `OCRParser` | `pytesseract` + `pillow` + `pdf2image` | OCR 识别 | 纯文本 |

### 文本分块

| 策略 | 状态 | 参数 |
|------|------|------|
| 段落分块（默认） | ✅ 已实现 | `max_chunk_size=500`，按双换行分段后合并 |
| 递归分块（标题→段落→句子） | ✅ 已实现 | `strategy="recursive"`，支持 `overlap` 参数 |
| 固定字符数分块 | ✅ 已实现 | `strategy="fixed"`，无段落意识 |

### 向量化（Embedding）

| Provider | 状态 | 语义质量 | 需要 API | 说明 |
|----------|------|---------|---------|------|
| `hash` | ✅ 已实现 | ❌ 无语义 | 否 | 确定性哈希，仅测试用 |
| `openai` | ✅ 已实现 | ✅ 真实语义 | 是 | 调 OpenAI/SiliconFlow API |
| `sentence-transformers` | ✅ 已实现 | ✅ 真实语义 | 否（本地运行） | HuggingFace 本地模型，离线运行 |
| `none` | ✅ 已实现（默认） | — | 否 | 禁用向量搜索，退化为文本匹配 |

### 向量检索

| 检索方式 | 状态 | 说明 |
|---------|------|------|
| pgvector 余弦距离 (`<=>`) | ✅ 已实现 | PostgreSQL 扩展，IVFFlat 索引 |
| 内存余弦相似度 | ✅ 已实现 | 回退方案 |
| 文本 LIKE 匹配 | ✅ 已实现 | 无 embedding 时的回退，含相关性评分（标题匹配 0.5 + 内容匹配 0.3 + 词频加分） |
| 混合检索（向量+关键词+图谱 RRF） | ✅ 已实现 | `fuse_results_rrf()` 函数 |
| 重排序（Reranker） | ✅ 已实现 | `CrossEncoderReranker`，cross-encoder 模型 |
| 批量摄入 | ✅ 已实现 | `batch_ingest()` 方法，支持多文件一次性摄入 |
| 知识库统计 | ✅ 已实现 | `get_stats()` 方法，返回文档数/分块数/内容总量/embedding 状态 |

---

## 4. 异步任务队列

| Provider | 状态 | 持久化 | 说明 |
|----------|------|--------|------|
| `none` | ✅ 已实现（默认） | — | 同步模式，无队列 |
| `memory` | ✅ 已实现 | ❌ 进程丢失 | 内存队列，测试/单机用 |
| `redis` | ✅ 已实现 | ✅ 持久化 | Redis JSON 存储，多进程安全 |
| `celery` | ✅ 已实现 | ✅ 持久化 | Celery 分布式任务（RabbitMQ/Redis broker），多节点多worker |

### 未实现

- RabbitMQ 原生队列（可通过 Celery + RabbitMQ broker 实现）

---

## 5. Agent 工具（37 个）

### 已实现

| 工具 | 数量 | 说明 |
|------|------|------|
| 文件操作 | 3 | `read_file`（1MB 限制+二进制检测）, `write_file`, `grep`（200 结果限制+二进制跳过） |
| 时间 | 2 | `get_time`, `now_local` |
| 工作区 | 2 | `echo`, `list_workspace` |
| 技能管理 | 6 | `skill_list`, `skill_get`, `skill_create`, `skill_update`, `skill_delete`, `skill_search` |
| 记忆管理 | 7 | `memory_save`, `memory_get`, `memory_list`, `memory_search`, `memory_delete`, `memory_count`, `memory_batch_save` |
| 知识库 | 8 | `kb_add`, `kb_get`, `kb_list`, `kb_search`, `kb_update`, `kb_delete`, `kb_ingest`, `kb_batch_ingest` |
| Web | 3 | `web_search`, `web_fetch`（超时+重试+Content-Type 校验+编码检测）, `http_request`（GET/POST/PUT/PATCH/DELETE+超时+重定向限制+响应截断+结构化返回） |
| 数据库查询 | 1 | `db_query` — 只读 SELECT 查询（SELECT 强制/DDL-DML 拦截/表白名单/行数上限/超时/结构化返回） |
| MCP | 2 | `mcp_list_tools`, `mcp_call_tool` |
| 代码执行 | 1 | `code_execute` — 沙箱 Python 执行（代码/输出大小限制+代理新env+超时上限） |
| 音频转录 | 1 | `transcribe` — Whisper API/本地转录 |
| 邮件发送 | 1 | `email_sender` — SMTP 邮件发送（纯文本/HTML/多收件人/CC/BCC/SSL/TLS 认证/超时控制/结构化返回） |

### 未实现

- 日程管理工具

---

## 6. 中间件（9 个）

| 中间件 | 状态 | 说明 |
|--------|------|------|
| `request_logger` | ✅ 已实现 | 记录模型调用请求和响应，含 `duration_ms` 耗时追踪和结构化 `extra` 字段 |
| `retry` | ✅ 已实现 | 指数退避重试 + 抖动，区分可重试/不可重试错误（认证错误立即失败） |
| `timeout` | ✅ 已实现 | 超时控制，复用共享线程池避免每次调用创建新线程 |
| `summary` | ✅ 已实现 | L1/L2 对话历史压缩 |
| `cache` | ✅ 已实现 | 线程安全缓存，`OrderedDict` LRU 淘汰 + TTL 过期 + 命中率统计 |
| `redact_output` | ✅ 已实现 | 响应脱敏中间件，基于 RedactionManager 对模型输出做 PII/密钥掩码 |
| `rate_limit` | ✅ 已实现 | 模型调用限流中间件，按 agent/全局 token bucket 限流，支持 burst 突发 |
| `model_router` | ✅ 已实现 | 多模型路由中间件，支持 round_robin/weighted/random/failover 策略，通过 `wrap_model_call` 替换 `request.model` |
| `audit_log` | ✅ 已实现 | 审计日志中间件，自动记录模型调用的 AuditEvent（actor/action/resource/result/duration），复用 AuditManager |

### 未实现

（无）

---

## 7. 可插拔 Provider（26 个注册表）

| Provider 类型 | 默认实现 | 可替换 | 替换方式 |
|--------------|---------|--------|---------|
| 文档解析器 | TextParser, MarkdownParser | ✅ | `@register_parser(".ext")` |
| Embedding | NoneEmbeddingProvider / HashEmbedding | ✅ | `@register_embedding_provider("name")` |
| Web 搜索 | NullSearchProvider / DuckDuckGoSearch | ✅ | `@register_search_provider("name")` |
| MCP 客户端 | NullMCPClient / MemoryMCPClient | ✅ | `@register_mcp_client("name")` |
| 队列 | NullQueueProvider (sync) / MemoryRequestQueue | ✅ | `@register_queue_provider("name")` |
| 追踪器 | NullTracer | ✅ | `@register_tracer_provider("name")` |
| 知识图谱 | NullGraphProvider | ✅ | `@register_graph_provider("name")` |
| 存储 | SQLiteBackend / PostgresBackend / MySQLBackend / MongoDBBackend | ✅ | 配置切换 `storage.type` |
| 检查点 | MemorySaver / SqliteSaver / PostgresSaver / MySQLSaver | ✅ | 配置切换 `checkpointer.type` |
| 模型管理 | InMemoryModelProvider / NullModelProvider | ✅ | `@register_model_provider("name")` |
| 提示词管理 | InMemoryPromptProvider / NullPromptProvider | ✅ | `@register_prompt_provider("name")` |
| 用户管理 | InMemoryUserProvider / NullUserProvider | ✅ | `@register_user_provider("name")` |
| API Key 管理 | InMemoryApiKeyProvider / NullApiKeyProvider | ✅ | `@register_apikey_provider("name")` |
| 用量追踪 | InMemoryUsageProvider / NullUsageProvider | ✅ | `@register_usage_provider("name")` |
| Webhook 通知 | InMemoryWebhookProvider / NullWebhookProvider | ✅ | `@register_webhook_provider("name")` |
| 用户反馈 | InMemoryFeedbackProvider / NullFeedbackProvider | ✅ | `@register_feedback_provider("name")` |
| 通知中心 | InMemoryNotificationProvider / NullNotificationProvider | ✅ | `@register_notification_provider("name")` |
| 对话历史 | InMemoryConversationProvider / NullConversationProvider | ✅ | `@register_conversation_provider("name")` |
| 定时任务调度 | InMemoryScheduleProvider / NullScheduleProvider | ✅ | `@register_schedule_provider("name")` |
| 审计日志 | SQLiteAuditProvider / NullAuditProvider | ✅ | `@register_audit_provider("name")` |
| A/B 实验 | InMemoryExperimentProvider / NullExperimentProvider | ✅ | `@register_experiment_provider("name")` |
| 敏感信息脱敏 | RuleRedactionProvider / NullRedactionProvider | ✅ | `@register_redaction_provider("name")` |
| 密钥加密存储 | FernetSecretsProvider / NullSecretsProvider | ✅ | `@register_secrets_provider("name")` |
| 工具（扩展注册表） | 37 个内置工具 | ✅ | `@register_tool("name")` |
| 子代理（扩展注册表） | researcher, general_helper | ✅ | `@register_subagent("name")` |
| 中间件（扩展注册表） | 9 个内置中间件 | ✅ | `@register_middleware("name")` |

> **不可替换的内置服务**：数据库迁移（`MigrationManager`，内部使用 Alembic）和 OAuth2 登录（`GoogleOAuth2Provider` / `GitHubOAuth2Provider`，内置 Google/GitHub）不通过注册表机制替换。

### 内置但未默认启用的 Provider

| Provider | 状态 | 启用方式 |
|----------|------|---------|
| OpenAI Embedding | ✅ 已验证 | `embedding.provider: openai` |
| SentenceTransformers 本地 Embedding | ✅ 已验证 | `embedding.provider: sentence-transformers` |
| Tavily 搜索 | ✅ 已验证 | `web_search.provider: tavily` |
| InMemoryTracer | ✅ 已验证 | `tracer.provider: memory` |
| Langfuse 追踪 | ✅ 已验证 | `tracer.provider: langfuse` |
| OpenTelemetry 追踪 | ✅ 已验证 | `tracer.provider: opentelemetry` |
| Redis 队列 | ✅ 已验证 | `queue.provider: redis` |
| Celery 分布式队列 | ✅ 已验证 | `queue.provider: celery` |
| Neo4j 知识图谱 | ✅ 已验证 | `graph.provider: neo4j` |
| MySQL 存储 | ✅ 已验证 | `storage.type: mysql` |
| MongoDB 存储 | ✅ 已验证 | `storage.type: mongodb` |
| LLM 文档解析 | ✅ 已验证 | 直接实例化 `LLMDocumentParser()` |
| OCR 解析 | ✅ 已验证 | 直接实例化 `OCRParser()` |
| 模型管理 | ✅ 已验证 | `model_manager.enabled: true` + `model_manager.provider: memory` |
| 提示词管理 | ✅ 已验证 | `prompt_manager.enabled: true` + `prompt_manager.provider: memory` |
| 用户管理 | ✅ 已验证 | `user_manager.enabled: true` + `user_manager.provider: memory` |
| API Key 管理 | ✅ 已验证 | `apikey_manager.enabled: true` + `apikey_manager.provider: memory` |
| Token 用量追踪 | ✅ 已验证 | `usage.enabled: true` + `usage.provider: memory` |
| Webhook 事件通知 | ✅ 已验证 | `webhook.enabled: true` + `webhook.provider: memory` |
| 用户反馈收集 | ✅ 已验证 | `feedback.enabled: true` + `feedback.provider: memory` |
| 通知中心 | ✅ 已验证 | `notification.enabled: true` + `notification.provider: memory` |
| 对话历史 | ✅ 已验证 | `conversation.enabled: true` + `conversation.provider: memory` |
| 定时任务调度 | ✅ 已验证 | `scheduler.enabled: true` + `scheduler.provider: memory` |

### 未实现

- ~~Celery/RabbitMQ 队列~~ → 已实现（Celery 队列 + RabbitMQ/Redis broker）

---

## 8. 追踪与可观测性

| 功能 | 状态 | 说明 |
|------|------|------|
| 结构化 JSON 日志 | ✅ 已实现 | 7 个必填字段（timestamp/level/event/thread_id/agent/duration_ms/request_id），含 `duration_ms` 执行时长追踪 |
| 密钥脱敏 | ✅ 已实现 | 日志中自动脱敏 API Key 和 DSN 密码 |
| Prometheus 指标 | ✅ 已实现 | `GET /metrics` — 请求计数/状态分布/延迟直方图/Agent 调用计数/错误码分布/WS 连接数 |
| 请求 ID 关联 | ✅ 已实现 | `X-Request-ID` 头，传播到 runner 日志和 tracer span |
| 追踪 (Tracing) | ✅ 已实现 | NullTracer + InMemoryTracer + LangfuseTracer + OpenTelemetryTracer，已集成到 invoke/stream/resume |
| 健康检查 | ✅ 已实现 | `GET /health` — 组件级探活（storage/queue/embedding/search/tracer），受 `health_check` 配置开关控制，返回 `status`/`components`/`storage_connected`/`queue_connected`/`embedding_connected`/`search_connected`/`tracer_connected` |
| 错误码体系 | ✅ 已实现 | `ErrorCode` 常量类，17 个领域（CONFIG/REG/FACTORY/RT/AUTH/RATE/QUEUE/KB/UPLOAD/WS/MIGRATION/USAGE/WEBHOOK/FEEDBACK/NOTIFICATION/CONVERSATION/SCHEDULE），HTTP 状态映射。另有 OAuth2 复用 AUTH 域 |

---

## 9. 评估框架

| 功能 | 状态 | 说明 |
|------|------|------|
| 评估运行器 | ✅ 已实现 | `EvaluationRunner`，批量跑测试用例 |
| 指标：关键词匹配 | ✅ 已实现 | `KeywordMatchMetric` |
| 指标：精确匹配 | ✅ 已实现 | `ExactMatchMetric` |
| 指标：子串匹配 | ✅ 已实现 | `SubstringMatchMetric` |
| 指标：LLM 评分 | ✅ 已实现 | `LLMJudgeMetric` |
| 指标：BLEU | ✅ 已实现 | `BLEUMetric`，纯 Python BLEU-4 |
| 指标：ROUGE-L | ✅ 已实现 | `ROUGEMetric`，LCS-based F1 |

### 未实现

- ~~A/B 测试框架~~ → 已实现（Experiment Provider + `/experiments` API）

---

## 10. 部署

| 方式 | 状态 | 说明 |
|------|------|------|
| Docker Compose | ✅ 已实现 | PostgreSQL (pgvector) + Redis + API |
| Dockerfile | ✅ 已实现 | 多阶段构建 |
| K8s 部署 | ✅ 已实现 | `deploy/k8s/` — Helm Chart + 原生 Manifests |
| Nginx 反向代理 | ✅ 已实现 | `deploy/nginx/nginx.conf` — 含 SSE/WebSocket/限流/TLS 模板 |
| 本地开发 | ✅ 已实现 | `agentbase serve --reload` |
| TLS/HTTPS | ✅ 已实现 | Nginx 配置模板中包含 HTTPS + HTTP→HTTPS 跳转 |

### 可选依赖安装

```bash
pip install agentbase[postgres]      # PostgreSQL 存储 + 检查点
pip install agentbase[api]          # FastAPI + uvicorn + SSE
pip install agentbase[openai]       # OpenAI 模型
pip install agentbase[anthropic]    # Anthropic 模型
pip install agentbase[google]       # Google 模型
pip install agentbase[embeddings]   # OpenAI + SentenceTransformers 向量化
pip install agentbase[search]       # Tavily 搜索
pip install agentbase[rag]          # PDF, DOCX, HTML, Excel, PPTX 解析
pip install agentbase[queue]        # Redis 持久化队列
pip install agentbase[tracing]      # Langfuse 追踪
pip install agentbase[ocr]          # OCR 识别
pip install agentbase[graph]        # Neo4j 知识图谱
pip install agentbase[mysql]        # MySQL 存储后端
pip install agentbase[mongodb]      # MongoDB 存储后端
pip install agentbase[celery]      # Celery 分布式队列
pip install agentbase[secrets]      # Fernet 密钥加密
pip install agentbase[otel]         # OpenTelemetry 追踪
pip install agentbase[transcribe]   # 音频/视频转录
pip install agentbase[all]          # 全部安装
```

---

## 11. CLI 命令

| 命令 | 状态 | 说明 |
|------|------|------|
| `agentbase doctor` | ✅ 已实现 | 14 项检查 |
| `agentbase agents` | ✅ 已实现 | 列出 Agent 配置 |
| `agentbase extensions` | ✅ 已实现 | 列出已注册扩展（`--verbose` 显示元数据） |
| `agentbase run` | ✅ 已实现 | 同步调用 Agent |
| `agentbase stream` | ✅ 已实现 | 流式调用 Agent |
| `agentbase resume` | ✅ 已实现 | 恢复中断的 Agent |
| `agentbase serve` | ✅ 已实现 | 启动 API 服务 |
| `agentbase worker` | ✅ 已实现 | 启动队列 worker 进程 |
| `agentbase version` | ✅ 已实现 | 打印版本信息（Python 版本、平台） |
| `agentbase config validate` | ✅ 已实现 | 验证配置文件（app config + agent configs + workspace 结构） |
| `agentbase config show` | ✅ 已实现 | 显示已解析的配置 |
| `agentbase backup` | ✅ 已实现 | 数据库备份（SQL/JSON 格式） |
| `agentbase restore` | ✅ 已实现 | 数据库恢复（SQL/JSON 格式） |
| `agentbase db init` | ✅ 已实现 | 初始化迁移脚本目录 |
| `agentbase db upgrade` | ✅ 已实现 | 升级数据库到最新 schema |
| `agentbase db downgrade` | ✅ 已实现 | 回退一步数据库 schema |
| `agentbase db current` | ✅ 已实现 | 查看当前迁移版本 |
| `agentbase db heads` | ✅ 已实现 | 查看头部迁移版本 |
| `agentbase db history` | ✅ 已实现 | 查看迁移历史 |
| `agentbase db stamp` | ✅ 已实现 | 标记数据库版本（不执行迁移） |

---

## 12. 安全与认证

| 功能 | 状态 | 说明 |
|------|------|------|
| API Key 认证 | ✅ 已实现 | Bearer Token / X-API-Key，常数时间比较（`hmac.compare_digest`） |
| JWT 认证 | ✅ 已实现 | HMAC-SHA256，Token 过期，自定义 claims，secret 为空时 fail-fast（`AGENTBASE_CONFIG_002`） |
| RBAC 权限控制 | ✅ 已实现 | admin/user/readonly 三级角色，路径级权限 |
| CORS | ✅ 已实现 | 可配置 origins，通配符 `*` 时自动禁用 credentials（CORS 规范） |
| 速率限制 | ✅ 已实现 | 每 IP 60 req/min，支持按角色动态配额（`quotas` 配置 + `/admin/rate-limit` API） |
| API Key 多 Key 管理 | ✅ 已实现 | `apikey_manager.enabled=true`，多 Key 生成/CRUD/吊销/验证/过期/使用统计，与 Bearer Token 认证集成 |
| Token 用量追踪 | ✅ 已实现 | `usage.enabled=true`，自动记录 prompt/completion/total tokens + 成本估算，按 Agent/模型/用户/时间聚合统计 |
| Webhook 事件通知 | ✅ 已实现 | `webhook.enabled=true`，注册端点接收 HTTP POST 事件通知，支持通配符事件订阅、HMAC-SHA256 签名、指数退避重试 |
| 用户反馈收集 | ✅ 已实现 | `feedback.enabled=true`，用户评分（1-5 星或 ±1 thumbs）+ 评论 + 标签，按 Agent/线程/情感聚合统计 |
| 通知中心 | ✅ 已实现 | `notification.enabled=true`，应用内通知（创建/查询/标记已读/广播），按用户/分类/严重度聚合统计，支持过期自动过滤 |
| 对话历史 | ✅ 已实现 | `conversation.enabled=true`，自动记录 invoke/stream/resume 对话消息，按用户/Agent/时间过滤，支持标题/标签/归档管理 |

### 未实现

- ~~OAuth2 第三方登录~~ → 已实现（Google/GitHub 授权码流程，State CSRF 防护，自动注册/匹配用户，签发 JWT）
- ~~API 限流配额管理（只有固定阈值）~~ → 已实现（按角色动态配额 + `/admin/rate-limit` 管理端点）

> 安全与认证功能已全部实现。

---

## 13. 音频/视频处理

| 功能 | 状态 | 说明 |
|------|------|------|
| 音频转录 (API) | ✅ 已实现 | OpenAI Whisper API |
| 音频转录 (本地) | ✅ 已实现 | 本地 whisper 模型 |
| 视频转录 | ✅ 已实现 | 提取音频后转录 |

---

## 14. 测试

| 指标 | 数值 |
|------|------|
| 总测试数 | 2686 |
| 失败数 | 0 |
| 覆盖率 | 79% |
| 覆盖率门槛 | 60%（CI 强制） |
| ruff lint | 0 errors |
| isort | 0 errors |
| CLI 命令 | 20 |
| 错误码领域 | 16 |

---

## 15. 文档

| 文档 | 状态 |
|------|------|
| README.md | ✅ 完整 |
| docs/quickstart.md | ✅ 11 步端到端教程 |
| docs/configuration.md | ✅ 全部配置项 |
| docs/core-services.md | ✅ 26 项核心服务/组件概览 + 10 个详细说明 |
| docs/extensions.md | ✅ 扩展开发指南 |
| docs/error-codes.md | ✅ 错误码注册表 |
| docs/backend-boundaries.md | ✅ 本文档 |
| examples/ | ✅ Cookbook 示例库（11 个可运行脚本，覆盖 7 个基础注册表 + 2 个扩展类型 + 2 个配置切换） |
| deploy/k8s/ | ✅ K8s Helm Chart + Manifests |
| deploy/nginx/ | ✅ Nginx 反向代理配置 |

---

## 16. 仍待开发

| 优先级 | 功能 | 说明 |
|--------|------|------|
| P5 | 前端 UI | Web 管理界面 |

> ROADMAP 中规划的全部后端模块（A1–A4, B1–B2, C1–C3, D1–D2, F1–F2, G1–G13）均已实现。详见 [ROADMAP](./ROADMAP.md)。
