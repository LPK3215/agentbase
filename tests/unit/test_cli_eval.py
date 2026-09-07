"""Unit tests for the `agentbase eval` CLI command (self-assessment)."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentbase.cli import (
    _build_eval_runner,
    _parse_inline_case,
    build_parser,
    cmd_eval,
)


def _make_fake_runtime(output_text: str = "The capital of France is Paris."):
    """A minimal runtime stub that answers every eval query with a fixed reply."""
    invoke = MagicMock(return_value={"output_text": output_text, "thread_id": "t", "result": {}})
    runner = SimpleNamespace(invoke=invoke)
    app_config = SimpleNamespace(runtime=SimpleNamespace(default_agent="default"))
    return SimpleNamespace(get_agent=lambda name: object(), runner=runner, app_config=app_config)


@pytest.fixture
def fake_build_runtime(monkeypatch):
    """Point cli.build_runtime at a stub so cmd_eval never needs a real agent."""

    def _patch(output_text: str = "The capital of France is Paris."):
        fake = _make_fake_runtime(output_text=output_text)
        monkeypatch.setattr("agentbase.cli.build_runtime", lambda root: fake)
        return fake

    return _patch


class TestEvalParser:
    def test_parser_has_eval_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["eval", "--case", "What is 2+2?||4"])
        assert args.command == "eval"
        assert args.cases == ["What is 2+2?||4"]
        assert args.suite is None
        assert args.metric is None
        assert args.agent is None
        assert args.name is None
        assert args.output is None
        assert args.format == "json"
        assert hasattr(args, "func")

    def test_parser_eval_repeatable_cases(self):
        parser = build_parser()
        args = parser.parse_args(["eval", "--case", "a||1", "--case", "b||2"])
        assert args.cases == ["a||1", "b||2"]

    def test_parser_eval_suite_and_metric_and_output(self):
        parser = build_parser()
        args = parser.parse_args(
            ["eval", "--suite", "suite.yaml", "--metric", "bleu", "--metric", "exact_match",
             "--name", "my-eval", "--output", "report.json", "--format", "yaml", "--agent", "worker"]
        )
        assert args.command == "eval"
        assert args.suite == "suite.yaml"
        assert args.metric == ["bleu", "exact_match"]
        assert args.name == "my-eval"
        assert args.output == "report.json"
        assert args.format == "yaml"
        assert args.agent == "worker"

    def test_parser_eval_rejects_unknown_metric(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["eval", "--case", "q||a", "--metric", "not_a_metric"])

    def test_parser_eval_requires_case_or_suite_at_parse_time(self):
        # argparse-level it parses; cmd_eval is responsible for the usage error.
        parser = build_parser()
        args = parser.parse_args(["eval"])
        assert args.command == "eval"
        assert args.cases is None
        assert args.suite is None


class TestParseInlineCase:
    def test_full_form(self):
        case = _parse_inline_case("What is 2+2?||4||four,four")
        assert case.query == "What is 2+2?"
        assert case.expected == "4"
        assert case.expected_keywords == ["four", "four"]

    def test_query_expected_only(self):
        case = _parse_inline_case("Hello||hi")
        assert case.query == "Hello"
        assert case.expected == "hi"
        assert case.expected_keywords == []

    def test_query_only(self):
        case = _parse_inline_case("Just a query")
        assert case.query == "Just a query"
        assert case.expected == ""
        assert case.expected_keywords == []


class TestBuildEvalRunner:
    def test_empty_selection_uses_defaults(self):
        runner = _build_eval_runner([])
        assert [m.name for m in runner._metrics] == ["keyword_match", "substring_match"]

    def test_none_selection_uses_defaults(self):
        runner = _build_eval_runner(None)
        assert [m.name for m in runner._metrics] == ["keyword_match", "substring_match"]

    def test_custom_metrics(self):
        runner = _build_eval_runner(["bleu", "rouge_l", "exact_match"])
        assert [m.name for m in runner._metrics] == ["bleu", "rouge_l", "exact_match"]

    def test_unknown_metric_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown metric 'bogus'"):
            _build_eval_runner(["bogus"])


class TestCmdEval:
    def test_requires_case_or_suite(self, capsys):
        args = SimpleNamespace(cases=None, suite=None)
        result = cmd_eval(args)
        assert result == 2

    def test_pass_returns_zero(self, fake_build_runtime, tmp_path, capsys):
        fake_build_runtime(output_text="The capital of France is Paris.")
        output = tmp_path / "report.json"
        args = SimpleNamespace(
            root=None, agent=None, suite=None, cases=["Capital of France?||Paris||Paris"],
            metric=None, name=None, output=str(output), format="json",
        )
        result = cmd_eval(args)
        assert result == 0
        import json

        data = json.loads(output.read_text(encoding="utf-8"))
        assert data["total"] == 1
        assert data["passed"] == 1
        assert data["failed"] == 0
        assert data["pass_rate"] == 1.0

    def test_fail_returns_one(self, fake_build_runtime):
        fake_build_runtime(output_text="I don't know.")
        args = SimpleNamespace(
            root=None, agent=None, suite=None, cases=["Capital of France?||Paris||Paris"],
            metric=None, name=None, output=None, format="json",
        )
        result = cmd_eval(args)
        assert result == 1

    def test_suite_yaml_and_yaml_report(self, fake_build_runtime, tmp_path):
        fake_build_runtime(output_text="42")
        suite = tmp_path / "suite.yaml"
        suite.write_text(
            "cases:\n"
            "  - id: add\n"
            "    query: What is 20+22?\n"
            "    expected: '42'\n"
            "    expected_keywords: ['42']\n",
            encoding="utf-8",
        )
        output = tmp_path / "report.yaml"
        args = SimpleNamespace(
            root=None, agent=None, suite=str(suite), cases=None,
            metric=None, name=None, output=str(output), format="yaml",
        )
        result = cmd_eval(args)
        assert result == 0
        import yaml

        data = yaml.safe_load(output.read_text(encoding="utf-8"))
        assert data["total"] == 1
        assert data["passed"] == 1

    def test_suite_file_missing_returns_two(self, fake_build_runtime, tmp_path):
        fake_build_runtime()
        args = SimpleNamespace(
            root=None, agent=None, suite=str(tmp_path / "missing.yaml"), cases=None,
            metric=None, name=None, output=None, format="json",
        )
        result = cmd_eval(args)
        assert result == 2

    def test_unknown_metric_returns_two(self, fake_build_runtime):
        fake_build_runtime()
        args = SimpleNamespace(
            root=None, agent=None, suite=None, cases=["q||a"],
            metric=["bogus"], name=None, output=None, format="json",
        )
        result = cmd_eval(args)
        assert result == 2

    def test_per_case_isolation_uses_fresh_thread(self, fake_build_runtime):
        fake = fake_build_runtime(output_text="42")
        args = SimpleNamespace(
            root=None, agent=None, suite=None,
            cases=["a?||42", "b?||42", "c?||42"],
            metric=None, name=None, output=None, format="json",
        )
        result = cmd_eval(args)
        assert result == 0
        # One fresh thread_id per case → three invocations with distinct ids.
        assert fake.runner.invoke.call_count == 3
        thread_ids = {call.kwargs["thread_id"] for call in fake.runner.invoke.call_args_list}
        assert len(thread_ids) == 3
