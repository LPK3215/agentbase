"""Audio/Video transcription tool — converts speech to text.

Uses OpenAI's Whisper API or local whisper model for transcription.

Usage in config::

    tools:
      - transcribe

The agent can then run::

    transcribe(file_path="meeting.mp3", language="zh")
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.tools import register_tool

_TRANSCRIBE_META = ExtensionMeta(
    name="transcribe",
    kind="tool",
    description="Transcribe audio/video files to text using Whisper API or local model.",
)


@register_tool("transcribe", meta=_TRANSCRIBE_META)
def transcribe(
    file_path: str,
    *,
    language: str = "auto",
    model: str = "whisper-1",
    api_key: str | None = None,
) -> dict:
    """Transcribe audio/video file to text.

    Uses OpenAI's Whisper API by default. For local transcription,
    set ``model="local"`` to use the whisper Python package.

    Args:
        file_path: Path to audio/video file (mp3, mp4, wav, m4a, etc.).
        language: Language code ("zh", "en", "auto"). Default "auto".
        model: Whisper model name. Use "local" for local whisper.
        api_key: OpenAI API key (falls back to env var).

    Returns:
        dict with keys: text, language, duration_seconds
    """
    path = Path(file_path)
    if not path.exists():
        return {"text": "", "error": f"File not found: {file_path}"}

    if model == "local":
        return _transcribe_local(path, language)
    return _transcribe_api(path, language, model, api_key)


def _transcribe_api(
    path: Path,
    language: str,
    model: str,
    api_key: str | None,
) -> dict:
    """Transcribe using OpenAI Whisper API."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        return {"text": "", "error": f"openai package required: {exc}"}

    import os
    key = api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("SILICONFLOW_API_KEY")
    kwargs: dict[str, Any] = {}
    if key:
        kwargs["api_key"] = key
    base = os.environ.get("OPENAI_BASE_URL") or os.environ.get("SILICONFLOW_BASE_URL")
    if base:
        kwargs["base_url"] = base

    client = OpenAI(**kwargs)

    try:
        with open(path, "rb") as audio_file:
            params: dict[str, Any] = {"model": model, "file": audio_file}
            if language != "auto":
                params["language"] = language
            result = client.audio.transcriptions.create(**params)
        return {
            "text": result.text,
            "language": language,
            "duration_seconds": getattr(result, "duration", None),
        }
    except Exception as exc:
        return {"text": "", "error": str(exc)}


def _transcribe_local(path: Path, language: str) -> dict:
    """Transcribe using local whisper model."""
    try:
        import whisper
    except ImportError:
        return {
            "text": "",
            "error": "Local whisper requires the whisper package. Install with: pip install openai-whisper",
        }

    try:
        model = whisper.load_model("base")
        options: dict[str, Any] = {}
        if language != "auto":
            options["language"] = language
        result = model.transcribe(str(path), **options)
        return {
            "text": result.get("text", ""),
            "language": result.get("language", language),
            "duration_seconds": result.get("segments", [{}])[-1].get("end", 0) if result.get("segments") else None,
        }
    except Exception as exc:
        return {"text": "", "error": str(exc)}


__all__ = ["transcribe"]
