from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from agentbase.config.settings import get_env_settings


@pytest.fixture(autouse=True)
def _clear_env_cache():
    get_env_settings.cache_clear()
    yield
    get_env_settings.cache_clear()


@pytest.fixture
def isolated_env(monkeypatch):
    for key in list(os.environ.keys()):
        if key.startswith("agentbase_") or key.endswith("_API_KEY") or key.endswith("_BASE_URL"):
            monkeypatch.delenv(key, raising=False)
    yield monkeypatch


@pytest.fixture
def tmp_workspace(tmp_path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "memory").mkdir()
    (ws / "skills").mkdir()
    (ws / "memory" / "AGENTS.md").write_text("# Agent Memory\n## Editing Conventions\n- test\n", encoding="utf-8")
    return ws


@pytest.fixture
def mock_model(monkeypatch):
    """Mock the model so that create_deep_agent gets a usable model object.

    The key insight: deepagents' resolve_model() calls init_chat_model(model, ...)
    when model is a string. We mock init_chat_model to return a FakeModel
    that mimics a LangChain chat model. This way the agent factory can
    build a real agent without needing API credentials.
    """

    class FakeModel:
        """A fake chat model that mimics LangChain's BaseChatModel interface."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args
            self.kwargs = kwargs
            self.model_name = kwargs.get("model", "fake-model")

        def invoke(self, messages: Any, **kwargs: Any) -> Any:
            # Return a simple response object
            class FakeResponse:
                def __init__(self, msgs):
                    self.messages = msgs if isinstance(msgs, list) else [msgs]

                @property
                def content(self):
                    return "Mock response"

            return FakeResponse(messages)

        def stream(self, messages: Any, **kwargs: Any) -> Any:
            yield messages

        def bind_tools(self, tools: Any, **kwargs: Any) -> "FakeModel":
            return self

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            return self.invoke(*args, **kwargs)

        def count(self, text: str) -> int:
            """Rough token count for testing."""
            return len(text) // 4

        def get_num_tokens(self, text: str) -> int:
            """Alias for count."""
            return len(text) // 4

        def get_num_tokens_from_messages(self, messages: Any) -> int:
            """Rough token count from messages."""
            return 10

        def __getattr__(self, name: str) -> Any:
            """Auto-respond to any non-dunder method deepagents checks for."""
            if name.startswith("_"):
                raise AttributeError(name)
            # Return a callable for any unknown method
            def _noop(*args: Any, **kwargs: Any) -> Any:
                return None
            return _noop

    def _fake_init(model: Any = None, **kwargs: Any) -> FakeModel:
        return FakeModel(model=model, **kwargs)

    # Mock at the source so both model_factory and deepagents.resolve_model use it
    monkeypatch.setattr("langchain.chat_models.init_chat_model", _fake_init)
    return FakeModel


@pytest.fixture
def bootstrapped():
    from agentbase.config.schema import ExtensionsConfig
    from agentbase.registry.bootstrap import bootstrap_registries

    bootstrap_registries(ExtensionsConfig(), force=True)
    yield


@pytest.fixture

def app_config():
    """Return a default AppConfig for testing."""
    from agentbase.config.schema import AppConfig
    return AppConfig()


@pytest.fixture

def tmp_config_dir(tmp_path):
    """Create a temporary project config directory with default.yaml and agent."""
    import yaml
    config_dir = tmp_path / "configs"
    config_dir.mkdir(parents=True)

    app_data = {
        "app": {"name": "test", "env": "test", "log_level": "DEBUG"},
        "model": {"provider": "openai", "name": "gpt-4.1-mini", "temperature": 0, "api_key_env": "TEST_API_KEY"},
        "checkpointer": {"type": "memory"},
        "storage": {"type": "sqlite", "db_dir": "data"},
        "runtime": {"default_agent": "default", "config_dir": "configs", "workspace_dir": "workspace"},
        "auth": {"type": "none"},
        "rate_limit": {"enabled": False},
        "extensions": {"autodiscover": [], "extra_modules": []},
    }
    (config_dir / "default.yaml").write_text(
        yaml.dump(app_data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    agents_dir = config_dir / "agents"
    agents_dir.mkdir(parents=True)
    agent_data = {
        "name": "default",
        "description": "Test agent",
        "system_prompt": "You are a test agent.",
        "tools": [],
        "middleware": [],
        "capabilities": [],
    }
    (agents_dir / "default.yaml").write_text(
        yaml.dump(agent_data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    return tmp_path


@pytest.fixture(autouse=True)
def clean_model_cache():
    """Clear the model cache before and after each test."""
    from agentbase.factories.model_factory import clear_model_cache
    clear_model_cache()
    yield
    clear_model_cache()