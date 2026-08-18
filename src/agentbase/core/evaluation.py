"""Agent evaluation framework.

Provides a pluggable system for evaluating agent responses against
expected outcomes. Define test cases, run them against an agent,
and compute metrics (accuracy, relevance, latency).

Default: basic metric calculators with zero dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, runtime_checkable


@dataclass
class EvalCase:
    """A single evaluation test case."""
    id: str = ""
    query: str = ""
    expected: str = ""
    expected_keywords: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalResult:
    """Result of evaluating a single case."""
    case: EvalCase
    actual: str = ""
    passed: bool = False
    score: float = 0.0
    metrics: dict[str, float] = field(default_factory=dict)
    latency_ms: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case.id,
            "query": self.case.query,
            "expected": self.case.expected,
            "actual": self.actual[:500],
            "passed": self.passed,
            "score": self.score,
            "metrics": self.metrics,
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


@dataclass
class EvalReport:
    """Aggregated evaluation report."""
    name: str
    results: list[EvalResult] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed_count / self.total if self.total > 0 else 0.0

    @property
    def avg_score(self) -> float:
        scores = [r.score for r in self.results if r.score is not None]
        return sum(scores) / len(scores) if scores else 0.0

    @property
    def avg_latency_ms(self) -> float:
        latencies = [r.latency_ms for r in self.results if r.latency_ms > 0]
        return sum(latencies) / len(latencies) if latencies else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "total": self.total,
            "passed": self.passed_count,
            "failed": self.failed_count,
            "pass_rate": round(self.pass_rate, 4),
            "avg_score": round(self.avg_score, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "results": [r.to_dict() for r in self.results],
        }


@runtime_checkable
class Metric(Protocol):
    """Protocol for evaluation metrics."""

    name: str

    def compute(self, case: EvalCase, actual: str) -> float:
        """Compute a metric score (0.0 to 1.0)."""
        ...


class KeywordMatchMetric:
    """Checks if expected keywords appear in the response."""

    name = "keyword_match"

    def compute(self, case: EvalCase, actual: str) -> float:
        if not case.expected_keywords:
            return 1.0
        actual_lower = actual.lower()
        hits = sum(1 for kw in case.expected_keywords if kw.lower() in actual_lower)
        return hits / len(case.expected_keywords)


class ExactMatchMetric:
    """Checks if the response matches the expected answer exactly."""

    name = "exact_match"

    def compute(self, case: EvalCase, actual: str) -> float:
        if not case.expected:
            return 1.0
        return 1.0 if actual.strip().lower() == case.expected.strip().lower() else 0.0


class SubstringMatchMetric:
    """Checks if the expected answer is a substring of the response."""

    name = "substring_match"

    def compute(self, case: EvalCase, actual: str) -> float:
        if not case.expected:
            return 1.0
        return 1.0 if case.expected.strip().lower() in actual.lower() else 0.0


class EvaluationRunner:
    """Runs evaluation cases against an agent and computes metrics."""

    def __init__(self, *, metrics: list[Metric] | None = None) -> None:
        self._metrics: list[Metric] = metrics or [
            KeywordMatchMetric(),
            SubstringMatchMetric(),
        ]

    def add_metric(self, metric: Metric) -> None:
        self._metrics.append(metric)

    def evaluate(
        self,
        cases: list[EvalCase],
        agent_fn: Callable[[str], str],
        *,
        name: str = "evaluation",
    ) -> EvalReport:
        """Evaluate cases against an agent function.

        Args:
            cases: List of evaluation cases.
            agent_fn: Function that takes a query string and returns a response.
            name: Name for the evaluation report.

        Returns:
            An EvalReport with results.
        """
        report = EvalReport(name=name)
        for case in cases:
            import time

            start = time.perf_counter()
            try:
                actual = agent_fn(case.query)
                latency = (time.perf_counter() - start) * 1000

                metric_scores: dict[str, float] = {}
                for metric in self._metrics:
                    metric_scores[metric.name] = metric.compute(case, actual)

                avg_score = sum(metric_scores.values()) / len(metric_scores) if metric_scores else 0.0
                passed = avg_score >= 0.5

                report.results.append(EvalResult(
                    case=case,
                    actual=actual,
                    passed=passed,
                    score=avg_score,
                    metrics=metric_scores,
                    latency_ms=latency,
                ))
            except Exception as exc:
                latency = (time.perf_counter() - start) * 1000
                report.results.append(EvalResult(
                    case=case,
                    error=str(exc),
                    latency_ms=latency,
                ))

        report.finished_at = datetime.now(timezone.utc).isoformat()
        return report

    def evaluate_from_file(self, path: str, agent_fn: Callable[[str], str], *, name: str = "") -> EvalReport:
        """Load cases from a JSON file and evaluate."""
        import json
        from pathlib import Path

        data = json.loads(Path(path).read_text(encoding="utf-8"))
        cases = [
            EvalCase(
                id=item.get("id", str(i)),
                query=item["query"],
                expected=item.get("expected", ""),
                expected_keywords=item.get("expected_keywords", []),
                metadata=item.get("metadata", {}),
            )
            for i, item in enumerate(data)
        ]
        return self.evaluate(cases, agent_fn, name=name or Path(path).stem)

    def evaluate_from_yaml(self, path: str, agent_fn: Callable[[str], str], *, name: str = "") -> EvalReport:
        """Load cases from a YAML file and evaluate.

        YAML format::

            cases:
              - id: test_1
                query: "What is 2+2?"
                expected: "4"
                expected_keywords: ["4"]
              - id: test_2
                query: "Say hello"
                expected: "hello"
        """
        from pathlib import Path

        import yaml

        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        case_list = data.get("cases", []) if isinstance(data, dict) else []
        cases = [
            EvalCase(
                id=item.get("id", str(i)),
                query=item["query"],
                expected=item.get("expected", ""),
                expected_keywords=item.get("expected_keywords", []),
                metadata=item.get("metadata", {}),
            )
            for i, item in enumerate(case_list)
        ]
        return self.evaluate(cases, agent_fn, name=name or Path(path).stem)

    def save_report(self, report: EvalReport, path: str, *, format: str = "json") -> None:
        """Save an evaluation report to a file.

        Args:
            report: The report to save.
            path: Output file path.
            format: "json" or "yaml".
        """
        from pathlib import Path

        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        data = report.to_dict()

        if format == "yaml":
            import yaml
            output_path.write_text(
                yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
        else:
            import json
            output_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )


class LLMJudgeMetric:
    """LLM-as-Judge metric: uses an LLM to score the response.

    Asks the LLM to rate the response on a scale of 0-1 based on
    correctness and relevance to the expected answer.

    Requires ``openai`` package and an API key.

    Usage::

        from agentbase.core.evaluation import LLMJudgeMetric, EvaluationRunner

        runner = EvaluationRunner(metrics=[LLMJudgeMetric()])
    """

    name = "llm_judge"

    def __init__(
        self,
        *,
        model: str = "deepseek-chat",
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url

    def _get_client(self) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError("LLMJudgeMetric requires openai package") from exc
        import os
        kwargs: dict[str, Any] = {}
        key = self._api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("SILICONFLOW_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
        if key:
            kwargs["api_key"] = key
        base = self._base_url or os.environ.get("OPENAI_BASE_URL") or os.environ.get("SILICONFLOW_BASE_URL")
        if base:
            kwargs["base_url"] = base
        return OpenAI(**kwargs)

    def compute(self, case: "EvalCase", actual: str) -> float:
        prompt = (
            f"You are an evaluation judge. Rate the response on a scale of 0.0 to 1.0.\n\n"
            f"Question: {case.query}\n"
            f"Expected: {case.expected}\n"
            f"Response: {actual}\n\n"
            f"Respond with only a number between 0.0 and 1.0."
        )
        try:
            client = self._get_client()
            resp = client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=10,
            )
            score_text = resp.choices[0].message.content.strip()
            score = float(score_text)
            return max(0.0, min(1.0, score))
        except Exception:
            return 0.0


class BLEUMetric:
    """BLEU score metric for translation/generation quality.

    Computes BLEU-4 score between the response and expected answer.
    Uses a pure Python implementation (no external dependencies).

    Usage::

        from agentbase.core.evaluation import BLEUMetric, EvaluationRunner
        runner = EvaluationRunner(metrics=[BLEUMetric()])
    """

    name = "bleu"

    def compute(self, case: "EvalCase", actual: str) -> float:
        import math
        import re
        from collections import Counter

        def tokenize(text: str) -> list[str]:
            # Simple tokenizer: lowercase + split on non-word chars
            return re.findall(r"\w+", text.lower())

        def ngram_counter(tokens: list[str], n: int) -> Counter:
            return Counter(tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1))

        def modified_precision(ref_tokens: list[str], hyp_tokens: list[str], n: int) -> float:
            ref_ngrams = ngram_counter(ref_tokens, n)
            hyp_ngrams = ngram_counter(hyp_tokens, n)
            if not hyp_ngrams:
                return 0.0
            overlap = sum((ref_ngrams & hyp_ngrams).values())
            total = sum(hyp_ngrams.values())
            return overlap / total if total > 0 else 0.0

        ref = tokenize(case.expected)
        hyp = tokenize(actual)
        if not hyp:
            return 0.0

        # BLEU-4
        weights = [0.25] * 4
        precisions = []
        for n in range(1, 5):
            p = modified_precision(ref, hyp, n)
            precisions.append(p if p > 0 else 1e-10)

        # Brevity penalty
        bp = 1.0 if len(hyp) > len(ref) else math.exp(1 - len(ref) / max(len(hyp), 1))

        # Geometric mean of precisions
        try:
            log_avg = sum(w * math.log(p) for w, p in zip(weights, precisions))
            bleu = bp * math.exp(log_avg)
        except (ValueError, OverflowError):
            bleu = 0.0

        return max(0.0, min(1.0, bleu))


class ROUGEMetric:
    """ROUGE-L score metric for summarization quality.

    Computes ROUGE-L (Longest Common Subsequence) score between
    the response and expected answer. Pure Python, no dependencies.

    Usage::

        from agentbase.core.evaluation import ROUGEMetric, EvaluationRunner
        runner = EvaluationRunner(metrics=[ROUGEMetric()])
    """

    name = "rouge_l"

    def compute(self, case: "EvalCase", actual: str) -> float:
        import re

        def tokenize(text: str) -> list[str]:
            return re.findall(r"\w+", text.lower())

        def lcs_length(a: list[str], b: list[str]) -> int:
            m, n = len(a), len(b)
            dp = [[0] * (n + 1) for _ in range(m + 1)]
            for i in range(1, m + 1):
                for j in range(1, n + 1):
                    if a[i - 1] == b[j - 1]:
                        dp[i][j] = dp[i - 1][j - 1] + 1
                    else:
                        dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
            return dp[m][n]

        ref = tokenize(case.expected)
        hyp = tokenize(actual)
        if not ref or not hyp:
            return 0.0

        lcs_len = lcs_length(ref, hyp)
        precision = lcs_len / len(hyp)
        recall = lcs_len / len(ref)

        if precision + recall == 0:
            return 0.0
        f1 = 2 * precision * recall / (precision + recall)
        return f1
