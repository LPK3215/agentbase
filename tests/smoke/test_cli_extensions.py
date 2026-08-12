from __future__ import annotations

from pathlib import Path

from agentbase.cli import main


def test_cli_extensions(mock_model, isolated_env, monkeypatch, capsys):
    root = str(Path(__file__).resolve().parents[2])
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key-not-real")
    monkeypatch.setenv("AGENTBASE_STORAGE__TYPE", "sqlite")
    monkeypatch.setenv("AGENTBASE_STORAGE__DSN", "")
    monkeypatch.setenv("AGENTBASE_CHECKPOINTER__TYPE", "memory")
    monkeypatch.setenv("AGENTBASE_CHECKPOINTER__DSN", "")
    from agentbase.config.settings import get_env_settings
    get_env_settings.cache_clear()
    rc = main(["extensions", "--root", root])
    captured = capsys.readouterr()
    assert "echo" in captured.out
    assert "read_file" in captured.out
    assert rc == 0


def test_cli_extensions_verbose(mock_model, isolated_env, monkeypatch, capsys):
    root = str(Path(__file__).resolve().parents[2])
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key-not-real")
    monkeypatch.setenv("AGENTBASE_STORAGE__TYPE", "sqlite")
    monkeypatch.setenv("AGENTBASE_STORAGE__DSN", "")
    monkeypatch.setenv("AGENTBASE_CHECKPOINTER__TYPE", "memory")
    monkeypatch.setenv("AGENTBASE_CHECKPOINTER__DSN", "")
    from agentbase.config.settings import get_env_settings
    get_env_settings.cache_clear()
    rc = main(["extensions", "--root", root, "--verbose"])
    captured = capsys.readouterr()
    assert "description" in captured.out or "name" in captured.out
    assert rc == 0
