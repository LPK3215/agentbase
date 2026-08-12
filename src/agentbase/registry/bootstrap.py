"""Registry bootstrap — auto-discovers and loads extension modules.

The bootstrap process:
1. Loads built-in factory modules (backend, checkpointer)
2. Loads built-in extension parsers
3. Auto-discovers and loads user-configured extension modules

Error handling:
- Built-in modules are loaded strictly — failure raises
- User extension modules are loaded with ``continue_on_error=True`` —
  failure logs a warning and continues with remaining modules
- A summary log is emitted at the end showing loaded/skipped counts
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Iterable

from agentbase.config.schema import ExtensionsConfig
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)
_BOOTSTRAPPED = False


def _import_module(module_name: str) -> None:
    """Import a module and recursively import all its sub-packages."""
    module = importlib.import_module(module_name)
    if hasattr(module, "__path__"):
        prefix = module.__name__ + "."
        for module_info in pkgutil.walk_packages(module.__path__, prefix):
            importlib.import_module(module_info.name)


def _resolve_load_order(
    modules: list[str],
    *,
    dependencies: dict[str, list[str]] | None = None,
) -> list[str]:
    """Sort modules respecting dependency order.

    Uses a simple topological sort — modules with no dependencies
    come first, then modules that depend on them.

    Args:
        modules: Module names to sort.
        dependencies: Map of module name → list of module names it depends on.

    Returns:
        Sorted module list. If circular dependencies are detected,
        the original order is returned (best-effort).
    """
    if not dependencies:
        return list(modules)

    result: list[str] = []
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(mod: str) -> None:
        if mod in visited:
            return
        if mod in visiting:
            logger.warning("Circular dependency detected: %s", mod)
            return
        visiting.add(mod)
        for dep in dependencies.get(mod, []):
            if dep in modules:
                visit(dep)
        visiting.discard(mod)
        visited.add(mod)
        result.append(mod)

    for mod in modules:
        visit(mod)

    for mod in modules:
        if mod not in result:
            result.append(mod)

    return result


def bootstrap_registries(
    extensions: ExtensionsConfig,
    *,
    force: bool = False,
    continue_on_error: bool = True,
) -> None:
    """Bootstrap all registries by importing extension modules.

    Args:
        extensions: Extensions config with autodiscover and extra_modules.
        force: If True, re-run bootstrap even if already bootstrapped.
        continue_on_error: If True (default), skip extension modules that
            fail to import. If False, raise on first failure.
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED and not force:
        return

    loaded: list[str] = []
    skipped: list[str] = []

    # Built-in infrastructure builders — always loaded, failure is fatal
    builtin_modules = [
        "agentbase.factories.backend_factory",
        "agentbase.factories.checkpointer_factory",
    ]
    for module_name in builtin_modules:
        _import_module(module_name)
        loaded.append(module_name)

    # Register extended document parsers (PDF, DOCX, HTML, Excel)
    try:
        importlib.import_module("agentbase.extensions.parsers")
        loaded.append("agentbase.extensions.parsers")
    except Exception:
        pass  # Optional parsers — skip silently

    # User-configured extension modules
    modules: list[str] = list(extensions.autodiscover) + list(extensions.extra_modules)
    for module_name in modules:
        try:
            _import_module(module_name)
            loaded.append(module_name)
            logger.debug("Loaded extension module: %s", module_name)
        except Exception as exc:  # noqa: BLE001
            if continue_on_error:
                logger.warning(
                    "Failed loading extension module %s: %s — skipping",
                    module_name,
                    exc,
                    extra={"event": "bootstrap.module_failed", "module": module_name, "error": str(exc)},
                )
                skipped.append(module_name)
                continue
            raise

    _BOOTSTRAPPED = True

    logger.info(
        "Bootstrap complete: %d modules loaded, %d skipped",
        len(loaded),
        len(skipped),
        extra={
            "event": "bootstrap.complete",
            "loaded_count": len(loaded),
            "skipped_count": len(skipped),
            "loaded": loaded,
            "skipped": skipped,
        },
    )


def ensure_modules(modules: Iterable[str]) -> None:
    """Import a list of modules, raising on failure."""
    for module_name in modules:
        _import_module(module_name)
