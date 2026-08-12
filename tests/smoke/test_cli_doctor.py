from __future__ import annotations

from pathlib import Path

from agentbase.cli import main


def test_cli_doctor(mock_model, isolated_env, monkeypatch, capsys):
    root = str(Path(__file__).resolve().parents[2])
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key-not-real")
    monkeypatch.setenv("AGENTBASE_STORAGE__TYPE", "sqlite")
    monkeypatch.setenv("AGENTBASE_STORAGE__DSN", "")
    monkeypatch.setenv("AGENTBASE_CHECKPOINTER__TYPE", "memory")
    monkeypatch.setenv("AGENTBASE_CHECKPOINTER__DSN", "")
    from agentbase.config.settings import get_env_settings
    get_env_settings.cache_clear()
    main(["doctor", "--root", root])
    captured = capsys.readouterr()
    assert "root" in captured.out
    assert "config" in captured.out
    assert "assembly" in captured.out


def test_cli_doctor_exit_code(mock_model, isolated_env, monkeypatch, capsys):
    root = str(Path(__file__).resolve().parents[2])
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key-not-real")
    monkeypatch.setenv("AGENTBASE_STORAGE__TYPE", "sqlite")
    monkeypatch.setenv("AGENTBASE_STORAGE__DSN", "")
    monkeypatch.setenv("AGENTBASE_CHECKPOINTER__TYPE", "memory")
    monkeypatch.setenv("AGENTBASE_CHECKPOINTER__DSN", "")
    from agentbase.config.settings import get_env_settings
    get_env_settings.cache_clear()
    rc = main(["doctor", "--root", root])
    assert rc in (0, 1)