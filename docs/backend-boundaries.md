# 后端功能边界

> 本文记录 `AgentBase` 后端脚手架的功能边界：**已实现什么、部分实现什么、未实现什么**。
> 供开源用户和二次开发者快速判断"这个项目能不能做 X"。

---

## 1. API 服务层

### 已实现（23 个端点）

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
| 全局异常处理 | ✅ 已实现 | 返回 `{"error": "...", "code": "...", "http_status": N, "request_id": "..."}` |
| 请求 ID 关联 | ✅ 已实现 | `X-Request-ID` 头，自动生成 UUID，传播到日志和 tracer |
| 分页 | ✅ 已实现 | `/queue` 和 `/documents` 支持 `page`/`page_size` 参数 |
| WebSocket 心跳 | ✅ 已实现 | 30 秒心跳间隔，防止连接超时 |

### 未实现

- 无前端 UI（只有 Swagger API 文档）
- 无 OAuth2 第三方登录（Google/GitHub 等）

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
| 知识库 (KB) | PostgreSQL + pgvector | ✅ | ✅ | ✅ | ✅ | `storage.type: postgres` |
| 知识库 (KB) | SQLite | ✅ | ✅ | — | ✅ | `storage.type: sqlite` |
| 知识库 (KB) | MySQL | ✅ | ✅ | ✅ | ✅ | `storage.type: mysql` |
| 技能 (Skills) | 文件系统 | ✅ | — | — | — | `workspace/skills/*.md` |
| 工作区文件 | 文件系统 | ✅ | — | — | — | `workspace/` 目录 |

### 自动 SQL 方言转换

PostgreSQL 和 MySQL 后端会自动将 SQLite 风格的 SQL 转换（`AUTOINCREMENT` → `SERIAL`/`AUTO_INCREMENT`，`?` → `%s`），上层代码无需感知数据库类型。

### 数据备份/恢复

| 功能 | 状态 | 命令 |
|------|------|------|
| 数据库备份 | ✅ 已实现 | `agentbase backup -o backup.sql --format sql` |
| 数据库备份 (JSON) | ✅ 已实现 | `agentbase backup -o backup.json --format json` |
| 数据库恢复 | ✅ 已实现 | `agentbase restore backup.sql --format sql` |
| 数据库恢复 (JSON) | ✅ 已实现 | `agentbase restore backup.json --format json` |

### 未实现

- MongoDB 支持
- 向量数据库（Milvus、Pinecone、Weaviate）—— 当前用 pgvector 替代
- 数据库迁移脚本（Alembic）

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
| `hash` | ✅ 已实现（默认） | ❌ 无语义 | 否 | 确定性哈希，仅测试用 |
| `openai` | ✅ 已实现 | ✅ 真实语义 | 是 | 调 OpenAI/SiliconFlow API |
| `sentence-transformers` | ✅ 已实现 | ✅ 真实语义 | 否（本地运行） | HuggingFace 本地模型，离线运行 |
| `none` | ✅ 已实现 | — | 否 | 禁用向量搜索，退化为文本匹配 |

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

### 未实现

- Celery 队列
- RabbitMQ 队列

---

## 5. Agent 工具（33 个）

### 已实现

| 工具 | 数量 | 说明 |
|------|------|------|
| 文件操作 | 4 | `read_file`（1MB 限制+二进制检测）, `write_file`, `grep`（200 结果限制+二进制跳过）, `list_workspace` |
| 时间 | 2 | `get_time`, `now_local` |
| 其他 | 2 | `echo` |
| 技能管理 | 6 | `skill_list`, `skill_get`, `skill_create`, `skill_update`, `skill_delete`, `skill_search` |
| 记忆管理 | 5 | `memory_save`, `memory_get`, `memory_list`, `memory_search`, `memory_delete` |
| 知识库 | 8 | `kb_add`, `kb_get`, `kb_list`, `kb_search`, `kb_update`, `kb_delete`, `kb_ingest`, `kb_batch_ingest` |
| Web | 3 | `web_search`, `web_fetch`（超时+重试+Content-Type 校验+编码检测）, `http_request`（GET/POST/PUT/PATCH/DELETE+超时+重定向限制+响应截断+结构化返回） |
| 数据库查询 | 1 | `db_query` — 只读 SELECT 查询（SELECT 强制/DDL-DML 拦截/表白名单/行数上限/超时/结构化返回） |
| MCP | 2 | `mcp_list_tools`, `mcp_call_tool` |
| 代码执行 | 1 | `code_execute` — 沙箱 Python 执行（代码/输出大小限制+代理新env+超时上限） |
| 音频转录 | 1 | `transcribe` — Whisper API/本地转录 |

