"""Tests for the evaluation framework — covers data classes, metrics, runner, file I/O.

Tests verify:
1. EvalCase / EvalResult / EvalReport data classes and properties
2. KeywordMatchMetric — keyword hit/miss, case-insensitive, empty keywords
3. ExactMatchMetric — exact match, case-insensitive, whitespace, empty expected
4. SubstringMatchMetric — substring match, case-insensitive, empty expected
5. BLEUMetric — identical text, partial match, empty, brevity penalty
6. ROUGEMetric — identical, partial, empty, LCS computation
7. LLMJudgeMetric — mock OpenAI client, import error, exception handling
8. EvaluationRunner — evaluate, add_metric, error handling, scoring
9. evaluate_from_file / evaluate_from_yaml — file loading
10. save_report — JSON / YAML output
11. Metric Protocol compliance
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# EvalCase
# ---------------------------------------------------------------------------


class TestEvalCase:
    def test_default_values(self):
        from agentbase.core.evaluation import EvalCase

        case = EvalCase()
        assert case.id == ""
        assert case.query == ""
        assert case.expected == ""
        assert case.expected_keywords == []
        assert case.metadata == {}

    def test_with_values(self):
        from agentbase.core.evaluation import EvalCase

        case = EvalCase(
            id="test_1",
            query="What is 2+2?",
            expected="4",
            expected_keywords=["4", "four"],
            metadata={"category": "math"},
        )
        assert case.id == "test_1"
        assert case.query == "What is 2+2?"
        assert case.expected == "4"
        assert "4" in case.expected_keywords
        assert case.metadata["category"] == "math"


# ---------------------------------------------------------------------------
# EvalResult
# ---------------------------------------------------------------------------


class TestEvalResult:
    def test_to_dict(self):
        from agentbase.core.evaluation import EvalCase, EvalResult

        case = EvalCase(id="t1", query="Q", expected="E")
        result = EvalResult(
            case=case,
            actual="A" * 600,
            passed=True,
            score=0.85,
            metrics={"keyword_match": 1.0},
            latency_ms=42.5,
        )
        d = result.to_dict()
        assert d["case_id"] == "t1"
        assert d["query"] == "Q"
        assert d["expected"] == "E"
        assert len(d["actual"]) <= 500  # Truncated
        assert d["passed"] is True
        assert d["score"] == 0.85
        assert d["latency_ms"] == 42.5
        assert d["error"] is None

    def test_to_dict_with_error(self):
        from agentbase.core.evaluation import EvalCase, EvalResult

        case = EvalCase(id="t2", query="Q")
        result = EvalResult(case=case, error="Something went wrong")
        d = result.to_dict()
        assert d["error"] == "Something went wrong"
        assert d["passed"] is False
        assert d["actual"] == ""


# ---------------------------------------------------------------------------
# EvalReport
# ---------------------------------------------------------------------------


class TestEvalReport:
    def _make_report(self):
        from agentbase.core.evaluation import EvalCase, EvalReport, EvalResult

        report = EvalReport(name="test_report")
        case1 = EvalCase(id="p1", query="Q1", expected="hello")
        case2 = EvalCase(id="f1", query="Q2", expected="world")
        report.results = [
            EvalResult(case=case1, actual="hello", passed=True, score=0.9, latency_ms=10),
            EvalResult(case=case2, actual="wrong", passed=False, score=0.1, latency_ms=20),
        ]
        return report

    def test_total(self):
        report = self._make_report()
        assert report.total == 2

    def test_passed_count(self):
        report = self._make_report()
        assert report.passed_count == 1

    def test_failed_count(self):
        report = self._make_report()
        assert report.failed_count == 1

    def test_pass_rate(self):
        report = self._make_report()
        assert report.pass_rate == 0.5

    def test_pass_rate_empty(self):
        from agentbase.core.evaluation import EvalReport

        report = EvalReport(name="empty")
        assert report.pass_rate == 0.0

    def test_avg_score(self):
        report = self._make_report()
        assert report.avg_score == 0.5

    def test_avg_score_empty(self):
        from agentbase.core.evaluation import EvalReport

        report = EvalReport(name="empty")
        assert report.avg_score == 0.0

    def test_avg_latency_ms(self):
        report = self._make_report()
        assert report.avg_latency_ms == 15.0

    def test_avg_latency_ms_empty(self):
        from agentbase.core.evaluation import EvalReport

        report = EvalReport(name="empty")
        assert report.avg_latency_ms == 0.0

    def test_to_dict(self):
        report = self._make_report()
        d = report.to_dict()
        assert d["name"] == "test_report"
        assert d["total"] == 2
        assert d["passed"] == 1
        assert d["failed"] == 1
        assert d["pass_rate"] == 0.5
        assert len(d["results"]) == 2

    def test_finished_at_default_none(self):
        from agentbase.core.evaluation import EvalReport

        report = EvalReport(name="test")
        assert report.finished_at is None


# ---------------------------------------------------------------------------
# KeywordMatchMetric
# ---------------------------------------------------------------------------


class TestKeywordMatchMetric:
    def test_all_keywords_present(self):
        from agentbase.core.evaluation import EvalCase, KeywordMatchMetric

        metric = KeywordMatchMetric()
        case = EvalCase(expected_keywords=["hello", "world"])
        assert metric.compute(case, "hello world") == 1.0

    def test_partial_keywords(self):
        from agentbase.core.evaluation import EvalCase, KeywordMatchMetric

        metric = KeywordMatchMetric()
        case = EvalCase(expected_keywords=["hello", "world", "foo"])
        score = metric.compute(case, "hello world")
        assert 0 < score < 1.0
        assert score == pytest.approx(2 / 3, rel=1e-2)

    def test_no_keywords_present(self):
        from agentbase.core.evaluation import EvalCase, KeywordMatchMetric

        metric = KeywordMatchMetric()
        case = EvalCase(expected_keywords=["hello", "world"])
        assert metric.compute(case, "foo bar") == 0.0

    def test_case_insensitive(self):
        from agentbase.core.evaluation import EvalCase, KeywordMatchMetric

        metric = KeywordMatchMetric()
        case = EvalCase(expected_keywords=["Hello", "WORLD"])
        assert metric.compute(case, "hello world") == 1.0

    def test_empty_keywords(self):
        from agentbase.core.evaluation import EvalCase, KeywordMatchMetric

        metric = KeywordMatchMetric()
        case = EvalCase(expected_keywords=[])
        assert metric.compute(case, "anything") == 1.0

    def test_name(self):
        from agentbase.core.evaluation import KeywordMatchMetric

        assert KeywordMatchMetric.name == "keyword_match"


# ---------------------------------------------------------------------------
# ExactMatchMetric
# ---------------------------------------------------------------------------


class TestExactMatchMetric:
    def test_exact_match(self):
        from agentbase.core.evaluation import EvalCase, ExactMatchMetric

        metric = ExactMatchMetric()
        case = EvalCase(expected="hello")
        assert metric.compute(case, "hello") == 1.0

    def test_case_insensitive(self):
        from agentbase.core.evaluation import EvalCase, ExactMatchMetric

        metric = ExactMatchMetric()
        case = EvalCase(expected="Hello")
        assert metric.compute(case, "hello") == 1.0

    def test_whitespace_trimmed(self):
        from agentbase.core.evaluation import EvalCase, ExactMatchMetric

        metric = ExactMatchMetric()
        case = EvalCase(expected="  hello  ")
        assert metric.compute(case, " hello ") == 1.0

    def test_no_match(self):
        from agentbase.core.evaluation import EvalCase, ExactMatchMetric

        metric = ExactMatchMetric()
        case = EvalCase(expected="hello")
        assert metric.compute(case, "world") == 0.0

    def test_empty_expected(self):
        from agentbase.core.evaluation import EvalCase, ExactMatchMetric

        metric = ExactMatchMetric()
        case = EvalCase(expected="")
        assert metric.compute(case, "anything") == 1.0

    def test_name(self):
        from agentbase.core.evaluation import ExactMatchMetric

        assert ExactMatchMetric.name == "exact_match"


# ---------------------------------------------------------------------------
# SubstringMatchMetric
# ---------------------------------------------------------------------------


class TestSubstringMatchMetric:
    def test_substring_present(self):
        from agentbase.core.evaluation import EvalCase, SubstringMatchMetric

        metric = SubstringMatchMetric()
        case = EvalCase(expected="hello")
        assert metric.compute(case, "hello world") == 1.0

    def test_substring_absent(self):
        from agentbase.core.evaluation import EvalCase, SubstringMatchMetric

        metric = SubstringMatchMetric()
        case = EvalCase(expected="hello")
        assert metric.compute(case, "world foo") == 0.0

    def test_case_insensitive(self):
        from agentbase.core.evaluation import EvalCase, SubstringMatchMetric

        metric = SubstringMatchMetric()
        case = EvalCase(expected="Hello")
        assert metric.compute(case, "say hello world") == 1.0

    def test_empty_expected(self):
        from agentbase.core.evaluation import EvalCase, SubstringMatchMetric

        metric = SubstringMatchMetric()
        case = EvalCase(expected="")
        assert metric.compute(case, "anything") == 1.0

    def test_name(self):
        from agentbase.core.evaluation import SubstringMatchMetric

        assert SubstringMatchMetric.name == "substring_match"


# ---------------------------------------------------------------------------
# BLEUMetric
# ---------------------------------------------------------------------------


class TestBLEUMetric:
    def test_identical_text(self):
        from agentbase.core.evaluation import BLEUMetric, EvalCase

        metric = BLEUMetric()
        case = EvalCase(expected="the quick brown fox jumps over the lazy dog")
        score = metric.compute(case, "the quick brown fox jumps over the lazy dog")
        assert score == pytest.approx(1.0, rel=1e-2)

    def test_completely_different(self):
        from agentbase.core.evaluation import BLEUMetric, EvalCase

        metric = BLEUMetric()
        case = EvalCase(expected="hello world")
        score = metric.compute(case, "foo bar baz")
        assert score < 0.1

    def test_empty_actual(self):
        from agentbase.core.evaluation import BLEUMetric, EvalCase

        metric = BLEUMetric()
        case = EvalCase(expected="hello world")
        assert metric.compute(case, "") == 0.0

    def test_partial_match(self):
        from agentbase.core.evaluation import BLEUMetric, EvalCase

        metric = BLEUMetric()
        case = EvalCase(expected="the quick brown fox")
        score = metric.compute(case, "the quick brown cat")
        assert 0 < score < 1.0

    def test_score_in_range(self):
        from agentbase.core.evaluation import BLEUMetric, EvalCase

        metric = BLEUMetric()
        case = EvalCase(expected="hello world foo bar")
        score = metric.compute(case, "hello world")
        assert 0.0 <= score <= 1.0

    def test_name(self):
        from agentbase.core.evaluation import BLEUMetric

        assert BLEUMetric.name == "bleu"


# ---------------------------------------------------------------------------
# ROUGEMetric
# ---------------------------------------------------------------------------


class TestROUGEMetric:
    def test_identical_text(self):
        from agentbase.core.evaluation import EvalCase, ROUGEMetric

        metric = ROUGEMetric()
        case = EvalCase(expected="the quick brown fox")
        score = metric.compute(case, "the quick brown fox")
        assert score == pytest.approx(1.0, rel=1e-2)

    def test_completely_different(self):
        from agentbase.core.evaluation import EvalCase, ROUGEMetric

        metric = ROUGEMetric()
        case = EvalCase(expected="hello world")
        score = metric.compute(case, "foo bar baz")
        assert score == 0.0

    def test_empty_actual(self):
        from agentbase.core.evaluation import EvalCase, ROUGEMetric

        metric = ROUGEMetric()
        case = EvalCase(expected="hello world")
        assert metric.compute(case, "") == 0.0

    def test_empty_expected(self):
        from agentbase.core.evaluation import EvalCase, ROUGEMetric

        metric = ROUGEMetric()
        case = EvalCase(expected="")
        assert metric.compute(case, "hello") == 0.0

    def test_partial_match(self):
        from agentbase.core.evaluation import EvalCase, ROUGEMetric

        metric = ROUGEMetric()
        case = EvalCase(expected="the quick brown fox jumps")
        score = metric.compute(case, "the quick brown cat")
        assert 0 < score < 1.0

    def test_score_in_range(self):
        from agentbase.core.evaluation import EvalCase, ROUGEMetric

        metric = ROUGEMetric()
        case = EvalCase(expected="hello world foo bar")
        score = metric.compute(case, "hello world")
        assert 0.0 <= score <= 1.0

    def test_name(self):
        from agentbase.core.evaluation import ROUGEMetric

        assert ROUGEMetric.name == "rouge_l"


# ---------------------------------------------------------------------------
# LLMJudgeMetric
# ---------------------------------------------------------------------------


class TestLLMJudgeMetric:
    def test_name(self):
        from agentbase.core.evaluation import LLMJudgeMetric

        assert LLMJudgeMetric.name == "llm_judge"

    def test_init_defaults(self):
        from agentbase.core.evaluation import LLMJudgeMetric

        metric = LLMJudgeMetric()
        assert metric._model == "deepseek-chat"
        assert metric._api_key is None
        assert metric._base_url is None

    def test_init_with_custom_values(self):
        from agentbase.core.evaluation import LLMJudgeMetric

        metric = LLMJudgeMetric(model="gpt-4", api_key="sk-test", base_url="https://custom.api")
        assert metric._model == "gpt-4"
        assert metric._api_key == "sk-test"
        assert metric._base_url == "https://custom.api"

    def test_compute_success(self):
        from agentbase.core.evaluation import EvalCase, LLMJudgeMetric

        metric = LLMJudgeMetric(api_key="sk-test")

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "0.85"
        mock_resp.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_resp

        with patch.object(metric, "_get_client", return_value=mock_client):
            case = EvalCase(query="Q", expected="E")
            score = metric.compute(case, "A")

        assert score == 0.85
        mock_client.chat.completions.create.assert_called_once()

    def test_compute_clamps_score(self):
        from agentbase.core.evaluation import EvalCase, LLMJudgeMetric

        metric = LLMJudgeMetric(api_key="sk-test")

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "1.5"
        mock_resp.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_resp

        with patch.object(metric, "_get_client", return_value=mock_client):
            case = EvalCase(query="Q", expected="E")
            score = metric.compute(case, "A")

        assert score == 1.0

    def test_compute_exception_returns_zero(self):
        from agentbase.core.evaluation import EvalCase, LLMJudgeMetric

        metric = LLMJudgeMetric(api_key="sk-test")

        with patch.object(metric, "_get_client", side_effect=RuntimeError("API error")):
            case = EvalCase(query="Q", expected="E")
            score = metric.compute(case, "A")

        assert score == 0.0


# ---------------------------------------------------------------------------
# Metric Protocol compliance
# ---------------------------------------------------------------------------


class TestMetricProtocol:
    def test_keyword_match_is_metric(self):
        from agentbase.core.evaluation import KeywordMatchMetric, Metric

        metric = KeywordMatchMetric()
        assert isinstance(metric, Metric)

    def test_exact_match_is_metric(self):
        from agentbase.core.evaluation import ExactMatchMetric, Metric

        metric = ExactMatchMetric()
        assert isinstance(metric, Metric)

    def test_substring_match_is_metric(self):
        from agentbase.core.evaluation import Metric, SubstringMatchMetric

        metric = SubstringMatchMetric()
        assert isinstance(metric, Metric)

    def test_bleu_is_metric(self):
        from agentbase.core.evaluation import BLEUMetric, Metric

        metric = BLEUMetric()
        assert isinstance(metric, Metric)

    def test_rouge_is_metric(self):
        from agentbase.core.evaluation import Metric, ROUGEMetric

        metric = ROUGEMetric()
        assert isinstance(metric, Metric)


# ---------------------------------------------------------------------------
# EvaluationRunner
# ---------------------------------------------------------------------------


class TestEvaluationRunner:
    def test_default_metrics(self):
        from agentbase.core.evaluation import EvaluationRunner, KeywordMatchMetric, SubstringMatchMetric

        runner = EvaluationRunner()
        assert len(runner._metrics) == 2
        assert any(isinstance(m, KeywordMatchMetric) for m in runner._metrics)
        assert any(isinstance(m, SubstringMatchMetric) for m in runner._metrics)

    def test_custom_metrics(self):
        from agentbase.core.evaluation import EvaluationRunner, ExactMatchMetric

        runner = EvaluationRunner(metrics=[ExactMatchMetric()])
        assert len(runner._metrics) == 1
        assert isinstance(runner._metrics[0], ExactMatchMetric)

    def test_add_metric(self):
        from agentbase.core.evaluation import EvaluationRunner, ExactMatchMetric

        runner = EvaluationRunner()
        runner.add_metric(ExactMatchMetric())
        assert len(runner._metrics) == 3

    def test_evaluate_success(self):
        from agentbase.core.evaluation import EvalCase, EvaluationRunner

        cases = [
            EvalCase(id="t1", query="hello", expected="hello", expected_keywords=["hello"]),
            EvalCase(id="t2", query="world", expected="world", expected_keywords=["world"]),
        ]
        runner = EvaluationRunner()
        report = runner.evaluate(cases, lambda q: q)

        assert report.total == 2
        assert report.passed_count == 2
        assert report.failed_count == 0
        assert report.finished_at is not None

    def test_evaluate_with_failures(self):
        from agentbase.core.evaluation import EvalCase, EvaluationRunner

        cases = [
            EvalCase(id="t1", query="hello", expected="hello", expected_keywords=["hello"]),
            EvalCase(id="t2", query="world", expected="world", expected_keywords=["world"]),
        ]
        runner = EvaluationRunner()
        # Agent always returns "wrong" — should fail both cases
        report = runner.evaluate(cases, lambda q: "wrong")

        assert report.total == 2
        assert report.passed_count == 0
        assert report.failed_count == 2

    def test_evaluate_agent_exception(self):
        from agentbase.core.evaluation import EvalCase, EvaluationRunner

        cases = [EvalCase(id="t1", query="hello", expected="hello")]
        runner = EvaluationRunner()

        def failing_agent(q):
            raise RuntimeError("Agent crashed")

        report = runner.evaluate(cases, failing_agent)

        assert report.total == 1
        assert report.failed_count == 1
        assert report.results[0].error is not None
        assert "Agent crashed" in report.results[0].error

    def test_evaluate_scoring(self):
        from agentbase.core.evaluation import EvalCase, EvaluationRunner

        cases = [EvalCase(id="t1", query="hello", expected="hello", expected_keywords=["hello"])]
        runner = EvaluationRunner()
        report = runner.evaluate(cases, lambda q: "hello")

        result = report.results[0]
        assert result.score > 0.5
        assert result.passed is True
        assert "keyword_match" in result.metrics
        assert "substring_match" in result.metrics
        assert result.latency_ms > 0

    def test_evaluate_report_name(self):
        from agentbase.core.evaluation import EvalCase, EvaluationRunner

        cases = [EvalCase(id="t1", query="hello", expected="hello")]
        runner = EvaluationRunner()
        report = runner.evaluate(cases, lambda q: q, name="custom_eval")

        assert report.name == "custom_eval"

    def test_evaluate_empty_cases(self):
        from agentbase.core.evaluation import EvaluationRunner

        runner = EvaluationRunner()
        report = runner.evaluate([], lambda q: q)

        assert report.total == 0
        assert report.pass_rate == 0.0


# ---------------------------------------------------------------------------
# File I/O tests
# ---------------------------------------------------------------------------


class TestEvaluationFileIO:
    def test_evaluate_from_file(self, tmp_path):
        from agentbase.core.evaluation import EvaluationRunner

        cases_data = [
            {"id": "t1", "query": "hello", "expected": "hello", "expected_keywords": ["hello"]},
            {"id": "t2", "query": "world", "expected": "world"},
        ]
        cases_file = tmp_path / "cases.json"
        cases_file.write_text(json.dumps(cases_data), encoding="utf-8")

        runner = EvaluationRunner()
        report = runner.evaluate_from_file(str(cases_file), lambda q: q)

        assert report.total == 2
        assert report.name == "cases"

    def test_evaluate_from_file_custom_name(self, tmp_path):
        from agentbase.core.evaluation import EvaluationRunner

        cases_data = [{"id": "t1", "query": "hello", "expected": "hello"}]
        cases_file = tmp_path / "cases.json"
        cases_file.write_text(json.dumps(cases_data), encoding="utf-8")

        runner = EvaluationRunner()
        report = runner.evaluate_from_file(str(cases_file), lambda q: q, name="custom")

        assert report.name == "custom"

    def test_evaluate_from_yaml(self, tmp_path):
        from agentbase.core.evaluation import EvaluationRunner

        yaml_content = """
