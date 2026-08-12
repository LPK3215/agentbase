from __future__ import annotations

from pathlib import Path
from typing import Any

from agentbase.config.schema import AgentConfig, AppConfig, PermissionRule
from agentbase.factories.backend_factory import build_backend
from agentbase.factories.checkpointer_factory import build_checkpointer
from agentbase.factories.middleware_factory import build_middleware
from agentbase.factories.model_factory import build_model, merge_model_config
from agentbase.factories.subagent_factory import build_subagents
from agentbase.factories.tool_factory import build_tools
from agentbase.runtime.errors import FactoryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


class AgentFactory:
    """Single composition root for create_deep_agent."""

    def __init__(self, *, root_dir: Path, app_config: AppConfig) -> None:
        self.root_dir = root_dir
        self.app_config = app_config
        self._backend = None
        self._checkpointer = None
        self._skill_manager = None
        self._memory_manager = None
        self._knowledge_base = None
        self._search_provider = None
        self._mcp_manager = None
        self._workspace_manager = None
        self._tracer = None
        self._queue = None
        self._storage = None

    @property
    def backend(self) -> Any:
        if self._backend is None:
            self._backend = build_backend(self.app_config.backend, root_dir=self.root_dir)
        return self._backend

    @property
    def storage(self) -> Any:
        """Underlying StorageBackend for direct DB access (backup/restore, etc.)."""
        if self._storage is None:
            from agentbase.core.storage import create_storage
            storage_cfg = self.app_config.storage
            if storage_cfg.dsn:
                self._storage = create_storage(dsn=storage_cfg.dsn)
            else:
                db_path = self.root_dir / storage_cfg.db_dir / "memory.db"
                self._storage = create_storage(db_path=db_path)
        return self._storage

    @property
    def skill_manager(self) -> Any:
        if self._skill_manager is None:
            from agentbase.core.skills import SkillManager
            skills_dir = self.root_dir / self.app_config.runtime.workspace_dir / "skills"
            self._skill_manager = SkillManager(skills_dir=skills_dir)
        return self._skill_manager

    @property
    def memory_manager(self) -> Any:
        if self._memory_manager is None:
            from agentbase.core.memory import MemoryManager
            storage = self.app_config.storage
            if storage.dsn:
                self._memory_manager = MemoryManager(dsn=storage.dsn)
            else:
                db_path = self.root_dir / storage.db_dir / "memory.db"
                self._memory_manager = MemoryManager(db_path=db_path)
        return self._memory_manager

    @property
    def knowledge_base(self) -> Any:
        if self._knowledge_base is None:
            from agentbase.core.knowledge import KnowledgeBase
            storage = self.app_config.storage
            embedding_cfg = self.app_config.embedding
            # Resolve embedding provider
            embedding_provider = None
            if embedding_cfg.provider and embedding_cfg.provider != "none":
                from agentbase.core.embeddings import embedding_registry
                if embedding_registry.has(embedding_cfg.provider):
                    embedding_provider = embedding_registry.get(embedding_cfg.provider)
            if storage.dsn:
                self._knowledge_base = KnowledgeBase(
                    dsn=storage.dsn,
                    embedding_provider=embedding_provider,
                )
            else:
                db_path = self.root_dir / storage.db_dir / "knowledge.db"
                self._knowledge_base = KnowledgeBase(
                    db_path=db_path,
                    embedding_provider=embedding_provider,
                )
        return self._knowledge_base

    @property
    def search_provider(self) -> Any:
        if self._search_provider is None:
            search_cfg = self.app_config.web_search
            if search_cfg.provider and search_cfg.provider != "none":
                from agentbase.core.search import search_registry
                if search_registry.has(search_cfg.provider):
                    self._search_provider = search_registry.get(search_cfg.provider)
        return self._search_provider

    @property
    def mcp_manager(self) -> Any:
        """Build MCP manager from configured servers."""
        if self._mcp_manager is None:
            from agentbase.core.mcp import MCPManager, mcp_registry
            mcp_cfg = self.app_config.mcp
            if mcp_cfg.provider and mcp_cfg.provider != "none":
                mgr = MCPManager()
                for server_spec in mcp_cfg.servers:
                    server_name = server_spec.get("name", "default")
                    server_type = server_spec.get("type", mcp_cfg.provider)
                    if mcp_registry.has(server_type):
                        client = mcp_registry.create(server_type, **server_spec.get("options", {}))
                        mgr.add_server(server_name, client)
                mgr.connect_all()
                self._mcp_manager = mgr
        return self._mcp_manager

    @property
    def workspace_manager(self) -> Any:
        """Build workspace manager for structured file management."""
        if self._workspace_manager is None:
            from agentbase.core.workspace import WorkspaceManager
            ws_dir = self.root_dir / self.app_config.runtime.workspace_dir
            self._workspace_manager = WorkspaceManager(ws_dir)
        return self._workspace_manager

    @property
    def tracer(self) -> Any:
        """Build tracer from config."""
        if self._tracer is None:
            from agentbase.core.tracer import tracer_registry
            tracer_cfg = self.app_config.tracer
            if tracer_registry.has(tracer_cfg.provider):
                self._tracer = tracer_registry.create(tracer_cfg.provider, **tracer_cfg.options)
            else:
                from agentbase.core.tracer import NullTracer
                self._tracer = NullTracer()
        return self._tracer

    @property
    def queue(self) -> Any:
        """Build request queue from config."""
        if self._queue is None:
            from agentbase.core.queue import MemoryRequestQueue, queue_registry
            queue_cfg = self.app_config.queue
            if queue_cfg.provider and queue_cfg.provider != "none":
                if queue_registry.has(queue_cfg.provider):
                    self._queue = queue_registry.create(queue_cfg.provider, **queue_cfg.options)
                else:
                    self._queue = MemoryRequestQueue()
        return self._queue

    @property
    def checkpointer(self) -> Any:
        if self._checkpointer is None:
            self._checkpointer = build_checkpointer(
                self.app_config.checkpointer,
                root_dir=self.root_dir,
            )
        return self._checkpointer

    def _resolve_paths(self, values: list[str]) -> list[str]:
        resolved: list[str] = []
        for value in values:
            path = Path(value)
            if not path.is_absolute():
                path = self.root_dir / path
            resolved.append(str(path.resolve()))
        return resolved

    def _build_permissions(self, rules: list[PermissionRule]) -> list[Any] | list[dict[str, Any]]:
        if not rules:
            return []

        # Prefer official class if present; otherwise pass plain dicts.
        permission_cls = None
        try:
            from deepagents import FilesystemPermission as permission_cls  # type: ignore
        except Exception:
            try:
                from deepagents.permissions import FilesystemPermission as permission_cls  # type: ignore
            except Exception:
                permission_cls = None

        items: list[Any] = []
        for rule in rules:
            # Normalize paths for deepagents: virtual fs paths must start with '/'
            normalized_paths = []
            for p in rule.paths:
                if not p.startswith("/"):
                    normalized_paths.append("/" + p)
                else:
                    normalized_paths.append(p)
            payload = {
                "operations": rule.operations,
                "paths": normalized_paths,
                "mode": rule.mode,
            }
            if permission_cls is None:
                items.append(payload)
                continue
            try:
                items.append(permission_cls(**payload))
            except Exception:
                items.append(payload)
        return items

    def build(self, agent_config: AgentConfig) -> Any:
        try:
            from deepagents import create_deep_agent
        except Exception as exc:  # noqa: BLE001
            raise FactoryError(f"deepagents is not installed: {exc}") from exc

        model_cfg = merge_model_config(self.app_config, agent_config.model)
        model = build_model(model_cfg)

        context = {
            "root_dir": self.root_dir,
            "app_config": self.app_config,
            "agent_config": agent_config,
            "workspace_dir": self.root_dir / self.app_config.runtime.workspace_dir,
            "skill_manager": self.skill_manager,
            "memory_manager": self.memory_manager,
            "knowledge_base": self.knowledge_base,
            "search_provider": self.search_provider,
            "mcp_manager": self.mcp_manager,
            "workspace_manager": self.workspace_manager,
            "tracer": self.tracer,
            "queue": self.queue,
        }

        tools = build_tools(agent_config.tools, context=context)
        middleware = build_middleware(agent_config.middleware, context=context)
        subagents = build_subagents(agent_config.subagents, context=context)
        permissions = self._build_permissions(agent_config.permissions)
        memory = self._resolve_paths(agent_config.memory)
        skills = self._resolve_paths(agent_config.skills)

        kwargs: dict[str, Any] = {
            "model": model,
            "tools": tools,
            "system_prompt": agent_config.system_prompt,
        }

        optional_kwargs = {
            "memory": memory or None,
            "skills": skills or None,
            "subagents": subagents or None,
            "middleware": middleware or None,
            "backend": self.backend,
            "permissions": permissions or None,
            "interrupt_on": agent_config.interrupt_on or None,
            "checkpointer": self.checkpointer,
            "response_format": agent_config.response_format,
        }
        for key, value in optional_kwargs.items():
            if value is not None:
                kwargs[key] = value

        logger.info(
            "Assembling agent '%s' (tools=%s middleware=%s subagents=%s)",
            agent_config.name,
            agent_config.tools,
            agent_config.middleware,
            agent_config.subagents,
        )

        try:
            return create_deep_agent(**kwargs)
        except TypeError as exc:
            # Compatibility fallback: drop unsupported kwargs progressively.
            unsupported = str(exc)
            filtered = dict(kwargs)
            for key in list(filtered):
                if key in {"model", "tools", "system_prompt"}:
                    continue
                try:
                    return create_deep_agent(**filtered)
                except TypeError as inner_exc:
                    if key in str(inner_exc) or "unexpected keyword" in str(inner_exc).lower():
                        filtered.pop(key, None)
                        continue
                    unsupported = str(inner_exc)
                    filtered.pop(key, None)
            try:
                return create_deep_agent(**filtered)
            except Exception as final_exc:  # noqa: BLE001
                raise FactoryError(
                    f"create_deep_agent failed for agent '{agent_config.name}': {final_exc}; last={unsupported}"
                ) from final_exc
        except Exception as exc:  # noqa: BLE001
            raise FactoryError(f"create_deep_agent failed for agent '{agent_config.name}': {exc}") from exc
