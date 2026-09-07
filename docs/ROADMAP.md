# AgentBase 功能路线图（ROADMAP）

> 配合 `docs/提示词.md` 使用。
> 选**最高优先级、未完成**的模块推进。已完成项只保留一览，细节以 `docs/backend-boundaries.md` 为准。
> 状态：`done` / `in_progress` / `pending`。

---

## 已完成（v0.4.0）

登记权威在 `backend-boundaries.md`。这里只列模块名，不再展开规格。

| 域 | 已完成 |
|----|--------|
| 核心服务 | memory / knowledge / queue / queue_celery / skills / workspace / storage / storage_mongodb / mcp / tracer / graph / audit / redaction / secrets / experiment / migration / model_manager / prompt / user_manager / apikey_manager / oauth2 / usage / webhook / feedback / notification / conversation / scheduler / calendar / system_config / rbac / alert / evaluation / parsers / embeddings / search |
| 注册表 | 30 个（parser / embedding / search / mcp / queue / tracer / graph / storage / checkpointer / audit / redaction / secrets / experiment / model_manager / prompt_manager / user_manager / apikey_manager / usage / webhook / feedback / notification / conversation / schedule / calendar / system_config / rbac / alert + tool / subagent / middleware） |
| 扩展 | tools(48) / middleware(9) / subagents / parsers(9) |
| API | ~144 路由（agents / memory / kb / queue / skills / workspace / health / audit / experiments / models / prompts / users / auth / sessions / apikeys / usage / webhooks / feedback / notifications / conversations / schedules / calendar / system-config / rbac / alerts / admin） |
| CLI | 21 条（run / stream / resume / serve / doctor / version / eval / config / backup / restore / worker / db\*） |
| 测试 / 部署 | pytest 基座 + Docker / K8s Helm / Nginx / bare metal |

历史条目（A1–A4、B1–B2、C1–C3、D1–D2、F1–F2、G1–G18）均已 `done`，细节见 CHANGELOG 与 `backend-boundaries.md`。

---

## 待推进（真正未完成）

| ID | 模块 | 优先级 | 说明 |
|----|------|--------|------|
| N1 | 轨迹/结果 grader | P2 | transcript / tool-call / state 评估，接入 `EvaluationRunner` |
| N2 | eval suite 进 CI | P2 | `agentbase eval --suite examples/eval_suite.yaml` 作回归门禁 |
| N3 | 自动记忆 save/recall | P3 | 对话后自动保存、查询前可选注入（默认关） |
| N4 | 输出 guardrail | P3 | 禁词 / schema / abstention 中间件（默认关） |

新模块必须先过 `docs/project-positioning.md` 的边界三问，并且**不是**为了把登记表凑长。

---

## 推进规则

1. 每次只推进一个未完成模块，完成后按 `docs/提示词.md` 第 11 节自查。
2. 模块完成 → 本表改 `done`，细节写入 `backend-boundaries.md`，不要把规格再贴回本文件。
3. 用户可随时调整优先级 / 增删模块。
4. 冗余清理：产物与死文档直接删；已接线的产品功能先列清单再删。
