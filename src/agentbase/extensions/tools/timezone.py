from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.tools import tool

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.tools import register_tool

_NOW_LOCAL_META = ExtensionMeta(
    name="now_local", kind="tool", description="Return current time in a given timezone (ISO 8601).", requires_context=[]
)


@register_tool("now_local", meta=_NOW_LOCAL_META)
def build_now_local_tool(context: dict[str, Any] | None = None):
    @tool
    def now_local(timezone_name: str = "UTC") -> str:
        """Return the current time in the specified timezone as ISO 8601 string."""
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(timezone_name) if timezone_name != "UTC" else timezone.utc
        except Exception as exc:  # noqa: BLE001
            return f"Invalid timezone: {timezone_name} ({exc})"
        return datetime.now(tz).isoformat()

    return now_local