### 未实现

- 邮件发送工具
- 日程管理工具

---

## 6. 中间件（6 个）

| 中间件 | 状态 | 说明 |
|--------|------|------|
| `request_logger` | ✅ 已实现 | 记录模型调用请求和响应，含 `duration_ms` 耗时追踪和结构化 `extra` 字段 |
| `retry` | ✅ 已实现 | 指数退避重试 + 抖动，区分可重试/不可重试错误（认证错误立即失败） |
| `timeout` | ✅ 已实现 | 超时控制，复用共享线程池避免每次调用创建新线程 |
| `summary` | ✅ 已实现 | L1/L2 对话历史压缩 |
| `cache` | ✅ 已实现 | 线程安全缓存，`OrderedDict` LRU 淘汰 + TTL 过期 + 命中率统计 |
| `redact_output` | ✅ 已实现 | 响应脱敏中间件，基于 RedactionManager 对模型输出做 PII/密钥掩码 |

### 未实现

- 多模型路由中间件（按任务复杂度选模型）

---

## 7. 可插拔 Provider（9 个注册表）

| Provider 类型 | 默认实现 | 可替换 | 替换方式 |
|--------------|---------|--------|---------|
| 文档解析器 | TextParser, MarkdownParser | ✅ | `@register_parser(".ext")` |
| Embedding | HashEmbedding | ✅ | `@register_embedding_provider("name")` |
| Web 搜索 | DuckDuckGoSearch | ✅ | `@register_search_provider("name")` |
| MCP 客户端 | MemoryMCPClient | ✅ | `@register_mcp_client("name")` |
| 队列 | MemoryRequestQueue | ✅ | `@register_queue_provider("name")` |
| 追踪器 | NullTracer | ✅ | `@register_tracer_provider("name")` |
| 知识图谱 | NullGraphProvider | ✅ | `@register_graph_provider("name")` |
| 存储 | SQLiteBackend / PostgresBackend / MySQLBackend | ✅ | 配置切换 `storage.type` |
| 检查点 | MemorySaver / SqliteSaver / PostgresSaver / MySQLSaver | ✅ | 配置切换 `checkpointer.type` |

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
| Neo4j 知识图谱 | ✅ 已验证 | `graph.provider: neo4j` |
| MySQL 存储 | ✅ 已验证 | `storage.type: mysql` |
| LLM 文档解析 | ✅ 已验证 | 直接实例化 `LLMDocumentParser()` |
| OCR 解析 | ✅ 已验证 | 直接实例化 `OCRParser()` |

### 未实现

- Celery/RabbitMQ 队列
- MongoDB 存储

---

## 8. 追踪与可观测性

