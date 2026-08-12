"""Verify sections 4-16 of backend-boundaries.md.

Run: python scripts/test_remaining_sections.py
"""
from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path

env_file = Path(__file__).resolve().parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.split("#")[0].strip()
        if val and key not in os.environ:
            os.environ[key] = val

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
SKIP = "\033[93mSKIP\033[0m"

results: list[tuple[str, str, str]] = []


def run_test(section: str, name: str, fn):
    try:
        fn()
        results.append((section, name, PASS))
        print(f"  [{PASS}] {section}::{name}")
    except Exception as exc:
        results.append((section, name, FAIL))
        print(f"  [{FAIL}] {section}::{name}: {type(exc).__name__}: {exc}")


def skip_test(section: str, name: str, reason: str):
    results.append((section, name, SKIP))
    print(f"  [{SKIP}] {section}::{name}  ({reason})")


def section(title: str):
    print(f"\n--- {title} ---")


# ── 4. Async Task Queue ────────────────────────────────────────────────

def test_queue():
    section("4. Async Task Queue")
    from agentbase.core.queue import queue_registry, MemoryRequestQueue

    def test_none():
        assert queue_registry.has("memory")
        q = queue_registry.create("memory")
        assert q is not None

    def test_memory():
        q = MemoryRequestQueue()
        t = q.submit(agent_name="test", message="hello")
        assert t.id is not None
        assert q.get_task(t.id) is not None
        tasks = q.list_tasks()
        assert len(tasks) >= 1
        q.cancel(t.id)

    def test_redis():
        if not _has_pkg("redis"):
            skip_test("Queue", "redis", "redis not installed")
            return
        from agentbase.core.queue import RedisRequestQueue
        assert queue_registry.has("redis")
        q = RedisRequestQueue(host="127.0.0.1", port=6379, db=0)
        for t in q.list_tasks():
            q.cancel(t.id)
        t = q.submit(agent_name="test", message="redis test")
        assert t.id is not None
        fetched = q.get_task(t.id)
        assert fetched is not None
        q.cancel(t.id)

    run_test("Queue", "none/memory registry", test_none)
    run_test("Queue", "memory CRUD", test_memory)
    run_test("Queue", "redis CRUD", test_redis)


# ── 5. Agent Tools (32) ────────────────────────────────────────────────

def test_tools():
    section("5. Agent Tools (32)")
    # Trigger auto-discovery via bootstrap
    from agentbase.registry.bootstrap import bootstrap_registries
    from agentbase.config.schema import ExtensionsConfig
    ext_cfg = ExtensionsConfig(
        autodiscover=["agentbase.extensions.tools", "agentbase.extensions.middleware"],
        extra_modules=[],
    )
    bootstrap_registries(ext_cfg)
    from agentbase.registry.tools import tool_registry

    def test_count():
        tools = tool_registry.names()
        print(f"       Registered tools: {len(tools)}")
        assert len(tools) >= 20, f"Expected >= 20 tools, got {len(tools)}"

    def test_categories():
        tools = tool_registry.names()
        categories = {
            "filesystem": ["read_file", "write_file", "grep", "list_workspace"],
            "time": ["get_time", "now_local"],
            "skills": ["skill_list", "skill_get", "skill_create", "skill_update", "skill_delete", "skill_search"],
            "memory": ["memory_save", "memory_get", "memory_list", "memory_search", "memory_delete"],
            "knowledge": ["kb_add", "kb_get", "kb_list", "kb_search", "kb_update", "kb_delete", "kb_ingest", "kb_batch_ingest"],
            "web": ["web_search", "web_fetch"],
            "mcp": ["mcp_list_tools", "mcp_call_tool"],
            "code": ["code_execute"],
        }
        for cat, expected in categories.items():
            missing = [t for t in expected if t not in tools]
            if missing:
                raise AssertionError(f"Category '{cat}' missing: {missing}")

    def test_echo():
        echo_factory = tool_registry.get("echo")
        echo_fn = echo_factory({})  # Build the actual tool
        result = echo_fn.invoke({"text": "hello"})
        assert "hello" in str(result)

    def test_get_time():
        t_factory = tool_registry.get("get_time")
        t_fn = t_factory({})  # Build the actual tool
        result = t_fn.invoke({})
        assert "20" in str(result)  # year 202x

    def test_list_workspace():
        lw_factory = tool_registry.get("list_workspace")
        lw_fn = lw_factory({"workspace_dir": str(Path(__file__).resolve().parent.parent / "workspace")})
        result = lw_fn.invoke({})
        assert result is not None

    run_test("Tools", "count >= 20", test_count)
    run_test("Tools", "all categories present", test_categories)
    run_test("Tools", "echo works", test_echo)
    run_test("Tools", "get_time works", test_get_time)
    run_test("Tools", "list_workspace works", test_list_workspace)


