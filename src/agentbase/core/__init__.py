"""Core domain services for skill, memory, knowledge, and search management.

These modules provide the persistent CRUD infrastructure and pluggable
provider registries that turn the scaffold from a bare runtime into a
usable agent platform.

Modules:
- ``skills``     — SkillManager: file-based skill CRUD
- ``memory``     — MemoryManager: SQLite/PostgreSQL-backed memory CRUD
- ``knowledge``  — KnowledgeBase: document store with chunking, embeddings, search
- ``storage``    — StorageBackend: SQLite/PostgreSQL abstraction layer
- ``parsers``    — ParserRegistry: pluggable document parsing (txt, md, pdf, …)
- ``embeddings`` — EmbeddingRegistry: pluggable vector embedding providers
- ``search``     — SearchRegistry: pluggable web search providers
- ``mcp``        — MCPRegistry: Model Context Protocol client registry
- ``queue``      — QueueRegistry: async request queue providers
- ``evaluation`` — EvaluationRunner: agent response evaluation framework
- ``tracer``     — TracerRegistry: tracing/observability providers
- ``workspace``  — WorkspaceManager: structured file management (uploads/outputs/workspace)
- ``graph``      — GraphRegistry: knowledge graph provider registry (Neo4j, RRF fusion)
"""
