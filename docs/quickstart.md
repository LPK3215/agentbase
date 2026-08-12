# Quick Start Guide

## 1. Prerequisites

- Python >= 3.11
- PostgreSQL 16+ (via Docker or local install)

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

## 4. Start PostgreSQL

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
```

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