# ── 6. Middleware (5) ──────────────────────────────────────────────────

def test_middleware():
    section("6. Middleware (5)")
    # Auto-discovery already triggered in test_tools
    from agentbase.registry.middleware import middleware_registry

    def test_registered():
        names = middleware_registry.names()
        print(f"       Registered middleware: {names}")
        expected = ["request_logger", "retry", "timeout", "summary", "cache"]
        for m in expected:
            assert m in names, f"Missing middleware: {m}"

    def test_request_logger():
        # request_logger is a function, not a class — just verify it's registered
        assert middleware_registry.has("request_logger")

    def test_cache():
        from agentbase.extensions.middleware.cache import CacheMiddleware
        mw = CacheMiddleware()
        assert mw is not None

    def test_retry():
        assert middleware_registry.has("retry")

    def test_timeout():
        assert middleware_registry.has("timeout")

    def test_summary():
        assert middleware_registry.has("summary")

    run_test("Middleware", "all 5 registered", test_registered)
    run_test("Middleware", "request_logger registered", test_request_logger)
    run_test("Middleware", "cache init", test_cache)
    run_test("Middleware", "retry registered", test_retry)
    run_test("Middleware", "timeout registered", test_timeout)
    run_test("Middleware", "summary registered", test_summary)


# ── 7. Pluggable Providers (9 registries) ──────────────────────────────

def test_registries():
    section("7. Pluggable Providers (9 registries)")

    def test_parser_registry():
        from agentbase.core.parsers import parser_registry
        import agentbase.extensions.parsers  # noqa: F401
        exts = [".txt", ".md", ".pdf", ".docx", ".html", ".xlsx", ".pptx"]
        for e in exts:
            assert parser_registry.has(e), f"Missing parser for {e}"

    def test_embedding_registry():
        from agentbase.core.embeddings import embedding_registry
        assert embedding_registry.has("hash")

    def test_search_registry():
        from agentbase.core.search import search_registry
        assert search_registry.has("duckduckgo")

    def test_queue_registry():
        from agentbase.core.queue import queue_registry
        assert queue_registry.has("memory")

    def test_tracer_registry():
        from agentbase.core.tracer import tracer_registry
        assert tracer_registry.has("null")
        assert tracer_registry.has("memory")

    def test_graph_registry():
        from agentbase.core.graph import graph_registry
        assert graph_registry.has("null")
        assert graph_registry.has("memory")

    def test_storage_factory():
        from agentbase.core.storage import create_storage
        # SQLite
        s = create_storage(db_path=Path("/tmp/test_registry.db"))
        assert s is not None
        # PostgreSQL
        s = create_storage(dsn="postgresql://agentbase:agentbase@127.0.0.1:5432/agentbase")
        assert s is not None

    def test_checkpointer_registry():
        from agentbase.registry.checkpointers import checkpointer_registry
        names = checkpointer_registry.names()
        print(f"       Checkpointers: {names}")
        assert "memory" in names
        assert "sqlite" in names
        assert "postgres" in names

    def test_mcp_registry():
        from agentbase.core.mcp import mcp_registry
        assert mcp_registry.has("memory")

    def test_tool_registry():
        # Auto-discovery already triggered above
        from agentbase.registry.tools import tool_registry
        assert len(tool_registry.names()) >= 20

    run_test("Registry", "parser (7 exts)", test_parser_registry)
    run_test("Registry", "embedding", test_embedding_registry)
    run_test("Registry", "search", test_search_registry)
    run_test("Registry", "queue", test_queue_registry)
    run_test("Registry", "tracer", test_tracer_registry)
    run_test("Registry", "graph", test_graph_registry)
    run_test("Registry", "storage factory", test_storage_factory)
    run_test("Registry", "checkpointer", test_checkpointer_registry)
    run_test("Registry", "mcp client", test_mcp_registry)
    run_test("Registry", "tools", test_tool_registry)


# ── 8. Tracing & Observability ─────────────────────────────────────────

