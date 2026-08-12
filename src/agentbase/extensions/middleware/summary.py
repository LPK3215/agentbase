"""Summary & compaction middleware.

Automatically compresses conversation history when it exceeds a threshold,
preventing token explosion in long conversations.

Two-level compaction strategy (inspired by production agent systems):
- L1: When message count exceeds ``threshold``, compress older messages into a summary.
- L2: When L1 summary + recent messages still exceed ``max_tokens_estimate``,
      further compress the summary itself.

The middleware is best-effort: if no model is available for summarization,
it simply truncates the oldest messages and logs a warning.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.middleware import register_middleware

logger = logging.getLogger("agentbase.middleware.summary")

_SUMMARY_META = ExtensionMeta(
    name="summary",
    kind="middleware",
    description="Compress conversation history when it exceeds a threshold.",
    requires_context=["agent_config"],
    default_enabled=False,
)

# Default summary prompt template
DEFAULT_SUMMARY_PROMPT = (
    "Summarize the following conversation history concisely, "
    "preserving key facts, decisions, and context:\n\n{history}\n\n"
    "Summary:"
)


# Compiled regex for word tokenization
_WORD_RE = re.compile(r"\S+", re.UNICODE)


def _estimate_tokens(text: str) -> int:
    """Estimate token count using a blended character/word heuristic.

    - CJK characters: ~1.5 tokens each
    - Non-CJK text: ~0.75 tokens per character (≈ ``chars / 4``), a common
      approximation for Latin-script tokens
    - Minimum 1 token for any non-empty text
    """
    if not text:
        return 0
    # Count CJK characters
    cjk_count = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf')
    # Count non-CJK characters (whitespace included, matching "chars / 4")
    non_cjk_chars = len(text) - cjk_count
    # Blended estimate
    estimated = int(cjk_count * 1.5 + non_cjk_chars / 4)
    return max(estimated, 1) if text.strip() else 0


def _extract_messages(request: Any) -> list[dict[str, Any]]:
    """Extract message list from a model call request."""
    if isinstance(request, dict):
        msgs = request.get("messages")
        if isinstance(msgs, list):
            return msgs
    messages = getattr(request, "messages", None)
    if isinstance(messages, list):
        return messages
    return []


def _messages_to_text(messages: list[dict[str, Any]]) -> str:
    """Convert messages to a plain text summary."""
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown") if isinstance(msg, dict) else getattr(msg, "role", "unknown")
        content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
        lines.append(f"[{role}]: {content}")
    return "\n".join(lines)


@register_middleware("summary", meta=_SUMMARY_META)
def build_summary(context: dict[str, Any] | None = None):
    """Summary middleware: compresses long conversation histories.

    Configuration via ``agent_config.metadata.summary``:
    - ``threshold`` (int, default 20): message count that triggers L1 compaction.
    - ``keep_recent`` (int, default 6): messages to keep uncompressed.
    - ``max_tokens_estimate`` (int, default 8000): L2 trigger threshold.
    - ``summary_prompt`` (str): custom summary prompt template.
    """
    context = context or {}
    agent_config = context.get("agent_config")
    config: dict[str, Any] = {}
    if agent_config is not None:
        config = agent_config.metadata.get("summary", {})

    threshold = int(config.get("threshold", 20))
    keep_recent = int(config.get("keep_recent", 6))
    max_tokens = int(config.get("max_tokens_estimate", 8000))
    summary_prompt = config.get("summary_prompt", DEFAULT_SUMMARY_PROMPT)

    # Try to get a model for summarization
    model_fn: Callable[[str], str] | None = context.get("summary_model")

    try:
        from langchain.agents.middleware import wrap_model_call
    except Exception:
        logger.warning(
            "middleware disabled: name=summary reason=wrap_model_call_unavailable",
            extra={"event": "middleware.disabled"},
        )
        return []

    @wrap_model_call
    def summary_middleware(request: Any, handler: Callable[[Any], Any]) -> Any:
        messages = _extract_messages(request)

        # Edge case: not enough messages to trigger compaction
        if len(messages) <= threshold:
            return handler(request)

        # Edge case: keep_recent >= len(messages) — nothing to compress
        if keep_recent >= len(messages):
            return handler(request)

        # L1: Compress older messages into a summary
        old_messages = messages[:-keep_recent]
        recent_messages = messages[-keep_recent:]

        old_text = _messages_to_text(old_messages)
        summary_text = _l1_summarize(old_text, model_fn, summary_prompt)

        # L2: If still too long, compress further
        total_text = summary_text + _messages_to_text(recent_messages)
        if _estimate_tokens(total_text) > max_tokens:
            summary_text = _l2_compact(summary_text, model_fn, max_tokens)

        logger.info(
            "summary.compaction: old=%d recent=%d summary_len=%d est_tokens=%d",
            len(old_messages), len(recent_messages), len(summary_text), _estimate_tokens(summary_text),
            extra={
                "event": "summary.compaction",
                "old_messages": len(old_messages),
                "recent_messages": len(recent_messages),
                "summary_length": len(summary_text),
                "estimated_tokens": _estimate_tokens(summary_text),
            },
        )

        # Reconstruct the messages with summary
        summary_message = {
            "role": "system",
            "content": f"[Conversation Summary]\n{summary_text}",
        }

        if isinstance(request, dict):
            new_request = dict(request)
            new_request["messages"] = [summary_message] + recent_messages
            return handler(new_request)
        else:
            # Try to set messages on the object
            try:
                request.messages = [summary_message] + recent_messages
            except Exception:
                pass
            return handler(request)

    return summary_middleware


def _l1_summarize(text: str, model_fn: Callable[[str], str] | None, prompt_template: str) -> str:
    """L1 summarization: compress conversation history."""
    if model_fn is not None:
        try:
            prompt = prompt_template.format(history=text[:4000])
            return model_fn(prompt)
        except Exception as exc:
            logger.warning("L1 summary model call failed: %s, falling back to truncation", exc)

    # Fallback: extract key sentences (first sentence of each message)
    lines = text.split("\n")
    summary_lines = [lines[0]] if lines else []
    for line in lines[1:]:
        if line.strip().startswith("[") and ":" in line:
            # Take first 200 chars of each message
            summary_lines.append(line[:200] + "..." if len(line) > 200 else line)
    return "\n".join(summary_lines[:20])  # Keep at most 20 lines


def _l2_compact(summary_text: str, model_fn: Callable[[str], str] | None, max_tokens: int) -> str:
    """L2 compaction: further compress an already-summarized text."""
    if model_fn is not None:
        try:
            prompt = f"Compress this summary to under {max_tokens // 2} tokens:\n\n{summary_text}"
            return model_fn(prompt)
        except Exception:
            pass

    # Fallback: truncate to fit
    max_chars = max_tokens * 4
    if len(summary_text) > max_chars:
        return summary_text[:max_chars] + "\n...(truncated)"
    return summary_text
