from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from agentbase.runtime.errors import RegistryError

if TYPE_CHECKING:
    from agentbase.extensions._meta import ExtensionMeta

T = TypeVar("T")


class Registry(Generic[T]):
    """Thread-safe generic registry for named items.

    Features:
    - Register/unregister items by name
    - Metadata attachment (ExtensionMeta)
    - Override protection (duplicate names raise unless override=True)
    - Thread-safe via threading.RLock
    - Clear all items for testing/reset
    """

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._items: dict[str, T] = {}
        self._metas: dict[str, ExtensionMeta] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        item: T,
        *,
        override: bool = False,
        meta: ExtensionMeta | None = None,
    ) -> T:
        key = name.strip()
        if not key:
            raise RegistryError(f"Cannot register empty {self.kind} name")
        with self._lock:
            if key in self._items and not override:
                raise RegistryError(f"{self.kind} already registered: {key}")
            self._items[key] = item
            resolved_meta = meta if meta is not None else getattr(item, "__agentbase_meta__", None)
            if resolved_meta is not None:
                self._metas[key] = resolved_meta
        return item

    def unregister(self, name: str) -> bool:
        """Remove an item from the registry. Returns True if removed."""
        key = name.strip()
        with self._lock:
            if key not in self._items:
                return False
            self._items.pop(key, None)
            self._metas.pop(key, None)
            return True

    def clear(self) -> int:
        """Clear all items. Returns the count of removed items."""
        with self._lock:
            count = len(self._items)
            self._items.clear()
            self._metas.clear()
            return count

    @property
    def count(self) -> int:
        """Number of registered items."""
        with self._lock:
            return len(self._items)

    def get(self, name: str) -> T:
        key = name.strip()
        with self._lock:
            if key not in self._items:
                available = ", ".join(sorted(self._items)) or "<empty>"
                raise RegistryError(f"Unknown {self.kind}: {key}. Available: {available}")
            return self._items[key]

    def has(self, name: str) -> bool:
        with self._lock:
            return name.strip() in self._items

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._items.keys())

    def items(self) -> dict[str, T]:
        with self._lock:
            return dict(self._items)

    def get_meta(self, name: str) -> ExtensionMeta | None:
        with self._lock:
            return self._metas.get(name.strip())

    def metas(self) -> dict[str, ExtensionMeta]:
        with self._lock:
            return dict(self._metas)

    def decorator(
        self,
        name: str,
        *,
        override: bool = False,
        meta: ExtensionMeta | None = None,
    ) -> Callable[[T], T]:
        def _wrap(item: T) -> T:
            self.register(name, item, override=override, meta=meta)
            return item

        return _wrap


Builder = Callable[..., Any]