def test_tracing():
    section("8. Tracing & Observability")
    from agentbase.runtime.logging import configure_logging, SecretRedactionFilter
    import logging as _logging
    from agentbase.core.tracer import NullTracer, InMemoryTracer, trace

    def test_json_logging():
        configure_logging(level="INFO")

    def test_null_tracer():
        tracer = NullTracer()
        with trace(tracer, "test") as span:
            span.add_event("event")

    def test_inmemory_tracer():
        tracer = InMemoryTracer()
        with trace(tracer, "test_span", agent="default") as span:
            span.add_event("event_1")
        all_traces = tracer.all_traces()
        assert len(all_traces) >= 1

    def test_secret_redaction():
        filt = SecretRedactionFilter()
        record = _logging.LogRecord(
            name="test", level=_logging.INFO, pathname="", lineno=0,
            msg="postgres://user:passw0rd@localhost:5432/db", args=(), exc_info=None,
        )
        filt.filter(record)
        assert "passw0rd" not in str(record.msg)
        assert "***" in str(record.msg)

        record2 = _logging.LogRecord(
            name="test", level=_logging.INFO, pathname="", lineno=0,
            msg="OPENAI_API_KEY=sk-abc123secret", args=(), exc_info=None,
        )
        filt.filter(record2)
        assert "sk-abc123secret" not in str(record2.msg)

    run_test("Tracing", "JSON logging setup", test_json_logging)
    run_test("Tracing", "NullTracer", test_null_tracer)
    run_test("Tracing", "InMemoryTracer", test_inmemory_tracer)
    run_test("Tracing", "secret redaction", test_secret_redaction)


# ── 9. Evaluation Framework ────────────────────────────────────────────

def test_evaluation():
    section("9. Evaluation Framework")
    from agentbase.core.evaluation import (
        EvaluationRunner, KeywordMatchMetric, ExactMatchMetric,
        SubstringMatchMetric, LLMJudgeMetric, BLEUMetric, ROUGEMetric,
    )

    def test_keyword_match():
        from agentbase.core.evaluation import KeywordMatchMetric, EvalCase
        metric = KeywordMatchMetric()
        case = EvalCase(query="What is Python?", expected_keywords=["Python", "language"])
        score = metric.compute(case, "Python is a programming language")
        assert score > 0

    def test_exact_match():
        from agentbase.core.evaluation import ExactMatchMetric, EvalCase
        metric = ExactMatchMetric()
        case = EvalCase(query="Say hello", expected="hello")
        score = metric.compute(case, "hello")
        assert score == 1.0

    def test_substring_match():
        from agentbase.core.evaluation import SubstringMatchMetric, EvalCase
        metric = SubstringMatchMetric()
        case = EvalCase(query="What is Python?", expected="Python")
        score = metric.compute(case, "Python is great")
        assert score == 1.0

    def test_bleu():
        from agentbase.core.evaluation import BLEUMetric, EvalCase
        metric = BLEUMetric()
        case = EvalCase(query="Translate", expected="the cat sat on the mat")
        score = metric.compute(case, "the cat sat on the mat")
        assert score > 0.5

    def test_rouge():
        from agentbase.core.evaluation import ROUGEMetric, EvalCase
        metric = ROUGEMetric()
        case = EvalCase(query="Summarize", expected="the cat sat on the mat")
        score = metric.compute(case, "the cat on the mat")
        assert score > 0

    def test_runner():
        runner = EvaluationRunner()
        assert runner is not None

    run_test("Evaluation", "KeywordMatchMetric", test_keyword_match)
    run_test("Evaluation", "ExactMatchMetric", test_exact_match)
    run_test("Evaluation", "SubstringMatchMetric", test_substring_match)
    run_test("Evaluation", "BLEUMetric", test_bleu)
    run_test("Evaluation", "ROUGEMetric", test_rouge)
    run_test("Evaluation", "EvaluationRunner", test_runner)


# ── 10. Deployment ─────────────────────────────────────────────────────

def test_deployment():
    section("10. Deployment")
    root = Path(__file__).resolve().parent.parent

    def test_dockerfile():
        assert (root / "Dockerfile").exists(), "Dockerfile not found"

    def test_docker_compose():
        p = root / "docker-compose.yml"
        if not p.exists():
            p = root / "docker-compose.yaml"
        assert p.exists(), "docker-compose not found"

    def test_k8s():
        k8s_dir = root / "deploy" / "k8s"
        assert k8s_dir.exists(), "deploy/k8s not found"
        files = list(k8s_dir.rglob("*"))
        assert len(files) >= 3, f"Expected >= 3 K8s files, got {len(files)}"

    def test_nginx():
        p = root / "deploy" / "nginx" / "nginx.conf"
        assert p.exists(), "nginx.conf not found"
        content = p.read_text(encoding="utf-8")
        assert "proxy_pass" in content or "upstream" in content

    def test_deploy_dir():
        assert (root / "deploy").exists()
        assert (root / "Dockerfile").exists()

    run_test("Deployment", "Dockerfile", test_dockerfile)
    run_test("Deployment", "docker-compose", test_docker_compose)
    run_test("Deployment", "K8s manifests", test_k8s)
    run_test("Deployment", "Nginx config", test_nginx)
    run_test("Deployment", "deploy/ directory", test_deploy_dir)


