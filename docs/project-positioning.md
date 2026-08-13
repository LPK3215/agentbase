# Project Positioning

> **AgentBase** — a configuration-driven AI Agent backend / LLM agent framework / 智能体脚手架 built on deepagents + LangChain + LangGraph. This document records the core positioning, design philosophy, and boundary principles of the project — why it exists, how it evolves, and what it deliberately does not do.

**Documentation index:** [README](../README.md) · [Quick Start](quickstart.md) · [Configuration](configuration.md) · [Core Services](core-services.md) · [Extensions](extensions.md) · [Error Codes](error-codes.md) · [Backend Boundaries](backend-boundaries.md)

---

## One-Line Positioning

**AgentBase is an AI Agent backend scaffold -- so others don't have to build infrastructure from scratch when writing AI Agents.**

Analogy:
- `create-react-app` sets up a React frontend for you; you only write business components
- `Django` sets up a web backend for you; you only write business logic
- `AgentBase` sets up an Agent backend for you (model calls, memory, knowledge base, queue, API); you only write Agent configs and tools

---

## Design Philosophy

### 1. Fully Furnished + Swappable Parts

Not a bare shell (nothing included, write everything yourself), nor a locked-down house (cannot replace, only use defaults).
It gives you a livable house, but every part can be swapped:

- Don't want PostgreSQL? Switch to SQLite / MySQL, change one config line
- Don't want Hash Embedding? Switch to OpenAI / SentenceTransformers, change one config line
- Don't want in-memory queue? Switch to Redis, change one config line
- Want to add your own tool? `@register_tool("my_tool")` one-line registration
- Want to add your own document parser? `@register_parser(".myext")` one-line registration

### 2. Fixed Interfaces, Swappable Implementations

9 pluggable provider registries, each with a three-layer structure:

```
Interface (Protocol)    -> defines "what must be implemented" -- this is fixed
Default implementation  -> works out of the box, use directly if you don't care
Replacement mechanism   -> @register_xxx_provider("name") one-line swap
```

The value to users is not "you have N features", but "you have N correct interface designs".

### 3. Optionality is Not Redundancy

| Scenario | User Choice |
|----------|-------------|
| Solo dev, quick prototyping | SQLite + Hash Embedding + Memory Queue |
| Production deployment, real business | PostgreSQL + OpenAI Embedding + Redis Queue |
| Offline environment, no API | PostgreSQL + SentenceTransformers + Redis Queue |
| Enterprise compliance, needs audit | PostgreSQL + JWT/RBAC + Langfuse Tracer |

Use `pip install agentbase[rag]` to install PDF/DOCX parsing dependencies only -- unused features add no burden.
The 14 optional dependency groups embody this design.

### 4. Don't Make Architecture Decisions for Users

Don't assume "optimal" -- because what's optimal for A may not suit B.
Provide multiple options + documentation explaining each scenario; let users choose.

---

## Project Positioning

### What It Is

1. **Open-source backend scaffold**: lets others write AI Agents without reinventing the wheel
2. **Personal learning/experiment platform**: test different approaches (swap database, swap embedding, swap queue, etc.)
3. **White-box**: deployable, usable, modifiable, extensible
4. **Open-source iteration**: ship a usable version first, refine through issues/PRs and community collaboration

### What It Is Not

- Not a SaaS product (does not host services)
- Not a frontend framework (explicitly no UI)
- Not an AI Agent itself (it's infrastructure for building Agents)
- Not "everything done for you" (it sets up an experiment bench with swappable parts)
- Not a one-shot finished product (it's an iteratively refined experiment platform)

---

## Boundary Principles

### Should Do

- Provide interfaces (Protocol) and default implementations
- Provide replacement mechanisms (registrars)
- Provide documentation explaining applicable scenarios for each option
- Core features work out of the box (zero-config can run)
- Optional features installed on demand (unused ones take no space)

### Should Not Do

- Don't make "single optimal" choices for users (provide options, don't lock in)
- Don't build frontend UI (explicitly excluded)
- Don't build features unrelated to Agent backend
- Don't pursue "finish everything before release" (ship usable version first, iterate)

### Boundary Decision Criteria

When facing "should we add this feature?", ask three questions:

1. **Is this Agent backend infrastructure?** -> Yes -> Should do
2. **Might users want a different implementation?** -> Yes -> Provide interface + default + replacement
3. **Does this require users to make business decisions?** -> Yes -> Don't do it, leave interface and docs

---

## Development Path

```
v0.1  MVP release -> core works
  |
v0.4  Feature-complete -> production-ready
  |
Open-source release -> people use -> issues filed -> bugs fixed -> PRs merged -> grows
  |
Community self-growth -> v1.0
```

Open source is a marathon, not a sprint. No need to be perfect on day one.
Ship first, discover issues through usage, gather feedback from the community, refine into a great project.

---

## Key Numbers (v0.4.0)

| Dimension | Value |
|-----------|-------|
| Source files | 67 |
| Source code | 6,500+ lines |
| Tests | 1464 (all passing) |
| Coverage | 67% |
| API endpoints | 33 |
| CLI commands | 10 |
| Agent tools | 37 |
| Pluggable providers | 9 registries |
| Document parsers | 9 types |
| Error code domains | 10 |
| Optional dependency groups | 14 |
| Deployment methods | 4 (Docker/K8s/Nginx/Bare metal) |

---

## Tech Stack

- **Core**: Python 3.11+ / deepagents / LangChain / LangGraph
- **API**: FastAPI + uvicorn
- **Storage**: PostgreSQL (pgvector) / SQLite / MySQL
- **Queue**: Memory / Redis
- **Tracing**: Null / InMemory / Langfuse / OpenTelemetry
- **Graph**: Null / InMemory / Neo4j
- **Deployment**: Docker Compose / K8s Helm / Nginx / Bare metal
