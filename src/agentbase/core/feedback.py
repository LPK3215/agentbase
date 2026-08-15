"""User feedback collection and analysis service.

Records user-submitted feedback (ratings, comments, thumbs up/down) for
agent responses.  This is a standard capability of any AI backend platform —
it enables quality monitoring, model evaluation, and continuous improvement.

Pluggable storage:
- ``InMemoryFeedbackProvider`` (default) — zero-config, thread-safe, in-process
- ``NullFeedbackProvider`` — no-op, zero overhead when disabled
- Register custom providers with ``@register_feedback_provider("name")``

Usage::

    from agentbase.core.feedback import FeedbackManager

    manager = FeedbackManager(provider="memory", enabled=True)

    # Record user feedback after an agent response
    record = manager.create_feedback(
        thread_id="abc123",
        message_id="msg-001",
        rating=5,                        # 1-5 star rating
        comment="Great answer!",         # optional text feedback
        user_id="user-001",
        agent_name="default",
    )

    # Query feedback with filters
    records = manager.list_feedback(thread_id="abc123")

    # Get aggregate statistics
    stats = manager.get_stats(agent_name="default")
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, runtime_checkable

from agentbase.runtime.errors import RegistryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class FeedbackRecord:
    """A single user feedback entry for an agent response.

    Attributes:
        thread_id: Session/conversation thread ID (links to checkpointer).
        message_id: Specific message ID within the thread (optional).
        rating: Numeric rating (e.g. 1-5 stars or -1/+1 for thumbs down/up).
        comment: Optional free-text feedback from the user.
        user_id: The user who submitted the feedback (optional, anonymous allowed).
        agent_name: The agent that generated the response being rated.
        tags: Optional list of tags for categorisation (e.g. ["helpful", "fast"]).
        metadata: Arbitrary key-value metadata for extensibility.
        created_at: ISO 8601 UTC timestamp (auto-set).
        id: Auto-assigned feedback ID.
    """

    thread_id: str
    message_id: str = ""
    rating: float | None = None
    comment: str = ""
    user_id: str = ""
    agent_name: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:16]

    @property
    def sentiment(self) -> str:
        """Classify the feedback sentiment based on rating.

        Supports two rating scales:
        - **±1 scale** (thumbs): ``-1`` = negative, ``+1`` = positive, ``0`` = neutral
        - **1-5 star scale**: ``1-2`` = negative, ``3`` = neutral, ``4-5`` = positive

        The scale is auto-detected: if ``abs(rating) <= 1``, the ±1 scale is used;
        otherwise the 1-5 star scale is used.

        Returns ``"unknown"`` when ``rating`` is None.
        """
        if self.rating is None:
            return "unknown"
        r = self.rating
        # ±1 scale: -1 = negative, 0 = neutral, +1 = positive
        if -1 <= r <= 1:
            if r > 0:
                return "positive"
            if r < 0:
                return "negative"
            return "neutral"
        # 1-5 star scale: 4-5 = positive, 3 = neutral, 1-2 = negative
        if r >= 4:
            return "positive"
        if r <= 2:
            return "negative"
        return "neutral"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "message_id": self.message_id,
            "rating": self.rating,
            "comment": self.comment,
            "user_id": self.user_id,
            "agent_name": self.agent_name,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
            "sentiment": self.sentiment,
            "created_at": self.created_at,
        }


@dataclass
class FeedbackFilter:
    """Filter criteria for querying feedback records.

    All fields are optional — ``None`` means "no filter on this field".
    """

    thread_id: str | None = None
    message_id: str | None = None
    user_id: str | None = None
    agent_name: str | None = None
    sentiment: str | None = None
    min_rating: float | None = None
    max_rating: float | None = None
    since: str | None = None
    until: str | None = None
    tags: list[str] | None = None
    limit: int = 100
    offset: int = 0


@dataclass
class FeedbackStats:
    """Aggregate feedback statistics.

    Attributes:
        total: Total number of feedback records.
        average_rating: Mean rating across all records with a rating.
        rating_distribution: Mapping of rating value to count.
        sentiment_distribution: Mapping of sentiment → count.
        by_agent: Per-agent feedback counts and average ratings.
        by_thread: Per-thread feedback counts.
        with_comments: Number of records that include a text comment.
        with_tags: Number of records that include tags.
    """

    total: int = 0
    average_rating: float = 0.0
    rating_distribution: dict[str, int] = field(default_factory=dict)
    sentiment_distribution: dict[str, int] = field(default_factory=dict)
    by_agent: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_thread: dict[str, int] = field(default_factory=dict)
    with_comments: int = 0
    with_tags: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "average_rating": round(self.average_rating, 4),
            "rating_distribution": dict(self.rating_distribution),
            "sentiment_distribution": dict(self.sentiment_distribution),
            "by_agent": dict(self.by_agent),
            "by_thread": dict(self.by_thread),
            "with_comments": self.with_comments,
            "with_tags": self.with_tags,
        }


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class FeedbackProvider(Protocol):
    """Protocol for feedback storage providers.

    Implementations must be thread-safe.
    """

    def create(self, record: FeedbackRecord) -> FeedbackRecord:
        """Store a feedback record. Returns the stored record with ID."""
        ...

    def get(self, record_id: str) -> FeedbackRecord | None:
        """Get a feedback record by ID. Returns None if not found."""
        ...

    def query(self, filter: FeedbackFilter | None = None) -> list[FeedbackRecord]:
        """Query feedback records matching the filter."""
        ...

    def delete(self, record_id: str) -> bool:
        """Delete a feedback record. Returns True if deleted."""
        ...

    def stats(self) -> FeedbackStats:
        """Get aggregate feedback statistics."""
        ...

    def close(self) -> None:
        """Release resources."""
        ...


# ---------------------------------------------------------------------------
# Null provider (no-op, zero overhead)
# ---------------------------------------------------------------------------

class NullFeedbackProvider:
    """No-op feedback provider — all operations return empty/None.

    Used when feedback collection is disabled (``feedback.enabled=false``).
    """

    def create(self, record: FeedbackRecord) -> FeedbackRecord:
        return record

    def get(self, record_id: str) -> FeedbackRecord | None:
        return None

    def query(self, filter: FeedbackFilter | None = None) -> list[FeedbackRecord]:
        return []

    def delete(self, record_id: str) -> bool:
        return False

    def stats(self) -> FeedbackStats:
        return FeedbackStats()

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# In-memory provider (default, zero-config)
# ---------------------------------------------------------------------------

class InMemoryFeedbackProvider:
    """In-memory feedback provider — thread-safe, zero-config.

    Stores feedback records in memory.  All data is lost on process restart.
    """

    def __init__(self, max_records: int = 50_000) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, FeedbackRecord] = {}
        self._max_records = max_records

    def create(self, record: FeedbackRecord) -> FeedbackRecord:
        with self._lock:
            # FIFO eviction when capacity reached
            if len(self._records) >= self._max_records:
                # Remove oldest record
                oldest_id = min(self._records, key=lambda k: self._records[k].created_at)
                del self._records[oldest_id]
            self._records[record.id] = record
            logger.info(
                "Feedback recorded: id=%s thread=%s agent=%s rating=%s",
                record.id,
                record.thread_id,
                record.agent_name,
                record.rating,
                extra={
                    "event": "feedback.created",
                    "feedback_id": record.id,
                    "thread_id": record.thread_id,
                    "agent_name": record.agent_name,
                    "rating": record.rating,
                },
            )
            return record

    def get(self, record_id: str) -> FeedbackRecord | None:
        with self._lock:
            return self._records.get(record_id)

    def query(self, filter: FeedbackFilter | None = None) -> list[FeedbackRecord]:
        with self._lock:
            records = list(self._records.values())
        if filter is None:
            # Sort by created_at descending (newest first) when no filter
            records.sort(key=lambda r: r.created_at, reverse=True)
            return records
        return _apply_feedback_filter(records, filter)

    def delete(self, record_id: str) -> bool:
        with self._lock:
            if record_id not in self._records:
                return False
            del self._records[record_id]
            logger.info(
                "Feedback deleted: id=%s",
                record_id,
                extra={"event": "feedback.deleted", "feedback_id": record_id},
            )
            return True

    def stats(self) -> FeedbackStats:
        with self._lock:
            records = list(self._records.values())

        total = len(records)
        ratings = [r.rating for r in records if r.rating is not None]
        avg_rating = sum(ratings) / len(ratings) if ratings else 0.0

        # Rating distribution
        rating_dist: dict[str, int] = {}
        for r in records:
            if r.rating is not None:
                key = str(r.rating)
                rating_dist[key] = rating_dist.get(key, 0) + 1

        # Sentiment distribution
        sentiment_dist: dict[str, int] = {}
        for r in records:
            s = r.sentiment
            sentiment_dist[s] = sentiment_dist.get(s, 0) + 1

        # By agent
        by_agent: dict[str, dict[str, Any]] = {}
        for r in records:
            name = r.agent_name or "unknown"
            if name not in by_agent:
                by_agent[name] = {"total": 0, "average_rating": 0.0, "ratings": []}
            by_agent[name]["total"] += 1
            if r.rating is not None:
                by_agent[name]["ratings"].append(r.rating)
        for name, info in by_agent.items():
            r_list = info.pop("ratings")
            info["average_rating"] = round(sum(r_list) / len(r_list), 4) if r_list else 0.0

        # By thread
        by_thread: dict[str, int] = {}
        for r in records:
            by_thread[r.thread_id] = by_thread.get(r.thread_id, 0) + 1

        with_comments = sum(1 for r in records if r.comment)
        with_tags = sum(1 for r in records if r.tags)

        return FeedbackStats(
            total=total,
            average_rating=avg_rating,
            rating_distribution=rating_dist,
            sentiment_distribution=sentiment_dist,
            by_agent=by_agent,
            by_thread=by_thread,
            with_comments=with_comments,
            with_tags=with_tags,
        )

    def close(self) -> None:
        with self._lock:
            self._records.clear()


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def _apply_feedback_filter(
    records: list[FeedbackRecord],
    flt: FeedbackFilter,
) -> list[FeedbackRecord]:
    """Apply filter criteria to a list of feedback records."""
    result: list[FeedbackRecord] = []
    for r in records:
        if flt.thread_id is not None and r.thread_id != flt.thread_id:
            continue
        if flt.message_id is not None and r.message_id != flt.message_id:
            continue
        if flt.user_id is not None and r.user_id != flt.user_id:
            continue
        if flt.agent_name is not None and r.agent_name != flt.agent_name:
            continue
        if flt.sentiment is not None and r.sentiment != flt.sentiment:
            continue
        if flt.min_rating is not None and (r.rating is None or r.rating < flt.min_rating):
            continue
        if flt.max_rating is not None and (r.rating is None or r.rating > flt.max_rating):
            continue
        if flt.since is not None and r.created_at < flt.since:
            continue
        if flt.until is not None and r.created_at >= flt.until:
            continue
        if flt.tags is not None:
            if not any(tag in r.tags for tag in flt.tags):
                continue
        result.append(r)
    # Sort by created_at descending (newest first)
    result.sort(key=lambda r: r.created_at, reverse=True)
    if flt.offset > 0:
        result = result[flt.offset:]
    if flt.limit > 0:
        result = result[:flt.limit]
    return result


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class FeedbackRegistry:
    """Thread-safe registry for feedback providers."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., FeedbackProvider]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        factory: Callable[..., FeedbackProvider],
        *,
        override: bool = False,
    ) -> None:
        key = name.strip().lower()
        with self._lock:
            if not key:
                raise RegistryError("Cannot register empty feedback provider name")
            if key in self._factories and not override:
                raise RegistryError(f"Feedback provider already registered: {key}")
            self._factories[key] = factory

    def create(self, name: str, **kwargs: Any) -> FeedbackProvider:
        key = name.strip().lower()
        with self._lock:
            if key not in self._factories:
                available = ", ".join(sorted(self._factories)) or "<empty>"
                raise RegistryError(
                    f"Unknown feedback provider: {key}. Available: {available}"
                )
            factory = self._factories[key]
        return factory(**kwargs)

    def has(self, name: str) -> bool:
        with self._lock:
            return name.strip().lower() in self._factories

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._factories.keys())

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._factories)

    def unregister(self, name: str) -> bool:
        key = name.strip().lower()
        with self._lock:
            if key not in self._factories:
                return False
            self._factories.pop(key, None)
            return True


