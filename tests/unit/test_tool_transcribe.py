"""Unit tests for transcribe tool — API path, local path, edge cases.

Covers: file not found, API transcription (with/without language, with/without
API key/base_url), local transcription, import error handling, and exception
propagation — all via mocks.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentbase.extensions.tools.transcribe import transcribe, _transcribe_api, _transcribe_local


# ---------------------------------------------------------------------------
# transcribe — file not found
# ---------------------------------------------------------------------------


class TestTranscribeFileNotFound:
    def test_returns_error_for_missing_file(self, tmp_path):
        nonexistent = tmp_path / "nonexistent.mp3"
        result = transcribe(str(nonexistent))
        assert result["text"] == ""
        assert "File not found" in result["error"]
        assert str(nonexistent) in result["error"]


# ---------------------------------------------------------------------------
# transcribe — model routing
# ---------------------------------------------------------------------------


class TestTranscribeRouting:
    def test_routes_to_local_when_model_is_local(self, tmp_path):
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake audio")
        with patch(
            "agentbase.extensions.tools.transcribe._transcribe_local"
        ) as mock_local:
            mock_local.return_value = {"text": "local result"}
            result = transcribe(str(audio), model="local")
            mock_local.assert_called_once_with(audio, "auto")
            assert result["text"] == "local result"

    def test_routes_to_api_by_default(self, tmp_path):
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake audio")
        with patch(
            "agentbase.extensions.tools.transcribe._transcribe_api"
        ) as mock_api:
            mock_api.return_value = {"text": "api result"}
            result = transcribe(str(audio))
            mock_api.assert_called_once_with(audio, "auto", "whisper-1", None)
            assert result["text"] == "api result"

    def test_passes_language_to_api(self, tmp_path):
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake audio")
        with patch(
            "agentbase.extensions.tools.transcribe._transcribe_api"
        ) as mock_api:
            mock_api.return_value = {"text": "api result"}
            transcribe(str(audio), language="zh")
            mock_api.assert_called_once_with(audio, "zh", "whisper-1", None)


# ---------------------------------------------------------------------------
# _transcribe_api
# ---------------------------------------------------------------------------


class TestTranscribeApi:
    def test_returns_error_when_openai_not_installed(self, tmp_path):
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake audio")
        with patch.dict(sys.modules, {"openai": None}):
            result = _transcribe_api(audio, "auto", "whisper-1", None)
            assert result["text"] == ""
            assert "openai package required" in result["error"]

    def test_successful_api_transcription(self, tmp_path):
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake audio")

        # Create a fake openai module
        fake_openai = ModuleType("openai")
        mock_client = MagicMock()
        mock_result = MagicMock()
        mock_result.text = "Hello world"
        mock_result.duration = 5.5
        mock_client.audio.transcriptions.create.return_value = mock_result
        fake_openai.OpenAI = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"openai": fake_openai}):
            result = _transcribe_api(audio, "en", "whisper-1", "sk-test")
            assert result["text"] == "Hello world"
            assert result["language"] == "en"
            assert result["duration_seconds"] == 5.5

        # Verify the API was called with correct params
        call_kwargs = mock_client.audio.transcriptions.create.call_args
        assert call_kwargs.kwargs["model"] == "whisper-1"
        assert call_kwargs.kwargs["language"] == "en"

    def test_auto_language_omits_language_param(self, tmp_path):
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake audio")

        fake_openai = ModuleType("openai")
        mock_client = MagicMock()
        mock_result = MagicMock()
        mock_result.text = "Auto detect"
        mock_result.duration = None
        mock_client.audio.transcriptions.create.return_value = mock_result
        fake_openai.OpenAI = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"openai": fake_openai}):
            result = _transcribe_api(audio, "auto", "whisper-1", "sk-test")
            assert result["text"] == "Auto detect"
            assert result["duration_seconds"] is None

        call_kwargs = mock_client.audio.transcriptions.create.call_args
        assert "language" not in call_kwargs.kwargs

    def test_uses_env_api_key_when_not_provided(self, tmp_path, monkeypatch):
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake audio")
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
        monkeypatch.delenv("SILICONFLOW_BASE_URL", raising=False)

        fake_openai = ModuleType("openai")
        mock_client = MagicMock()
        mock_result = MagicMock()
        mock_result.text = "Result"
        mock_result.duration = None
        mock_client.audio.transcriptions.create.return_value = mock_result
        fake_openai.OpenAI = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"openai": fake_openai}):
            _transcribe_api(audio, "auto", "whisper-1", None)
            fake_openai.OpenAI.assert_called_once_with(api_key="env-key")

    def test_uses_siliconflow_env_as_fallback(self, tmp_path, monkeypatch):
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake audio")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("SILICONFLOW_API_KEY", "sf-key")
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.setenv("SILICONFLOW_BASE_URL", "https://sf.example.com")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        fake_openai = ModuleType("openai")
        mock_client = MagicMock()
        mock_result = MagicMock()
        mock_result.text = "SF result"
        mock_result.duration = None
        mock_client.audio.transcriptions.create.return_value = mock_result
        fake_openai.OpenAI = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"openai": fake_openai}):
            _transcribe_api(audio, "auto", "whisper-1", None)
            fake_openai.OpenAI.assert_called_once_with(
                api_key="sf-key", base_url="https://sf.example.com"
            )

    def test_no_key_no_base_url(self, tmp_path, monkeypatch):
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake audio")
        for env_var in ("OPENAI_API_KEY", "SILICONFLOW_API_KEY", "OPENAI_BASE_URL", "SILICONFLOW_BASE_URL"):
            monkeypatch.delenv(env_var, raising=False)

        fake_openai = ModuleType("openai")
        mock_client = MagicMock()
        mock_result = MagicMock()
        mock_result.text = "No key result"
        mock_result.duration = None
        mock_client.audio.transcriptions.create.return_value = mock_result
        fake_openai.OpenAI = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"openai": fake_openai}):
            _transcribe_api(audio, "auto", "whisper-1", None)
            fake_openai.OpenAI.assert_called_once_with()

    def test_api_exception_returns_error(self, tmp_path):
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake audio")

        fake_openai = ModuleType("openai")
        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.side_effect = RuntimeError("API timeout")
        fake_openai.OpenAI = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"openai": fake_openai}):
            result = _transcribe_api(audio, "auto", "whisper-1", "sk-test")
            assert result["text"] == ""
            assert "API timeout" in result["error"]


# ---------------------------------------------------------------------------
# _transcribe_local
# ---------------------------------------------------------------------------


class TestTranscribeLocal:
    def test_returns_error_when_whisper_not_installed(self, tmp_path):
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake audio")
        with patch.dict(sys.modules, {"whisper": None}):
            result = _transcribe_local(audio, "auto")
            assert result["text"] == ""
            assert "whisper package" in result["error"]

    def test_successful_local_transcription(self, tmp_path):
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake audio")

        fake_whisper = ModuleType("whisper")
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {
            "text": "Local transcription",
            "language": "zh",
            "segments": [{"start": 0, "end": 10.5}],
        }
        fake_whisper.load_model = MagicMock(return_value=mock_model)

        with patch.dict(sys.modules, {"whisper": fake_whisper}):
            result = _transcribe_local(audio, "zh")
            assert result["text"] == "Local transcription"
            assert result["language"] == "zh"
            assert result["duration_seconds"] == 10.5

        # Verify language was passed to transcribe
        mock_model.transcribe.assert_called_once_with(str(audio), language="zh")

    def test_auto_language_omits_language_option(self, tmp_path):
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake audio")

        fake_whisper = ModuleType("whisper")
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {
            "text": "Auto",
            "language": "en",
            "segments": [{"start": 0, "end": 5.0}],
        }
        fake_whisper.load_model = MagicMock(return_value=mock_model)

        with patch.dict(sys.modules, {"whisper": fake_whisper}):
            result = _transcribe_local(audio, "auto")
            assert result["text"] == "Auto"
            assert result["language"] == "en"
            assert result["duration_seconds"] == 5.0

        # No language option when auto
        mock_model.transcribe.assert_called_once_with(str(audio))

    def test_no_segments_returns_none_duration(self, tmp_path):
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake audio")

        fake_whisper = ModuleType("whisper")
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {
            "text": "No segments",
            "language": "en",
            "segments": None,
        }
        fake_whisper.load_model = MagicMock(return_value=mock_model)

        with patch.dict(sys.modules, {"whisper": fake_whisper}):
            result = _transcribe_local(audio, "auto")
            assert result["text"] == "No segments"
            assert result["duration_seconds"] is None

    def test_empty_segments_returns_none_duration(self, tmp_path):
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake audio")

        fake_whisper = ModuleType("whisper")
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {
            "text": "Empty",
            "language": "en",
            "segments": [],
        }
        fake_whisper.load_model = MagicMock(return_value=mock_model)

        with patch.dict(sys.modules, {"whisper": fake_whisper}):
            result = _transcribe_local(audio, "auto")
            assert result["duration_seconds"] is None

    def test_local_exception_returns_error(self, tmp_path):
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake audio")

        fake_whisper = ModuleType("whisper")
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = RuntimeError("Model load failed")
        fake_whisper.load_model = MagicMock(return_value=mock_model)

        with patch.dict(sys.modules, {"whisper": fake_whisper}):
            result = _transcribe_local(audio, "auto")
            assert result["text"] == ""
            assert "Model load failed" in result["error"]

    def test_fallback_language_from_result(self, tmp_path):
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake audio")

        fake_whisper = ModuleType("whisper")
        mock_model = MagicMock()
        # Result doesn't have "language" key — should fall back to param
        mock_model.transcribe.return_value = {
            "text": "No lang",
            "segments": [{"end": 3.0}],
        }
        fake_whisper.load_model = MagicMock(return_value=mock_model)

        with patch.dict(sys.modules, {"whisper": fake_whisper}):
            result = _transcribe_local(audio, "zh")
            assert result["language"] == "zh"
