"""Tests for agentbase init — preset combinations and skeleton validity.

Verifies that different preset combinations produce valid, importable
project skeletons that can pass `agentbase doctor`.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import yaml

from agentbase.cli import cmd_init
from agentbase.core.presets import (
    PRESETS,
    Preset,
    get_preset,
    list_presets,
    resolve_preset,
)


# ---------------------------------------------------------------------------
# Presets module tests
# ---------------------------------------------------------------------------

class TestPresets:
    def test_get_preset_dev(self):
        """dev preset should have SQLite + Hash + Memory + Null."""
        p = get_preset("dev")
        assert p.storage_type == "sqlite"
        assert p.embedding_provider == "hash"
        assert p.queue_type == "memory"
        assert p.tracer_type == "null"
        assert p.audit_enabled is False
        assert p.redaction_enabled is False

    def test_get_preset_prod(self):
        """prod preset should have PostgreSQL + OpenAI + Redis."""
        p = get_preset("prod")
        assert p.storage_type == "postgresql"
        assert p.embedding_provider == "openai"
        assert p.queue_type == "redis"
        assert p.audit_enabled is True
        assert p.redaction_enabled is True

    def test_get_preset_unknown_returns_dev(self):
        """Unknown preset should fall back to dev."""
        p = get_preset("nonexistent")
        assert p.name == "dev"

    def test_list_presets(self):
        """Should list all 4 presets."""
        presets = list_presets()
        assert len(presets) == 4
        names = {p.name for p in presets}
        assert names == {"dev", "prod", "minimal", "full"}

    def test_resolve_preset_with_overrides(self):
        """Individual overrides should take precedence over preset."""
        p = resolve_preset(preset_name="dev", storage="postgresql")
        assert p.storage_type == "postgresql"
        assert p.embedding_provider == "hash"  # from dev preset

    def test_resolve_preset_no_args(self):
        """No args should return dev preset."""
        p = resolve_preset()
        assert p.name == "dev"

    def test_preset_to_dict(self):
        """to_dict should include all fields."""
        p = get_preset("dev")
        d = p.to_dict()
        assert d["name"] == "dev"
        assert "storage_type" in d
        assert "embedding_provider" in d
        assert "queue_type" in d
        assert "tracer_type" in d


# ---------------------------------------------------------------------------
# CLI init tests — different presets
# ---------------------------------------------------------------------------

def _make_init_args(path: str, name: str = "test-proj", preset: str = "dev",
                    storage=None, embedding=None, queue=None, tracer=None,
                    force=True, dry_run=False) -> argparse.Namespace:
    """Build argparse.Namespace for cmd_init."""
    return argparse.Namespace(
        path=path,
        name=name,
        preset=preset,
        storage=storage,
        embedding=embedding,
        queue=queue,
        tracer=tracer,
        force=force,
        dry_run=dry_run,
    )


class TestInitPresets:
    def test_init_dev_preset(self, tmp_path):
        """dev preset should create SQLite/Hash/Memory/Null config."""
        project = tmp_path / "dev_proj"
        args = _make_init_args(str(project), name="dev_proj", preset="dev")
        assert cmd_init(args) == 0

        config_path = project / "configs" / "default.yaml"
        assert config_path.exists()
        config = yaml.safe_load(config_path.read_text())
        assert config["storage"]["type"] == "sqlite"
        assert config["embedding"]["provider"] == "hash"
        assert config["audit"]["enabled"] is False
        assert config["redaction"]["enabled"] is False

    def test_init_prod_preset(self, tmp_path):
        """prod preset should create PostgreSQL/OpenAI/Redis config."""
        project = tmp_path / "prod_proj"
        args = _make_init_args(str(project), name="prod_proj", preset="prod")
        assert cmd_init(args) == 0

        config_path = project / "configs" / "default.yaml"
        config = yaml.safe_load(config_path.read_text())
        assert config["storage"]["type"] == "postgresql"
        assert config["embedding"]["provider"] == "openai"
        assert config["queue"]["provider"] == "redis"
        assert config["audit"]["enabled"] is True
        assert config["redaction"]["enabled"] is True

    def test_init_minimal_preset(self, tmp_path):
        """minimal preset should create SQLite/Hash/Memory/Null config."""
        project = tmp_path / "min_proj"
        args = _make_init_args(str(project), name="min_proj", preset="minimal")
        assert cmd_init(args) == 0

        config_path = project / "configs" / "default.yaml"
        config = yaml.safe_load(config_path.read_text())
        assert config["storage"]["type"] == "sqlite"

    def test_init_full_preset(self, tmp_path):
        """full preset should create PostgreSQL/OpenAI/Redis config."""
        project = tmp_path / "full_proj"
        args = _make_init_args(str(project), name="full_proj", preset="full")
        assert cmd_init(args) == 0

        config_path = project / "configs" / "default.yaml"
        config = yaml.safe_load(config_path.read_text())
        assert config["storage"]["type"] == "postgresql"
        assert config["audit"]["enabled"] is True
        assert config["redaction"]["enabled"] is True

    def test_init_with_storage_override(self, tmp_path):
        """--storage override should change storage type."""
        project = tmp_path / "override_proj"
        args = _make_init_args(str(project), name="override", preset="dev", storage="postgresql")
        assert cmd_init(args) == 0

        config_path = project / "configs" / "default.yaml"
        config = yaml.safe_load(config_path.read_text())
        # Override should change storage to postgresql
        assert config["storage"]["type"] == "postgresql"
        # But embedding should remain hash (from dev preset)
        assert config["embedding"]["provider"] == "hash"

    def test_init_creates_all_directories(self, tmp_path):
        """Init should create all required directories."""
        project = tmp_path / "dir_proj"
        args = _make_init_args(str(project), name="dir_proj")
        assert cmd_init(args) == 0

        for d in ["configs/agents", "workspace/skills", "workspace/memory",
                  "workspace/uploads", "workspace/outputs", "workspace/workspace",
                  "data", "tests"]:
            assert (project / d).is_dir(), f"Directory {d} should exist"

    def test_init_creates_agent_config(self, tmp_path):
        """Init should create default agent config."""
        project = tmp_path / "agent_proj"
        args = _make_init_args(str(project), name="agent_proj")
        assert cmd_init(args) == 0

        agent_path = project / "configs" / "agents" / "default.yaml"
        assert agent_path.exists()
        agent = yaml.safe_load(agent_path.read_text())
        assert agent["name"] == "default"
        assert "tools" in agent

    def test_init_env_example_reflects_preset(self, tmp_path):
        """.env.example should reflect the selected preset."""
        project = tmp_path / "env_proj"
        args = _make_init_args(str(project), name="env_proj", preset="prod")
        assert cmd_init(args) == 0

        env_path = project / ".env.example"
        env_content = env_path.read_text(encoding="utf-8")
        assert "postgresql" in env_content
        assert "AGENTBASE_STORAGE__TYPE=postgresql" in env_content

    def test_init_dry_run(self, tmp_path):
        """Dry run should not create files."""
        project = tmp_path / "dry_proj"
        args = _make_init_args(str(project), name="dry_proj", dry_run=True)
        assert cmd_init(args) == 0

        # Config file should NOT exist (dry run)
        assert not (project / "configs" / "default.yaml").exists()

    def test_init_config_loadable(self, tmp_path):
        """Generated config should be valid YAML and loadable by AppConfig."""
        from agentbase.config.schema import AppConfig

        project = tmp_path / "loadable_proj"
        args = _make_init_args(str(project), name="loadable_proj")
        assert cmd_init(args) == 0

        config_path = project / "configs" / "default.yaml"
        raw = yaml.safe_load(config_path.read_text())
        # Should be loadable as AppConfig without errors
        config = AppConfig.model_validate(raw)
        assert config.app.name == "loadable_proj"
