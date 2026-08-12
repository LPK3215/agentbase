from __future__ import annotations

import time

from agentbase.config.schema import ExtensionsConfig
from agentbase.registry.bootstrap import bootstrap_registries


def test_autodiscover_performance():
    ext_config = ExtensionsConfig()
    start = time.perf_counter()
    bootstrap_registries(ext_config, force=True)
    elapsed = (time.perf_counter() - start) * 1000
    assert elapsed < 500, f"autodiscover took {elapsed:.1f}ms (>500ms)"