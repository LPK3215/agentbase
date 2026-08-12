from __future__ import annotations

from agentbase.runtime.events import EventType, RuntimeEvent


def _make_runner():
    from pathlib import Path

    from agentbase.config.schema import AppConfig
    from agentbase.factories.agent_factory import AgentFactory
    from agentbase.runtime.runner import AgentRunner

    app_config = AppConfig()
    factory = AgentFactory.__new__(AgentFactory)
    factory.root_dir = Path(".")
    factory.app_config = app_config
    factory._backend = None
    factory._checkpointer = None
    return AgentRunner(factory=factory, app_config=app_config)


def test_normalize_interrupt_event():
    runner = _make_runner()
    event = ("updates", {"interrupts": [{"reason": "approval needed", "resume_point": "step1"}]})
    result = runner._normalize_event(event, thread_id="T1", agent_name="default")
    assert result.type == EventType.INTERRUPT
    assert "approval needed" in result.data["reason"]


def test_normalize_messages_event():
    runner = _make_runner()
    event = ("messages", {"content": "hello"})
    result = runner._normalize_event(event, thread_id="T1", agent_name="default")
    assert result.type == EventType.MESSAGE_DELTA


def test_normalize_updates_with_messages():
    runner = _make_runner()
    event = ("updates", {"messages": [{"role": "assistant", "content": "hi"}]})
    result = runner._normalize_event(event, thread_id="T1", agent_name="default")
    assert result.type == EventType.UPDATE


def test_normalize_raw_event():
    runner = _make_runner()
    event = ("unknown_mode", {"data": "test"})
    result = runner._normalize_event(event, thread_id="T1", agent_name="default")
    assert result.type == EventType.RAW


def test_runtime_event_to_dict():
    ev = RuntimeEvent(type=EventType.RUN_STARTED, thread_id="T1", agent="default", data={"msg": "hi"})
    d = ev.to_dict()
    assert d["type"] == "run.started"
    assert d["thread_id"] == "T1"


def test_event_type_enum():
    assert EventType.RUN_STARTED == "run.started"
    assert EventType.INTERRUPT == "interrupt"
    assert EventType.RUN_ERROR == "run.error"