# 贡献指南

感谢您对 AgentBase 项目的关注！以下是参与贡献的流程。

## 开发环境准备

### 前置要求

- Python >= 3.11
- PostgreSQL 16+ with pgvector（生产环境）或 SQLite（开发环境，零配置）
- 推荐使用 [uv](https://github.com/astral-sh/uv) 进行依赖管理

### 1. 克隆仓库

```bash
git clone https://github.com/LPK3215/agentbase.git
cd agentbase
```

### 2. 安装依赖

```bash
# 使用 uv（推荐）
uv sync

# 或使用 pip
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### 3. 初始化配置

```bash
cp .env.example .env
# 编辑 .env 填入配置

# 验证配置
agentbase doctor
```

### 4. 运行测试

```bash
# 全量测试
pytest

# 带覆盖率
pytest --cov=src --cov-report=term-missing

# 特定测试文件
pytest tests/unit/test_agent.py
```

### 5. 启动开发服务

```bash
# CLI 模式
agentbase serve --reload

# Docker 模式
docker compose up -d
```

## 分支策略

- `main`：稳定发布分支
- 功能分支命名：`feature/<简述>`、修复分支：`fix/<简述>`

## 提交规范

使用 Conventional Commits 格式：

| 类型 | 说明 |
|---|---|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档更新 |
| `refactor` | 代码重构 |
| `chore` | 构建/工具/依赖变更 |
| `test` | 测试相关 |

示例：`feat: add new embedding provider for Cohere`

## 代码规范

### Python

- 使用 `ruff` 进行 lint 和格式化
- 类型注解：公共 API 必须有类型标注
- 文档字符串：公共函数/类使用 docstring
- 错误码：使用 `agentbase_<domain>_<nnn>` 格式

### 配置文件

- YAML 配置文件放在 `configs/` 目录下
- 新增扩展注册器时，需在 `src/agentbase/registries/` 中注册

## 测试规范

- 测试文件放在 `tests/` 目录下，按 `unit/`、`integration/`、`e2e/` 分层
- 新增功能时，需同时编写对应测试
- 目标覆盖率：≥ 90%
- CI 通过 GitHub Actions 自动运行

```bash
# Lint
ruff check src tests

# 格式化
ruff format src tests

# 类型检查
pyright src
```

## Pull Request 流程

1. Fork 仓库并创建功能分支
2. 确保代码通过 `ruff check` 和 `pytest`
3. 新增功能需附带测试
4. 提交 PR 并描述变更内容
5. 等待 CI 检查通过
6. 代码审查通过后合并

## 报告 Bug / 提建议

请通过 [GitHub Issues](https://github.com/LPK3215/agentbase/issues) 提交，描述：

- 复现步骤
- 期望行为与实际行为
- 环境信息（Python 版本、OS、PostgreSQL 版本）

## 许可证

贡献的代码将遵循项目许可证（MIT）。
