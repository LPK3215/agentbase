"""Conversation history service — query and manage agent conversation messages.

Provides a pluggable conversation system that allows the platform to:
- Retrieve conversation message history from langgraph checkpoints
- Manage conversation metadata (title, tags, archive status)
- List conversations per user / agent / time range
- Delete conversations and their associated checkpoints
- Get conversation statistics (message counts, duration, etc.)

Pluggable storage:
- ``InMemoryConversationProvider`` (default) — zero-config, thread-safe, in-process
- ``NullConversationProvider`` — no-op, zero overhead when disabled
- Register custom providers with ``@register_conversation_provider("name")``

Usage::

    from agentbase.core.conversation import ConversationManager

    manager = ConversationManager(provider="memory", enabled=True)

    # Record a conversation (called by the runner after invoke/stream)
    manager.record_conversation(
        thread_id="thread-001",
        agent_name="default",
        user_id="user-001",
        messages=[
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ],
    )

    # Retrieve conversation history
    history = manager.get_history(thread_id="thread-001")
    for msg in history.messages:
        print(f"[{msg.role}] {msg.content}")

    # List conversations for a user
    conversations = manager.list_conversations(user_id="user-001")
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, runtime_checkable

from agentbase.runtime.errors import ErrorCode, RegistryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Message:
    """A single message in a conversation.

    Attributes:
        role: Message role — ``user``, ``assistant``, ``system``, ``tool``.
        content: The message content (text or structured).
        metadata: Optional metadata (tool calls, token counts, etc.).
        timestamp: ISO 8601 UTC timestamp when the message was recorded.
        id: Auto-assigned message ID.
    """

    role: str
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_now)
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }


@dataclass
class Conversation:
    """A conversation thread with metadata and messages.

    Attributes:
        thread_id: Unique conversation thread identifier.
        agent_name: The agent that handled this conversation.
        user_id: The user who initiated the conversation.
        title: Human-readable conversation title (auto-generated or set).
        tags: User-defined tags for categorisation.
        archived: Whether the conversation is archived (hidden from default lists).
        messages: List of messages in the conversation.
        message_count: Number of messages (may differ from len(messages) if
            messages are not loaded).
        metadata: Arbitrary key-value metadata.
        created_at: ISO 8601 UTC timestamp (auto-set).
        updated_at: ISO 8601 UTC timestamp of last message (auto-updated).
        finished_at: Optional ISO 8601 UTC timestamp when the conversation ended.
        duration_ms: Conversation duration in milliseconds (if finished).
    """

    thread_id: str
    agent_name: str = ""
    user_id: str = ""
    title: str = ""
    tags: list[str] = field(default_factory=list)
    archived: bool = False
    messages: list[Message] = field(default_factory=list)
    message_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    finished_at: str = ""
    duration_ms: float | None = None

    def to_dict(self, *, include_messages: bool = True) -> dict[str, Any]:
        result = {
            "thread_id": self.thread_id,
            "agent_name": self.agent_name,
            "user_id": self.user_id,
            "title": self.title,
            "tags": self.tags,
            "archived": self.archived,
            "message_count": self.message_count,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
        }
        if include_messages:
            result["messages"] = [m.to_dict() for m in self.messages]
        return result


@dataclass
class ConversationFilter:
    """Filter criteria for listing conversations.

    All fields are optional — ``None`` means no filter on that field.
    """

    user_id: str | None = None
    agent_name: str | None = None
    archived: bool | None = None
    tag: str | None = None
    start_time: str | None = None
    end_time: str | None = None


@dataclass
class ConversationStats:
    """Aggregate statistics for conversations.

    Attributes:
        total_conversations: Total number of conversations.
        total_messages: Total number of messages across all conversations.
        avg_messages: Average messages per conversation.
        conversations_by_agent: Count by agent name.
        conversations_by_user: Count by user ID.
        archived_count: Number of archived conversations.
    """

    total_conversations: int = 0
    total_messages: int = 0
    avg_messages: float = 0.0
    conversations_by_agent: dict[str, int] = field(default_factory=dict)
    conversations_by_user: dict[str, int] = field(default_factory=dict)
    archived_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_conversations": self.total_conversations,
            "total_messages": self.total_messages,
            "avg_messages": round(self.avg_messages, 2),
            "conversations_by_agent": self.conversations_by_agent,
            "conversations_by_user": self.conversations_by_user,
            "archived_count": self.archived_count,
        }


# ---------------------------------------------------------------------------
# Provider Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class ConversationProvider(Protocol):
    """Protocol for conversation history storage backends.

    Implementations must be thread-safe.
    """

    def record_conversation(
        self,
        *,
        thread_id: str,
        agent_name: str,
        user_id: str,
        messages: list[dict[str, Any]],
        title: str = "",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        duration_ms: float | None = None,
    ) -> Conversation:
        """Record or update a conversation.

        Args:
            thread_id: Unique conversation thread identifier.
            agent_name: Agent that handled the conversation.
            user_id: User who initiated the conversation.
            messages: List of message dicts with ``role`` and ``content``.
            title: Optional conversation title.
            tags: Optional tags for categorisation.
            metadata: Optional arbitrary metadata.
            duration_ms: Optional conversation duration.

        Returns:
            The recorded Conversation object.
        """
        ...

    def get_history(
        self,
        *,
        thread_id: str,
        include_messages: bool = True,
    ) -> Conversation | None:
        """Get conversation history by thread ID.

        Args:
            thread_id: The conversation thread ID.
            include_messages: Whether to include full message list.

        Returns:
            Conversation object or None if not found.
        """
        ...

    def list_conversations(
        self,
        *,
        filter: ConversationFilter | None = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
    ) -> list[Conversation]:
        """List conversations with optional filtering and pagination.

        Args:
            filter: Optional filter criteria.
            limit: Maximum number of results.
            offset: Number of results to skip.
            sort_by: Field to sort by — ``updated_at``, ``created_at``, ``message_count``.
            sort_order: Sort order — ``asc`` or ``desc``.

        Returns:
            List of Conversation objects (without messages by default).
        """
        ...

    def update_conversation(
        self,
        *,
        thread_id: str,
        title: str | None = None,
        tags: list[str] | None = None,
        archived: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Conversation:
        """Update conversation metadata.

        Args:
            thread_id: The conversation thread ID.
            title: New title (None = no change).
            tags: New tags (None = no change, [] = clear tags).
            archived: New archived status (None = no change).
            metadata: New metadata (None = no change, merged with existing).

        Returns:
            The updated Conversation object.

        Raises:
            RegistryError: If the conversation is not found.
        """
        ...

    def delete_conversation(self, *, thread_id: str) -> bool:
        """Delete a conversation and all its messages.

        Args:
            thread_id: The conversation thread ID.

        Returns:
            True if deleted, False if not found.
        """
        ...

    def get_stats(
        self,
        *,
        filter: ConversationFilter | None = None,
    ) -> ConversationStats:
        """Get aggregate statistics for conversations.

        Args:
            filter: Optional filter to limit the statistics scope.

        Returns:
            ConversationStats with aggregate counts.
        """
        ...

    def count(
        self,
        *,
        filter: ConversationFilter | None = None,
    ) -> int:
        """Count conversations matching the filter.

        Args:
            filter: Optional filter criteria.

        Returns:
            Number of matching conversations.
        """
        ...

    def close(self) -> None:
        """Release any held resources."""
        ...


# ---------------------------------------------------------------------------
# Null provider (no-op when disabled)
# ---------------------------------------------------------------------------

class NullConversationProvider:
    """No-op provider — zero overhead when conversations are disabled."""

    def record_conversation(
        self,
        *,
        thread_id: str,
        agent_name: str,
        user_id: str,
        messages: list[dict[str, Any]],
        title: str = "",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        duration_ms: float | None = None,
    ) -> Conversation:
        return Conversation(
            thread_id=thread_id,
            agent_name=agent_name,
            user_id=user_id,
            title=title,
            tags=tags or [],
            message_count=len(messages),
            duration_ms=duration_ms,
        )

    def get_history(self, *, thread_id: str, include_messages: bool = True) -> Conversation | None:
        return None

    def list_conversations(
        self,
        *,
        filter: ConversationFilter | None = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
    ) -> list[Conversation]:
        return []

    def update_conversation(
        self,
        *,
        thread_id: str,
        title: str | None = None,
        tags: list[str] | None = None,
        archived: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Conversation:
        raise RegistryError(
            "Conversation not found (conversations disabled)",
            code=ErrorCode.CONVERSATION_NOT_FOUND,
            detail={"thread_id": thread_id},
        )

    def delete_conversation(self, *, thread_id: str) -> bool:
        return False

    def get_stats(self, *, filter: ConversationFilter | None = None) -> ConversationStats:
        return ConversationStats()

    def count(self, *, filter: ConversationFilter | None = None) -> int:
        return 0

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# In-memory provider (default)
# ---------------------------------------------------------------------------

class InMemoryConversationProvider:
    """In-memory conversation storage — zero-config, thread-safe.

    Stores conversations in a dict keyed by ``thread_id``.
    Messages are stored as ``Message`` objects.
    Uses a re-entrant lock for thread safety.
    """

    def __init__(self, *, max_conversations: int = 10_000) -> None:
        self._conversations: dict[str, Conversation] = {}
        self._lock = threading.RLock()
        self._max = max_conversations

    def record_conversation(
        self,
        *,
        thread_id: str,
        agent_name: str,
        user_id: str,
        messages: list[dict[str, Any]],
        title: str = "",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        duration_ms: float | None = None,
    ) -> Conversation:
        now = _now()
        msg_objects: list[Message] = []
        for m in messages:
            msg_objects.append(
                Message(
                    role=m.get("role", "user"),
                    content=_extract_content(m.get("content", "")),
                    metadata=m.get("metadata", {}),
                    timestamp=m.get("timestamp", now),
                    id=m.get("id", ""),
                )
            )

        with self._lock:
            existing = self._conversations.get(thread_id)
            if existing is not None:
                # Update existing conversation
                existing.agent_name = agent_name or existing.agent_name
                existing.user_id = user_id or existing.user_id
                existing.messages = msg_objects
                existing.message_count = len(msg_objects)
                existing.updated_at = now
                if duration_ms is not None:
                    existing.duration_ms = duration_ms
                    existing.finished_at = now
                if title:
                    existing.title = title
                elif not existing.title:
                    existing.title = _auto_title(msg_objects)
                if tags is not None:
                    existing.tags = tags
                if metadata:
                    existing.metadata.update(metadata)
                return existing

            # Create new conversation
            conv = Conversation(
                thread_id=thread_id,
                agent_name=agent_name,
                user_id=user_id,
                title=title or _auto_title(msg_objects),
                tags=tags or [],
                messages=msg_objects,
                message_count=len(msg_objects),
                metadata=metadata or {},
                duration_ms=duration_ms,
                finished_at=now if duration_ms is not None else "",
                updated_at=now,
            )
            self._conversations[thread_id] = conv
            self._evict_if_needed()
            return conv

    def get_history(self, *, thread_id: str, include_messages: bool = True) -> Conversation | None:
        with self._lock:
            conv = self._conversations.get(thread_id)
            if conv is None:
                return None
            if not include_messages:
                # Return a copy without messages
                conv_copy = Conversation(
                    thread_id=conv.thread_id,
                    agent_name=conv.agent_name,
                    user_id=conv.user_id,
                    title=conv.title,
                    tags=list(conv.tags),
                    archived=conv.archived,
                    messages=[],
                    message_count=conv.message_count,
                    metadata=dict(conv.metadata),
                    created_at=conv.created_at,
                    updated_at=conv.updated_at,
                    finished_at=conv.finished_at,
                    duration_ms=conv.duration_ms,
                )
                return conv_copy
            return conv

    def list_conversations(
        self,
        *,
        filter: ConversationFilter | None = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
    ) -> list[Conversation]:
        with self._lock:
            convs = list(self._conversations.values())

        # Apply filter
        if filter is not None:
            convs = _apply_filter(convs, filter)

        # Sort
        reverse = sort_order == "desc"
        if sort_by == "created_at":
            convs.sort(key=lambda c: c.created_at, reverse=reverse)
        elif sort_by == "message_count":
            convs.sort(key=lambda c: c.message_count, reverse=reverse)
        elif sort_by == "updated_at":
            convs.sort(key=lambda c: c.updated_at, reverse=reverse)
        else:
            convs.sort(key=lambda c: c.updated_at, reverse=reverse)

        # Pagination
        total = len(convs)
        if offset >= total:
            return []
        return convs[offset:offset + limit]

    def update_conversation(
        self,
        *,
        thread_id: str,
        title: str | None = None,
        tags: list[str] | None = None,
        archived: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Conversation:
        with self._lock:
            conv = self._conversations.get(thread_id)
            if conv is None:
                raise RegistryError(
                    f"Conversation not found: {thread_id}",
                    code=ErrorCode.CONVERSATION_NOT_FOUND,
                    detail={"thread_id": thread_id},
                )
            if title is not None:
                conv.title = title
            if tags is not None:
                conv.tags = list(tags)
            if archived is not None:
                conv.archived = archived
            if metadata is not None:
                conv.metadata.update(metadata)
            conv.updated_at = _now()
            return conv

    def delete_conversation(self, *, thread_id: str) -> bool:
        with self._lock:
            if thread_id in self._conversations:
                del self._conversations[thread_id]
                logger.info(
                    "Deleted conversation: thread_id=%s",
                    thread_id,
                    extra={"event": "conversation.deleted", "thread_id": thread_id},
                )
                return True
            return False

    def get_stats(self, *, filter: ConversationFilter | None = None) -> ConversationStats:
        with self._lock:
            convs = list(self._conversations.values())

        if filter is not None:
            convs = _apply_filter(convs, filter)

        total_conversations = len(convs)
        total_messages = sum(c.message_count for c in convs)
        by_agent: dict[str, int] = {}
        by_user: dict[str, int] = {}
        archived_count = 0
        for c in convs:
            by_agent[c.agent_name] = by_agent.get(c.agent_name, 0) + 1
            by_user[c.user_id] = by_user.get(c.user_id, 0) + 1
            if c.archived:
                archived_count += 1

        avg = total_messages / total_conversations if total_conversations else 0.0
        return ConversationStats(
            total_conversations=total_conversations,
            total_messages=total_messages,
            avg_messages=avg,
            conversations_by_agent=by_agent,
            conversations_by_user=by_user,
            archived_count=archived_count,
        )

    def count(self, *, filter: ConversationFilter | None = None) -> int:
        with self._lock:
            convs = list(self._conversations.values())
        if filter is not None:
            convs = _apply_filter(convs, filter)
        return len(convs)

    def close(self) -> None:
        with self._lock:
            self._conversations.clear()

    def _evict_if_needed(self) -> None:
        """Evict oldest conversations if over capacity."""
        if len(self._conversations) <= self._max:
            return
        # Sort by updated_at, evict oldest
        sorted_items = sorted(
            self._conversations.items(),
            key=lambda x: x[1].updated_at,
        )
        to_evict = len(self._conversations) - self._max
        for tid, _ in sorted_items[:to_evict]:
            self._conversations.pop(tid, None)
            logger.warning(
                "Evicted conversation due to capacity: thread_id=%s",
                tid,
                extra={"event": "conversation.evicted", "thread_id": tid},
            )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class ConversationRegistry:
    """Registry for conversation provider factories.

    Providers are registered with ``@register_conversation_provider("name")``
    and created via ``registry.create("name", **kwargs)``.
    """

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., ConversationProvider]] = {}
        self._instances: dict[str, ConversationProvider] = {}

    def register(
        self,
        name: str,
        factory: Callable[..., ConversationProvider],
    ) -> None:
        if not name:
            raise RegistryError(
                "Provider name cannot be empty",
                code=ErrorCode.REG_EMPTY_NAME,
            )
        self._factories[name] = factory
        logger.info(
            "Registered conversation provider: %s",
            name,
            extra={"event": "conversation.registry.register", "provider": name},
        )

    def create(self, name: str, **kwargs: Any) -> ConversationProvider:
        if name not in self._factories:
            raise RegistryError(
                f"Unknown conversation provider: {name}",
                code=ErrorCode.REG_NOT_FOUND,
                detail={"requested": name, "available": list(self._factories.keys())},
            )
        return self._factories[name](**kwargs)

    def has(self, name: str) -> bool:
        return name in self._factories

    def list_providers(self) -> list[str]:
        return list(self._factories.keys())


conversation_registry = ConversationRegistry()


def register_conversation_provider(name: str) -> Callable:
    """Decorator to register a conversation provider factory.

    Example::

        @register_conversation_provider("redis")
        class RedisConversationProvider:
            def __init__(self, *, host: str, port: int = 6379, db: int = 0):
                ...
    """

    def decorator(cls: type | Callable[..., ConversationProvider]) -> Callable[..., ConversationProvider]:
        conversation_registry.register(name, cls)
        return cls

    return decorator


# Register built-in providers
conversation_registry.register("memory", InMemoryConversationProvider)
conversation_registry.register("null", NullConversationProvider)


# ---------------------------------------------------------------------------
# Manager (singleton facade)
# ---------------------------------------------------------------------------

class ConversationManager:
    """Facade for conversation history management.

    Wraps a :class:`ConversationProvider` and provides a unified API.
    When disabled, uses :class:`NullConversationProvider`.

    Usage::

        manager = ConversationManager(provider="memory", enabled=True)
        manager.record_conversation(...)
        history = manager.get_history(thread_id="...")
    """

    def __init__(
        self,
        *,
        provider: str = "memory",
        enabled: bool = False,
        **kwargs: Any,
    ) -> None:
        self.enabled = enabled
        if not enabled:
            self._provider: ConversationProvider = NullConversationProvider()
            logger.info("Conversation manager disabled (NullConversationProvider)")
        elif conversation_registry.has(provider):
            self._provider = conversation_registry.create(provider, **kwargs)
            logger.info(
                "Conversation manager enabled with provider: %s",
                provider,
                extra={"event": "conversation.manager.init", "provider": provider},
            )
        else:
            logger.warning(
                "Unknown conversation provider '%s', falling back to memory",
                provider,
            )
            self._provider = InMemoryConversationProvider(**kwargs)

    @property
    def provider_name(self) -> str:
        return type(self._provider).__name__

    def record_conversation(
        self,
        *,
        thread_id: str,
        agent_name: str,
        user_id: str = "",
        messages: list[dict[str, Any]] | None = None,
        title: str = "",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        duration_ms: float | None = None,
    ) -> Conversation:
        """Record or update a conversation.

        When the manager is disabled, returns a Conversation object
        without persisting it.
        """
        try:
            return self._provider.record_conversation(
                thread_id=thread_id,
                agent_name=agent_name,
                user_id=user_id,
                messages=messages or [],
                title=title,
                tags=tags,
                metadata=metadata,
                duration_ms=duration_ms,
            )
        except Exception as exc:
            logger.error(
                "Failed to record conversation: %s",
                exc,
                extra={
                    "event": "conversation.record_failed",
                    "thread_id": thread_id,
                    "error": str(exc),
                },
            )
            if isinstance(exc, RegistryError):
                raise
            raise RegistryError(
                f"Failed to record conversation: {exc}",
                code=ErrorCode.CONVERSATION_RECORD_FAILED,
                detail={"thread_id": thread_id, "error": str(exc)},
            ) from exc

    def get_history(
        self,
        *,
        thread_id: str,
        include_messages: bool = True,
    ) -> Conversation | None:
        """Get conversation history by thread ID."""
        return self._provider.get_history(
            thread_id=thread_id,
            include_messages=include_messages,
        )

    def list_conversations(
        self,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
        archived: bool | None = None,
        tag: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
    ) -> list[Conversation]:
        """List conversations with optional filtering and pagination."""
        filter = ConversationFilter(
            user_id=user_id,
            agent_name=agent_name,
            archived=archived,
            tag=tag,
            start_time=start_time,
            end_time=end_time,
        )
        return self._provider.list_conversations(
            filter=filter,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    def update_conversation(
        self,
        *,
        thread_id: str,
        title: str | None = None,
        tags: list[str] | None = None,
        archived: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Conversation:
        """Update conversation metadata."""
        return self._provider.update_conversation(
            thread_id=thread_id,
            title=title,
            tags=tags,
            archived=archived,
            metadata=metadata,
        )

    def delete_conversation(self, *, thread_id: str) -> bool:
        """Delete a conversation and all its messages."""
        return self._provider.delete_conversation(thread_id=thread_id)

    def get_stats(
        self,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> ConversationStats:
        """Get aggregate statistics for conversations."""
        filter = ConversationFilter(
            user_id=user_id,
            agent_name=agent_name,
        )
        return self._provider.get_stats(filter=filter)

    def count(
        self,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> int:
        """Count conversations matching the filter."""
        filter = ConversationFilter(
            user_id=user_id,
            agent_name=agent_name,
        )
        return self._provider.count(filter=filter)

    def close(self) -> None:
        """Release provider resources."""
        self._provider.close()


# ---------------------------------------------------------------------------
# Singleton management
# ---------------------------------------------------------------------------

_conversation_manager: ConversationManager | None = None
_conversation_lock = threading.Lock()


def init_conversation_manager(
    *,
    provider: str = "memory",
    enabled: bool = False,
    **kwargs: Any,
) -> ConversationManager:
    """Initialise the global ConversationManager singleton.

    Must be called once during application startup.
    Subsequent calls return the existing instance (with a warning).
    """
    global _conversation_manager
    with _conversation_lock:
        if _conversation_manager is not None:
            logger.warning("ConversationManager already initialised")
            return _conversation_manager
        _conversation_manager = ConversationManager(
            provider=provider,
            enabled=enabled,
            **kwargs,
        )
        return _conversation_manager


def get_conversation_manager() -> ConversationManager:
    """Get the global ConversationManager singleton.

    Raises:
        RuntimeError: If the manager has not been initialised.
    """
    global _conversation_manager
    if _conversation_manager is None:
        raise RuntimeError(
            "ConversationManager not initialised. "
            "Call init_conversation_manager() first.",
        )
    return _conversation_manager


def set_conversation_manager(manager: ConversationManager) -> None:
    """Set the global ConversationManager singleton (for API layer)."""
    global _conversation_manager
    with _conversation_lock:
        _conversation_manager = manager


def reset_conversation_manager() -> None:
    """Reset the global singleton (for testing)."""
    global _conversation_manager
    with _conversation_lock:
        if _conversation_manager is not None:
            _conversation_manager.close()
        _conversation_manager = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_content(content: Any) -> str:
    """Extract text content from various message content formats."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                else:
                    text = item.get("text")
                    if text is not None:
                        parts.append(str(text))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def _auto_title(messages: list[Message]) -> str:
    """Generate a conversation title from the first user message."""
    for msg in messages:
        if msg.role == "user":
            content = msg.content
            if len(content) <= 80:
                return content
            return content[:77] + "..."
    return "Untitled conversation"


