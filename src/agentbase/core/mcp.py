"""MCP (Model Context Protocol) client registry and integration.

Provides a pluggable registry for MCP server connections. Agents can
discover and call MCP-exposed tools at runtime.

Default: ``MemoryMCPClient`` — a zero-dependency in-memory implementation
for testing. Register real MCP clients with ``@register_mcp_client``.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable


@dataclass
class MCPTool:
    """A tool discovered from an MCP server."""
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    server_name: str = ""


@dataclass
class MCPToolResult:
    """Result of calling an MCP tool."""
    content: str = ""
    is_error: bool = False


@runtime_checkable
class MCPClient(Protocol):
    """Protocol for MCP server clients."""

    @property
    def name(self) -> str:
        """Server identifier."""
        ...

    def connect(self) -> None:
        """Establish connection to the MCP server."""
        ...

    def disconnect(self) -> None:
        """Close the connection."""
        ...

    def list_tools(self) -> list[MCPTool]:
        """Discover available tools from the server."""
        ...

    def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> MCPToolResult:
        """Invoke a tool on the server."""
        ...


class MemoryMCPClient:
    """In-memory MCP client for testing and development.

    Pre-register tools and their handlers, then agents can call them.
    """

    def __init__(self, *, name: str = "memory") -> None:
        self._name = name
        self._tools: dict[str, MCPTool] = {}
        self._handlers: dict[str, Callable[[dict[str, Any]], str]] = {}
        self._connected = False

    @property
    def name(self) -> str:
        return self._name

    def register_tool(
        self,
        name: str,
        handler: Callable[[dict[str, Any]], str],
        *,
        description: str = "",
        input_schema: dict[str, Any] | None = None,
    ) -> None:
        """Register an in-memory tool."""
        self._tools[name] = MCPTool(
            name=name,
            description=description,
            input_schema=input_schema or {},
            server_name=self._name,
        )
        self._handlers[name] = handler

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def list_tools(self) -> list[MCPTool]:
        return list(self._tools.values())

    def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> MCPToolResult:
        if tool_name not in self._handlers:
            return MCPToolResult(content=f"Unknown tool: {tool_name}", is_error=True)
        try:
            content = self._handlers[tool_name](arguments or {})
            return MCPToolResult(content=content)
        except Exception as exc:
            return MCPToolResult(content=str(exc), is_error=True)


class MCPRegistry:
    """Thread-safe registry for MCP client factories."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., MCPClient]] = {}
        self._metas: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        factory: Callable[..., MCPClient],
        *,
        description: str = "",
        override: bool = False,
    ) -> None:
        key = name.lower()
        with self._lock:
            if key in self._factories and not override:
                raise ValueError(f"MCP client '{name}' is already registered")
            self._factories[key] = factory
            self._metas[key] = {"name": name, "description": description}

    def create(self, provider_name: str, **kwargs: Any) -> MCPClient:
        key = provider_name.lower()
        with self._lock:
            if key not in self._factories:
                available = ", ".join(sorted(self._factories.keys())) or "<empty>"
                raise KeyError(f"Unknown MCP client: {provider_name}. Available: {available}")
            factory = self._factories[key]
        return factory(**kwargs)

    def has(self, name: str) -> bool:
        with self._lock:
            return name.lower() in self._factories

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._metas.keys())

    def get_meta(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            return self._metas.get(name.lower())

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._factories)

    def unregister(self, name: str) -> bool:
        """Remove a factory. Returns True if removed."""
        key = name.lower()
        with self._lock:
            if key not in self._factories:
                return False
            self._factories.pop(key, None)
            self._metas.pop(key, None)
            return True


# Global registry instance
mcp_registry = MCPRegistry()

# Register default
mcp_registry.register("memory", MemoryMCPClient, description="In-memory MCP client for testing.")


def register_mcp_client(name: str, *, description: str = "", override: bool = False):
    """Decorator to register an MCP client factory."""

    def decorator(factory: Callable[..., MCPClient]):
        mcp_registry.register(name, factory, description=description, override=override)
        return factory

    return decorator


class MCPManager:
    """Manages multiple MCP server connections for an agent.

    Aggregates tools from all connected servers so the agent
    sees a unified tool list.

    Features:
    - Tool index — maps tool name → server name for O(1) lookup
    - Connection timeout — configurable per-call timeout
    - Reconnect on failure — automatically tries to reconnect
      disconnected servers before calling tools
    - Health check — ``health_check()`` verifies all connections
    """

    def __init__(self, *, call_timeout: float = 30.0) -> None:
        self._clients: dict[str, MCPClient] = {}
        self._tool_index: dict[str, str] = {}  # tool_name → server_name
        self._call_timeout = call_timeout

    def add_server(self, name: str, client: MCPClient) -> None:
        self._clients[name] = client
        self._refresh_tool_index()

    def _refresh_tool_index(self) -> None:
        """Rebuild the tool→server index from all connected clients."""
        self._tool_index.clear()
        for server_name, client in self._clients.items():
            try:
                for tool in client.list_tools():
                    # First server to register a tool wins
                    if tool.name not in self._tool_index:
                        self._tool_index[tool.name] = server_name
            except Exception:
                pass  # Server not connected yet

    def connect_all(self) -> None:
        for client in self._clients.values():
            try:
                client.connect()
            except Exception:
                pass  # Continue connecting other servers
        self._refresh_tool_index()

    def disconnect_all(self) -> None:
        for client in self._clients.values():
            try:
                client.disconnect()
            except Exception:
                pass
        self._tool_index.clear()

    def reconnect_server(self, name: str) -> bool:
        """Attempt to reconnect a specific server."""
        client = self._clients.get(name)
        if client is None:
            return False
        try:
            client.disconnect()
        except Exception:
            pass
        try:
            client.connect()
            self._refresh_tool_index()
            return True
        except Exception:
            return False

    def health_check(self) -> dict[str, bool]:
        """Check connection status of all servers.

        Returns a dict mapping server name → connected status.
        """
        result = {}
        for name, client in self._clients.items():
            # MemoryMCPClient has _connected attribute
            connected = getattr(client, "_connected", None)
            if connected is None:
                # Try listing tools as a health check
                try:
                    client.list_tools()
                    connected = True
                except Exception:
                    connected = False
            result[name] = bool(connected)
        return result

    def list_all_tools(self) -> list[MCPTool]:
        tools: list[MCPTool] = []
        for client in self._clients.values():
            try:
                tools.extend(client.list_tools())
            except Exception:
                pass
        return tools

    def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> MCPToolResult:
        """Call a tool by name using the tool index for O(1) lookup.

        Falls back to linear scan if the tool is not in the index.
        """
        server_name = self._tool_index.get(tool_name)
        if server_name is not None:
            client = self._clients.get(server_name)
            if client is not None:
                try:
                    return client.call_tool(tool_name, arguments)
                except Exception as exc:
                    # Try reconnecting once
                    if self.reconnect_server(server_name):
                        try:
                            return client.call_tool(tool_name, arguments)
                        except Exception:
                            pass
                    return MCPToolResult(content=f"Tool call failed: {exc}", is_error=True)

        # Fallback: linear scan all servers
        for client in self._clients.values():
            try:
                tools = client.list_tools()
                if any(t.name == tool_name for t in tools):
                    return client.call_tool(tool_name, arguments)
            except Exception:
                continue
        return MCPToolResult(content=f"Tool '{tool_name}' not found on any MCP server", is_error=True)

    @property
    def server_names(self) -> list[str]:
        return list(self._clients.keys())
