from __future__ import annotations

from pathlib import Path
from typing import Any

from agentbase.config.schema import BackendConfig
from agentbase.registry.backends import backend_registry, register_backend
from agentbase.runtime.errors import FactoryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


def _resolve_root(root_dir: Path, configured: str) -> Path:
    path = Path(configured)
    if not path.is_absolute():
        path = root_dir / path
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


@register_backend("state")
@register_backend("memory")
def build_state_backend(spec: BackendConfig, *, root_dir: Path) -> Any:
    try:
        from deepagents.backends import StateBackend
    except Exception:
        try:
            from deepagents.backends.state import StateBackend  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise FactoryError(f"StateBackend unavailable: {exc}") from exc
    return StateBackend()


@register_backend("filesystem")
def build_filesystem_backend(spec: BackendConfig, *, root_dir: Path) -> Any:
    try:
        from deepagents.backends import FilesystemBackend
    except Exception as exc:  # noqa: BLE001
        raise FactoryError(f"FilesystemBackend unavailable: {exc}") from exc

    fs_root = _resolve_root(root_dir, spec.root_dir)
    kwargs = dict(spec.options or {})
    # Be tolerant to signature differences across deepagents versions.
    try:
        return FilesystemBackend(root_dir=str(fs_root), **kwargs)
    except TypeError:
        try:
            return FilesystemBackend(root_dir=fs_root, **kwargs)
        except TypeError:
            return FilesystemBackend(str(fs_root))


@register_backend("store")
def build_store_backend(spec: BackendConfig, *, root_dir: Path) -> Any:
    try:
        from deepagents.backends import StoreBackend
    except Exception as exc:  # noqa: BLE001
        raise FactoryError(f"StoreBackend unavailable: {exc}") from exc
    return StoreBackend(**(spec.options or {}))


def build_backend(spec: BackendConfig, *, root_dir: Path) -> Any:
    builder = backend_registry.get(spec.type)
    logger.info("Building backend: %s", spec.type)
    return builder(spec, root_dir=root_dir)