| 功能 | 状态 | 说明 |
|------|------|------|
| 结构化 JSON 日志 | ✅ 已实现 | 6 个必填字段 + `duration_ms` 执行时长追踪 |
| 密钥脱敏 | ✅ 已实现 | 日志中自动脱敏 API Key 和 DSN 密码 |
| Prometheus 指标 | ✅ 已实现 | `GET /metrics` — 请求计数/状态分布/延迟直方图/Agent 调用计数/错误码分布/WS 连接数 |
| 请求 ID 关联 | ✅ 已实现 | `X-Request-ID` 头，传播到 runner 日志和 tracer span |
| 追踪 (Tracing) | ✅ 已实现 | NullTracer + InMemoryTracer + LangfuseTracer + OpenTelemetryTracer，已集成到 invoke/stream/resume |
| 健康检查 | ✅ 已实现 | `GET /health` — 组件级探活（storage/queue/embedding/search/tracer），受 `health_check` 配置开关控制，返回 `status`/`components`/`storage_connected`/`queue_connected`/`embedding_connected`/`search_connected`/`tracer_connected` |
| 错误码体系 | ✅ 已实现 | `ErrorCode` 常量类，10 个领域（CONFIG/REG/FACTORY/RT/AUTH/RATE/QUEUE/KB/UPLOAD/WS），HTTP 状态映射 |

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

- A/B 测试框架

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
pip install agentbase[rag]          # PDF, DOCX, HTML, Excel, PPTX 解析
pip install agentbase[embeddings]   # OpenAI + SentenceTransformers 向量化
pip install agentbase[queue]        # Redis 持久化队列
pip install agentbase[tracing]      # Langfuse 追踪
pip install agentbase[ocr]          # OCR 识别
pip install agentbase[graph]        # Neo4j 知识图谱
pip install agentbase[mysql]        # MySQL 存储后端
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
| `agentbase version` | ✅ 已实现 | 打印版本信息（Python 版本、平台） |
| `agentbase config validate` | ✅ 已实现 | 验证配置文件（app config + agent configs + workspace 结构） |
| `agentbase backup` | ✅ 已实现 | 数据库备份（SQL/JSON 格式） |
| `agentbase restore` | ✅ 已实现 | 数据库恢复（SQL/JSON 格式） |

---

## 12. 安全与认证

| 功能 | 状态 | 说明 |
|------|------|------|
| API Key 认证 | ✅ 已实现 | Bearer Token / X-API-Key |
| JWT 认证 | ✅ 已实现 | HMAC-SHA256，Token 过期，自定义 claims |
| RBAC 权限控制 | ✅ 已实现 | admin/user/readonly 三级角色，路径级权限 |
| CORS | ✅ 已实现 | 可配置 origins |
| 速率限制 | ✅ 已实现 | 每 IP 60 req/min |

### 未实现

- OAuth2 第三方登录
- API 限流配额管理（只有固定阈值）

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
| 总测试数 | 892 |
| 失败数 | 0 |
| 覆盖率 | 65% |
| 覆盖率门槛 | 60%（CI 强制） |
| ruff lint | 0 errors |
| isort | 0 errors |
| CLI 命令 | 10 |
| 错误码领域 | 10 |

---

## 15. 文档

| 文档 | 状态 |
|------|------|
| README.md | ✅ 完整 |
| docs/quickstart.md | ✅ 11 步端到端教程 |
| docs/configuration.md | ✅ 全部配置项 |
| docs/core-services.md | ✅ 13 个核心模块说明 |
| docs/extensions.md | ✅ 扩展开发指南 |
| docs/error-codes.md | ✅ 错误码注册表 |
| docs/backend-boundaries.md | ✅ 本文档 |
| examples/ | ✅ Cookbook 示例库（11 个可运行脚本，覆盖全部 9 个注册表 + 2 个扩展类型 + 2 个配置切换） |
| deploy/k8s/ | ✅ K8s Helm Chart + Manifests |
| deploy/nginx/ | ✅ Nginx 反向代理配置 |

---

## 16. 仍待开发

| 优先级 | 功能 | 说明 |
|--------|------|------|
| P5 | OAuth2 第三方登录 | Google/GitHub 登录 |
| P5 | A/B 测试框架 | Agent 策略对比 |
| P5 | 审计日志中间件 | 合规审计 |
| P5 | 多模型路由中间件 | 按任务复杂度选模型 |
| P5 | Alembic 数据库迁移 | 版本化 schema |
| P5 | MongoDB 存储 | 文档型数据库 |
| P5 | Celery/RabbitMQ 队列 | 分布式任务 |
| P5 | 前端 UI | Web 管理界面 |