def _apply_filter(convs: list[Conversation], filter: ConversationFilter) -> list[Conversation]:
    """Apply filter criteria to a list of conversations."""
    result = []
    for c in convs:
        if filter.user_id is not None and c.user_id != filter.user_id:
            continue
        if filter.agent_name is not None and c.agent_name != filter.agent_name:
            continue
        if filter.archived is not None and c.archived != filter.archived:
            continue
        if filter.tag is not None and filter.tag not in c.tags:
            continue
        if filter.start_time is not None and c.created_at < filter.start_time:
            continue
        if filter.end_time is not None and c.created_at > filter.end_time:
            continue
        result.append(c)
    return result


def extract_messages_from_result(result: Any) -> list[dict[str, Any]]:
    """Extract messages from a langgraph agent result.

    Handles various result formats:
    - Dict with ``messages`` key (list of message dicts or objects)
    - Object with ``messages`` attribute
    - List of messages directly
    """
    messages: list[Any] = []

    if result is None:
        return []

    if isinstance(result, dict):
        raw_msgs = result.get("messages")
        if isinstance(raw_msgs, list):
            messages = raw_msgs
        elif isinstance(raw_msgs, tuple):
            messages = list(raw_msgs)
    else:
        raw_msgs = getattr(result, "messages", None)
        if isinstance(raw_msgs, list):
            messages = raw_msgs
        elif isinstance(raw_msgs, tuple):
            messages = list(raw_msgs)

    extracted: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, dict):
            extracted.append({
                "role": msg.get("role", msg.get("type", "user")),
                "content": msg.get("content", ""),
                "metadata": msg.get("metadata", {}),
                "id": msg.get("id", ""),
            })
        elif hasattr(msg, "content") and hasattr(msg, "type"):
            # langchain BaseMessage
            role = getattr(msg, "type", "user")
            content = getattr(msg, "content", "")
            meta = getattr(msg, "response_metadata", {}) or getattr(msg, "metadata", {})
            extracted.append({
                "role": role,
                "content": content if isinstance(content, str) else _extract_content(content),
                "metadata": dict(meta) if meta else {},
                "id": getattr(msg, "id", ""),
            })
        else:
            extracted.append({
                "role": "unknown",
                "content": str(msg),
                "metadata": {},
                "id": "",
            })

    return extracted