# Global singleton
feedback_registry = FeedbackRegistry()

# Register defaults
feedback_registry.register("null", NullFeedbackProvider)
feedback_registry.register("memory", InMemoryFeedbackProvider)


def register_feedback_provider(name: str, *, override: bool = False):
    """Decorator: register a feedback provider class.

    Usage::

        @register_feedback_provider("redis")
        class RedisFeedbackProvider:
            def create(self, record): ...
    """
    def decorator(factory: Callable[..., FeedbackProvider]):
        feedback_registry.register(name, factory, override=override)
        return factory
    return decorator


# ---------------------------------------------------------------------------
# Manager — high-level facade
# ---------------------------------------------------------------------------

class FeedbackManager:
    """High-level user feedback collection and analysis manager.

    Wraps a ``FeedbackProvider`` for record storage and statistics.
    When ``enabled=False``, uses ``NullFeedbackProvider`` (no-op).

    Usage::

        manager = FeedbackManager(provider="memory", enabled=True)
        manager.create_feedback(
            thread_id="abc",
            rating=5,
            comment="Very helpful",
        )
    """

    def __init__(
        self,
        *,
        provider: str = "null",
        enabled: bool = False,
        **provider_kwargs: Any,
    ) -> None:
        self._enabled = enabled
        if not enabled:
            self._provider: FeedbackProvider = NullFeedbackProvider()
        else:
            self._provider = feedback_registry.create(provider, **provider_kwargs)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def provider(self) -> FeedbackProvider:
        return self._provider

    def create_feedback(
        self,
        *,
        thread_id: str,
        message_id: str = "",
        rating: float | None = None,
        comment: str = "",
        user_id: str = "",
        agent_name: str = "",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> FeedbackRecord:
        """Record a new user feedback entry.

        Args:
            thread_id: Session thread ID (required, links to conversation).
            message_id: Specific message ID being rated (optional).
            rating: Numeric rating (1-5 stars or -1/+1 for thumbs).
            comment: Free-text feedback (optional).
            user_id: User ID (optional, allows anonymous feedback).
            agent_name: Agent name that generated the response (optional).
            tags: Categorisation tags (optional).
            metadata: Extensible metadata (optional).

        Returns:
            The stored ``FeedbackRecord`` with ID assigned.

        Raises:
            RegistryError: If ``thread_id`` is empty.
        """
        if not thread_id:
            raise RegistryError("thread_id is required for feedback")

        record = FeedbackRecord(
            thread_id=thread_id,
            message_id=message_id,
            rating=rating,
            comment=comment,
            user_id=user_id,
            agent_name=agent_name,
            tags=tags or [],
            metadata=metadata or {},
        )
        return self._provider.create(record)

    def update_feedback(
        self,
        record_id: str,
        *,
        rating: float | None = None,
        comment: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> FeedbackRecord | None:
        """Update an existing feedback record.

        Only provided fields are updated. Returns the updated record,
        or None if the record doesn't exist.
        """
        record = self._provider.get(record_id)
        if record is None:
            return None
        if rating is not None:
            record.rating = rating
        if comment is not None:
            record.comment = comment
        if tags is not None:
            record.tags = tags
        if metadata is not None:
            record.metadata = metadata
        # Re-store (for in-memory, this is a no-op since it's the same object;
        # for external providers, they need to handle updates in create/get)
        return self._provider.create(record)

    def get_feedback(self, record_id: str) -> FeedbackRecord | None:
        """Get a feedback record by ID."""
        return self._provider.get(record_id)

    def list_feedback(
        self,
        *,
        thread_id: str | None = None,
        message_id: str | None = None,
        user_id: str | None = None,
        agent_name: str | None = None,
        sentiment: str | None = None,
        min_rating: float | None = None,
        max_rating: float | None = None,
        since: str | None = None,
        until: str | None = None,
        tags: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FeedbackRecord]:
        """Query feedback records with filters. Returns empty list when disabled."""
        flt = FeedbackFilter(
            thread_id=thread_id,
            message_id=message_id,
            user_id=user_id,
            agent_name=agent_name,
            sentiment=sentiment,
            min_rating=min_rating,
            max_rating=max_rating,
            since=since,
            until=until,
            tags=tags,
            limit=limit,
            offset=offset,
        )
        return self._provider.query(flt)

    def delete_feedback(self, record_id: str) -> bool:
        """Delete a feedback record. Returns True if deleted."""
        return self._provider.delete(record_id)

    def get_stats(
        self,
        *,
        agent_name: str | None = None,
        thread_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> FeedbackStats:
        """Get aggregate feedback statistics.

        When filters are provided, statistics are computed only for
        matching records. Returns zero-values when disabled.
        """
        if agent_name or thread_id or since or until:
            flt = FeedbackFilter(
                agent_name=agent_name,
                thread_id=thread_id,
                since=since,
                until=until,
                limit=0,  # 0 = no limit
            )
            records = self._provider.query(flt)
            return _compute_stats(records)
        return self._provider.stats()

    def clear_all(self) -> int:
        """Delete all feedback records. Returns the count deleted.

        This is a convenience method that queries all records and deletes
        them individually (for provider-agnostic implementation).
        """
        records = self._provider.query(None)
        count = 0
        for r in records:
            if self._provider.delete(r.id):
                count += 1
        logger.info("Cleared all feedback records: count=%d", count)
        return count

    def close(self) -> None:
        self._provider.close()


def _compute_stats(records: list[FeedbackRecord]) -> FeedbackStats:
    """Compute aggregate statistics from a list of records."""
    total = len(records)
    ratings = [r.rating for r in records if r.rating is not None]
    avg_rating = sum(ratings) / len(ratings) if ratings else 0.0

    rating_dist: dict[str, int] = {}
    for r in records:
        if r.rating is not None:
            key = str(r.rating)
            rating_dist[key] = rating_dist.get(key, 0) + 1

    sentiment_dist: dict[str, int] = {}
    for r in records:
        s = r.sentiment
        sentiment_dist[s] = sentiment_dist.get(s, 0) + 1

    by_agent: dict[str, dict[str, Any]] = {}
    for r in records:
        name = r.agent_name or "unknown"
        if name not in by_agent:
            by_agent[name] = {"total": 0, "average_rating": 0.0, "ratings": []}
        by_agent[name]["total"] += 1
        if r.rating is not None:
            by_agent[name]["ratings"].append(r.rating)
    for name, info in by_agent.items():
        r_list = info.pop("ratings")
        info["average_rating"] = round(sum(r_list) / len(r_list), 4) if r_list else 0.0

    by_thread: dict[str, int] = {}
    for r in records:
        by_thread[r.thread_id] = by_thread.get(r.thread_id, 0) + 1

    with_comments = sum(1 for r in records if r.comment)
    with_tags = sum(1 for r in records if r.tags)

    return FeedbackStats(
        total=total,
        average_rating=avg_rating,
        rating_distribution=rating_dist,
        sentiment_distribution=sentiment_dist,
        by_agent=by_agent,
        by_thread=by_thread,
        with_comments=with_comments,
        with_tags=with_tags,
    )


# ---------------------------------------------------------------------------
# Singleton management
# ---------------------------------------------------------------------------

_feedback_manager: FeedbackManager | None = None
_feedback_manager_lock = threading.Lock()


def get_feedback_manager() -> FeedbackManager:
    """Get the global FeedbackManager singleton.

    Raises ``RuntimeError`` if not initialised — call ``set_feedback_manager``
    first (typically during application bootstrap).
    """
    if _feedback_manager is None:
        with _feedback_manager_lock:
            if _feedback_manager is None:
                raise RuntimeError(
                    "FeedbackManager not initialised. Call set_feedback_manager() first."
                )
    return _feedback_manager  # type: ignore[return-value]


def set_feedback_manager(manager: FeedbackManager) -> None:
    """Set the global FeedbackManager singleton."""
    global _feedback_manager
    with _feedback_manager_lock:
        _feedback_manager = manager


def reset_feedback_manager() -> None:
    """Reset the global FeedbackManager singleton (for testing)."""
    global _feedback_manager
    with _feedback_manager_lock:
        _feedback_manager = None
