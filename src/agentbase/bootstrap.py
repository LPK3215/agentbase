"""Runtime bootstrap — assembles the full execution context.

``RuntimeContext`` is the single object that ties together:
- ``AgentFactory`` — lazy-instantiates all dependencies (model, tools, storage, etc.)
- ``AgentRunner`` — executes invoke / stream / resume
- Agent config cache — maps agent name → assembled agent instance

Lifecycle:
    ctx = build_runtime()          # build once
    agent = ctx.get_agent("foo")  # lazy-build + cache
    ctx.reload()                   # clear caches (hot-reload configs)
    ctx.close()                    # release DB connections, thread pools, etc.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from agentbase.config.loader import list_agent_names, load_agent_config, load_app_config
from agentbase.config.schema import AgentConfig, AppConfig
from agentbase.factories.agent_factory import AgentFactory
from agentbase.registry.bootstrap import bootstrap_registries
from agentbase.runtime.errors import FactoryError, NotFoundError
from agentbase.runtime.logging import configure_logging, get_logger
from agentbase.runtime.runner import AgentRunner

logger = get_logger(__name__)


class RuntimeContext:
    """Assembled runtime with config, factory, and runner.

    Use ``build_runtime()`` to create an instance. The context lazily
    builds agents on first access and caches them. Call ``reload()``
    to clear the cache and ``close()`` to release resources.
    """

    def __init__(self, root_dir: Path, app_config: AppConfig) -> None:
        self.root_dir = root_dir
        self.app_config = app_config
        self.factory = AgentFactory(root_dir=root_dir, app_config=app_config)
        self.runner = AgentRunner(factory=self.factory, app_config=app_config)
        self._agents: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._closed = False

    def list_agents(self) -> list[str]:
        """Return all agent names found in the config directory."""
        return list_agent_names(self.root_dir / self.app_config.runtime.config_dir)

    def get_agent_config(self, name: str | None = None) -> AgentConfig:
        """Load an agent's YAML config from disk.

        Raises ``NotFoundError`` if the agent config file does not exist.
        """
        agent_name = name or self.app_config.runtime.default_agent
        config_dir = self.root_dir / self.app_config.runtime.config_dir
        try:
            return load_agent_config(config_dir, agent_name)
        except FileNotFoundError as exc:
            raise NotFoundError(
                f"Agent config not found: {agent_name}",
                detail={"agent": agent_name, "config_dir": str(config_dir)},
            ) from exc

    def get_agent(self, name: str | None = None) -> Any:
        """Get or build an agent instance (cached).

        Raises ``NotFoundError`` if the agent config doesn't exist.
        Raises ``FactoryError`` if the agent cannot be assembled.
        """
        agent_name = name or self.app_config.runtime.default_agent
        if agent_name not in self._agents:
            with self._lock:
                # Double-check after acquiring lock
                if agent_name not in self._agents:
                    agent_config = self.get_agent_config(agent_name)
                    logger.info(
                        "Building agent '%s' (tools=%s middleware=%s subagents=%s)",
                        agent_config.name,
                        agent_config.tools,
                        agent_config.middleware,
                        agent_config.subagents,
                        extra={"event": "agent.build", "agent": agent_name},
                    )
                    try:
                        self._agents[agent_name] = self.factory.build(agent_config)
                    except FactoryError:
                        raise
                    except Exception as exc:
                        raise FactoryError(
                            f"Failed to build agent '{agent_name}': {exc}",
                            detail={"agent": agent_name},
                        ) from exc
        return self._agents[agent_name]

    def reload(self) -> None:
        """Clear the agent cache and rebuild the factory.

        Use this to hot-reload agent configs without restarting the process.
        Already-built agents are discarded and will be rebuilt on next access.
        """
        with self._lock:
            self._agents.clear()
            logger.info(
                "Runtime reloaded — agent cache cleared",
                extra={"event": "runtime.reload"},
            )

    def close(self) -> None:
        """Release all held resources (DB connections, thread pools, etc.).

        After ``close()``, the context should not be used.
        """
        if self._closed:
            return
        with self._lock:
            self._closed = True
            self._agents.clear()
            # Close factory-managed resources
            for attr in ("_storage", "_memory_manager", "_knowledge_base"):
                obj = getattr(self.factory, attr, None)
                if obj is not None and hasattr(obj, "close"):
                    try:
                        obj.close()
                    except Exception:
                        pass
            logger.info(
                "Runtime closed — resources released",
                extra={"event": "runtime.close"},
            )

    def __enter__(self) -> RuntimeContext:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


def resolve_root_dir(root_dir: str | Path | None = None) -> Path:
    """Resolve the project root directory."""
    if root_dir is not None:
        return Path(root_dir).resolve()
    return Path.cwd().resolve()


def build_runtime(root_dir: str | Path | None = None) -> RuntimeContext:
    """Build a ``RuntimeContext`` from the given root directory.

    Steps:
    1. Load YAML config (with env overlays)
    2. Configure structured logging
    3. Bootstrap extension registries (auto-discover)
    4. Assemble the RuntimeContext
    """
    root = resolve_root_dir(root_dir)
    app_config = load_app_config(root)
    configure_logging(app_config.app.log_level)
    bootstrap_registries(app_config.extensions)
    ctx = RuntimeContext(root_dir=root, app_config=app_config)
    logger.info(
        "Runtime built: root=%s agents_dir=%s",
        root,
        app_config.runtime.config_dir,
        extra={
            "event": "runtime.build",
            "root_dir": str(root),
            "default_agent": app_config.runtime.default_agent,
            "storage_type": app_config.storage.type,
        },
    )
    return ctx