cases:
  - id: t1
    query: "hello"
    expected: "hello"
    expected_keywords: ["hello"]
  - id: t2
    query: "world"
    expected: "world"
"""
        cases_file = tmp_path / "cases.yaml"
        cases_file.write_text(yaml_content, encoding="utf-8")

        runner = EvaluationRunner()
        report = runner.evaluate_from_yaml(str(cases_file), lambda q: q)

        assert report.total == 2
        assert report.name == "cases"

    def test_evaluate_from_yaml_empty(self, tmp_path):
        from agentbase.core.evaluation import EvaluationRunner

        cases_file = tmp_path / "empty.yaml"
        cases_file.write_text("cases: []", encoding="utf-8")

        runner = EvaluationRunner()
        report = runner.evaluate_from_yaml(str(cases_file), lambda q: q)

        assert report.total == 0

    def test_save_report_json(self, tmp_path):
        from agentbase.core.evaluation import EvalCase, EvalReport, EvalResult, EvaluationRunner

        report = EvalReport(name="test_save")
        report.results = [
            EvalResult(case=EvalCase(id="t1", query="Q", expected="E"), actual="A", passed=True, score=0.9),
        ]
        report.finished_at = "2024-01-01T00:00:00Z"

        runner = EvaluationRunner()
        output_file = tmp_path / "report.json"
        runner.save_report(report, str(output_file), format="json")

        data = json.loads(output_file.read_text(encoding="utf-8"))
        assert data["name"] == "test_save"
        assert data["total"] == 1
        assert data["passed"] == 1

    def test_save_report_yaml(self, tmp_path):
        from agentbase.core.evaluation import EvalCase, EvalReport, EvalResult, EvaluationRunner

        report = EvalReport(name="test_save_yaml")
        report.results = [
            EvalResult(case=EvalCase(id="t1", query="Q", expected="E"), actual="A", passed=True, score=0.9),
        ]
        report.finished_at = "2024-01-01T00:00:00Z"

        runner = EvaluationRunner()
        output_file = tmp_path / "report.yaml"
        runner.save_report(report, str(output_file), format="yaml")

        content = output_file.read_text(encoding="utf-8")
        assert "test_save_yaml" in content
        assert "passed: 1" in content

    def test_save_report_creates_parent_dirs(self, tmp_path):
        from agentbase.core.evaluation import EvalReport, EvaluationRunner

        report = EvalReport(name="test")
        runner = EvaluationRunner()
        output_file = tmp_path / "subdir" / "deeper" / "report.json"
        runner.save_report(report, str(output_file), format="json")

        assert output_file.exists()
