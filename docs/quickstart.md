# Quick Start Guide

> **AgentBase** — a configuration-driven AI Agent backend / LLM agent framework / 智能体脚手架 built on deepagents + LangChain + LangGraph. This guide gets a working agent from zero to deployed in 10 steps.

**Documentation index:** [README](../README.md) · [Configuration](configuration.md) · [Core Services](core-services.md) · [Extensions](extensions.md) · [Guardrails](guardrails.md) · [Error Codes](error-codes.md) · [Backend Boundaries](backend-boundaries.md) · [Project Positioning](project-positioning.md) · [SECURITY](../SECURITY.md)

## 1. Prerequisites

- Python >= 3.11
- PostgreSQL 16+ (for production, via Docker or local install)
- Or just use SQLite (zero-config, no install needed, dev/single-user)

## 2. Install

```bash
# Clone the project
git clone <your-repo-url>
cd agentbase

# Install with all dependencies
pip install ".[all]"

# Or minimal install
pip install .

# Or with specific extras
pip install ".[postgres,api,openai]"
```

## 3. Configure Environment

```bash
# Copy the example env file
cp .env.example .env

# Edit .env and set your API key
# SILICONFLOW_API_KEY=your-key-here
# OPENAI_API_KEY=your-key-here
```

## 4. Start PostgreSQL (Optional — skip for SQLite)

> **Zero-config mode**: If you don't start PostgreSQL, AgentBase defaults to SQLite (file-based, no install needed). Skip to Step 5.

```bash
# Start PostgreSQL via Docker
docker compose up -d postgres

# Verify it's running
docker compose ps
```

## 5. Validate Your Setup

```bash
# Run health checks
agentbase doctor

# List available agents
agentbase agents

# List registered extensions
agentbase extensions
```

## 6. Run an Agent

```bash
# Single invocation
agentbase run "Hello, what can you do?"

# Stream output
agentbase stream "Explain the project structure"

# Use a specific agent profile
agentbase run --agent coder "Write a Python function"

# Assess the agent against an eval suite (template: examples/eval_suite.yaml)
agentbase eval --suite examples/eval_suite.yaml -o eval_report.json
```

The `default` profile can write and delete files with `interrupt_on` empty. That is local-dev, not a production HITL gate. For a read-only image use `configs/agents/readonly.yaml`; to require confirmation before writes, start from `configs/agents/interrupt_demo.yaml`. Production (`app.env: prod`) will not start without `AGENTBASE_API_KEY`, YAML `auth.api_key`, or a JWT secret.

## 7. Start the API Server

```bash
# Start the FastAPI server
agentbase serve --reload

# Or with custom settings
agentbase serve --host 0.0.0.0 --port 8000 --reload

# Open API docs in your browser:
# http://localhost:8000/docs
# http://localhost:8000/health
```

## 8. Use the API

These curls assume local fail-open (`app.env: dev` and empty API key). If `AGENTBASE_API_KEY` or YAML `auth.api_key` is set, add `-H "Authorization: Bearer <key>"` or `-H "X-API-Key: <key>"`. JWT mode uses `Authorization: Bearer <jwt>`. WebSocket uses the same credentials (query `token` only when those headers are absent); failure closes with 4001.

```bash
# List agents
curl http://localhost:8000/agents

# Invoke an agent
curl -X POST http://localhost:8000/agents/default/invoke \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello!"}'

# Stream an agent (SSE)
curl -N -X POST http://localhost:8000/agents/default/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Stream test"}'

# Submit an async task
curl -X POST http://localhost:8000/queue/submit \
  -H "Content-Type: application/json" \
  -d '{"agent_name": "default", "message": "Background task"}'

# Check task status
curl http://localhost:8000/queue/<task_id>

# Process pending tasks
curl -X POST http://localhost:8000/queue/process
```

## 9. Full Docker Deployment

```bash
# Start everything (PostgreSQL + API)
docker compose up -d

# Check logs
docker compose logs -f api

# API available at http://localhost:8000
```

Compose sets `AGENTBASE_APP__ENV=prod`. Set `AGENTBASE_API_KEY` (or YAML `auth.api_key`, or JWT secret) in `.env` before `docker compose up`, otherwise the API process exits with `AGENTBASE_CONFIG_004`. After that, `/metrics` requires the same credentials as other non-public routes.

## 10. Develop a Custom Agent

1. Create a YAML config in `configs/agents/my_agent.yaml`:

```yaml
name: my_agent
description: My custom agent
system_prompt: |
  You are a custom agent that does X.
tools:
  - echo
  - get_time
  - read_file
  - web_search
middleware:
  - request_logger
capabilities:
  - file_upload
  - files
```

2. Test it:
```bash
agentbase doctor
agentbase run --agent my_agent "Test message"
```

## 11. Register a Custom Provider

```python
# custom_providers.py
from agentbase.core.embeddings import register_embedding_provider

@register_embedding_provider("my_embedding")
class MyEmbedding:
    @property
    def dimension(self) -> int:
        return 768

    def embed(self, text: str) -> list[float]:
        # Your embedding logic
        return [0.0] * 768
```

Add to `configs/default.yaml`:
```yaml
extensions:
  extra_modules:
    - custom_providers
```