# ── 11. CLI Commands ───────────────────────────────────────────────────

def test_cli():
    section("11. CLI Commands")
    os.environ["PYTHONIOENCODING"] = "utf-8"

    def run_cli(args: list[str]) -> tuple[int, str]:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "agentbase"] + args,
                capture_output=True, text=True, timeout=30,
                cwd=str(Path(__file__).resolve().parent.parent),
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                encoding="utf-8", errors="replace",
            )
            return result.returncode, (result.stdout or "") + (result.stderr or "")
        except subprocess.TimeoutExpired:
            return -1, "timeout"

    def test_doctor():
        code, output = run_cli(["doctor"])
        # doctor may return non-zero if some checks fail (e.g. missing API keys)
        # but it should always produce output
        assert "assembly" in output or "check" in output.lower() or "OK" in output, f"doctor output unexpected: {output[:200]}"

    def test_agents():
        code, output = run_cli(["agents"])
        assert code == 0, f"agents failed: {output[:200]}"

    def test_extensions():
        code, output = run_cli(["extensions"])
        assert code == 0, f"extensions failed: {output[:200]}"

    def test_backup():
        code, output = run_cli(["backup", "-o", "data/cli_test_backup.json", "--format", "json"])
        assert code == 0, f"backup failed: {output[:200]}"

    run_test("CLI", "doctor", test_doctor)
    run_test("CLI", "agents", test_agents)
    run_test("CLI", "extensions", test_extensions)
    run_test("CLI", "backup", test_backup)


# ── 12-16: Quick checks ────────────────────────────────────────────────

def test_remaining():
    section("12-16: Security / Audio / Tests / Docs / Backlog")
    root = Path(__file__).resolve().parent.parent

    def test_security_code():
        from agentbase.extensions.auth import JWTAuth, Role, Permission
        auth = JWTAuth(secret="test")
        token = auth.create_token(user_id="u1", roles=[Role.ADMIN])
        payload = auth.verify_token(token)
        assert payload is not None
        assert auth.has_permission(payload, Permission.ADMIN)

    def test_audio_transcribe():
        if not _has_pkg("openai"):
            skip_test("Security/Audio", "transcribe tool", "openai not installed")
            return
        # Just verify the tool exists
        import agentbase.extensions.tools  # noqa: F401
        from agentbase.registry.tools import tool_registry
        assert "transcribe" in tool_registry.names()

    def test_test_count():
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--co", "-q", "tests/unit/"],
            capture_output=True, text=True, timeout=60,
            cwd=str(root),
        )
        # Count collected tests
        lines = [l for l in result.stdout.splitlines() if "::" in l]
        count = len(lines)
        print(f"       Collected {count} unit tests")
        assert count >= 100, f"Expected >= 100 tests, got {count}"

    def test_docs_exist():
        docs = ["README.md", "docs/quickstart.md", "docs/core-services.md",
                "docs/extensions.md", "docs/backend-boundaries.md"]
        for d in docs:
            assert (root / d).exists(), f"Missing doc: {d}"

    def test_pyproject():
        p = root / "pyproject.toml"
        assert p.exists(), "pyproject.toml not found"
        content = p.read_text()
        # Check optional deps
        assert "rag" in content or "embeddings" in content

    run_test("Security", "JWT + RBAC library", test_security_code)
    run_test("Audio", "transcribe tool registered", test_audio_transcribe)
    run_test("Tests", "unit test count >= 100", test_test_count)
    run_test("Docs", "all 5 docs exist", test_docs_exist)
    run_test("Config", "pyproject.toml with optional deps", test_pyproject)


# ── Helpers ────────────────────────────────────────────────────────────

def _has_pkg(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


# ── Main ───────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Sections 4-16 Verification")
    print("=" * 60)

    test_queue()
    test_tools()
    test_middleware()
    test_registries()
    test_tracing()
    test_evaluation()
    test_deployment()
    test_cli()
    test_remaining()

    # Summary
    print(f"\n{'=' * 60}")
    print("  SUMMARY")
    print(f"{'=' * 60}")
    total = len(results)
    passed = sum(1 for _, _, s in results if s == PASS)
    failed = sum(1 for _, _, s in results if s == FAIL)
    skipped = sum(1 for _, _, s in results if s == SKIP)

    for sec, name, status in results:
        print(f"  [{status}] {sec}::{name}")

    print(f"\n  Total: {passed} passed, {failed} failed, {skipped} skipped / {total}")
    print(f"{'=' * 60}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
