"""Comprehensive test for all built-in but not-default-enabled providers.

Tests each provider end-to-end: initialization + actual operation.
Run: python scripts/test_all_providers.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Load .env file
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

# Ensure SiliconFlow/OpenAI env vars are set
if not os.environ.get("SILICONFLOW_BASE_URL"):
    os.environ["SILICONFLOW_BASE_URL"] = "https://api.siliconflow.cn/v1"
if not os.environ.get("OPENAI_BASE_URL"):
    os.environ["OPENAI_BASE_URL"] = os.environ.get("SILICONFLOW_BASE_URL")
if not os.environ.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = os.environ.get("SILICONFLOW_API_KEY", "")

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
SKIP = "\033[93mSKIP\033[0m"

results: list[tuple[str, str, str]] = []  # (provider, test, status)


def run_test(provider: str, name: str, fn):
    try:
        fn()
        results.append((provider, name, PASS))
        print(f"  [{PASS}] {provider}::{name}")
    except Exception as exc:
        results.append((provider, name, FAIL))
        print(f"  [{FAIL}] {provider}::{name}: {type(exc).__name__}: {exc}")


def skip_test(provider: str, name: str, reason: str):
    results.append((provider, name, SKIP))
    print(f"  [{SKIP}] {provider}::{name}  ({reason})")


def section(title: str):
    print(f"\n--- {title} ---")


# ── 1. InMemoryTracer ──────────────────────────────────────────────────

def test_inmemory_tracer():
    section("InMemoryTracer")
    from agentbase.core.tracer import InMemoryTracer, TracerRegistry, trace

    def test_basic():
        tracer = InMemoryTracer()
        with trace(tracer, "test_span", agent="default") as span:
            span.add_event("event_1")
            span.set_attribute("key", "value")
        all_traces = tracer.all_traces()
        assert len(all_traces) >= 1
        first_trace = list(all_traces.values())[0]
        assert len(first_trace) >= 1
        assert first_trace[0].name == "test_span"

    def test_registry():
        reg = TracerRegistry()
        reg.register("memory", InMemoryTracer)
        assert reg.has("memory")
        tracer = reg.create("memory")
        assert tracer is not None

    run_test("InMemoryTracer", "basic_trace", test_basic)
    run_test("InMemoryTracer", "registry", test_registry)


# ── 2. Redis Queue ─────────────────────────────────────────────────────

def test_redis_queue():
    section("Redis Queue")
    try:
        import redis  # noqa: F401
    except ImportError:
        skip_test("RedisQueue", "all", "redis package not installed")
        return

    from agentbase.core.queue import RedisRequestQueue, queue_registry

    def test_registry():
        assert queue_registry.has("redis")

    def test_submit_and_status():
        q = RedisRequestQueue(host="127.0.0.1", port=6379, db=0)
        # Clean up old tasks
        for t in q.list_tasks():
            q.cancel(t.id)
        task = q.submit(agent_name="default", message="hello redis")
        assert task.id is not None
        fetched = q.get_task(task.id)
        assert fetched is not None
        assert fetched.agent_name == "default"
        assert fetched.message == "hello redis"

    def test_process():
        q = RedisRequestQueue(host="127.0.0.1", port=6379, db=0)
        for t in q.list_tasks():
            q.cancel(t.id)
        q.submit(agent_name="default", message="job1")
        result = q.process_one(lambda t: {"output": f"processed-{t.message}"})
        assert result is not None

    def test_list_and_cancel():
        q = RedisRequestQueue(host="127.0.0.1", port=6379, db=0)
        t1 = q.submit(agent_name="default", message="task_a")
        assert t1
        t2 = q.submit(agent_name="default", message="task_b")
        tasks = q.list_tasks()
        assert len(tasks) >= 2
        ok = q.cancel(t2.id)
        assert ok
        # Cleanup
        for t in q.list_tasks():
            q.cancel(t.id)

    run_test("RedisQueue", "registry", test_registry)
    run_test("RedisQueue", "submit_and_status", test_submit_and_status)
    run_test("RedisQueue", "process", test_process)
    run_test("RedisQueue", "list_and_cancel", test_list_and_cancel)


# ── 3. OpenAI Embedding ────────────────────────────────────────────────

def test_openai_embedding():
    section("OpenAI Embedding (via SiliconFlow API)")
    try:
        import openai  # noqa: F401
    except ImportError:
        skip_test("OpenAIEmbedding", "all", "openai package not installed")
        return

    from agentbase.core.embeddings import OpenAIEmbeddingProvider, embedding_registry

    def test_registry():
        assert embedding_registry.has("openai")

    def test_embed():
        provider = OpenAIEmbeddingProvider(
            model="BAAI/bge-m3",
            api_key=os.environ.get("SILICONFLOW_API_KEY"),
            base_url=os.environ.get("SILICONFLOW_BASE_URL"),
            dimension=1024,
        )
        vec = provider.embed("hello world")
        assert len(vec) > 0

    def test_embed_batch():
        provider = OpenAIEmbeddingProvider(
            model="BAAI/bge-m3",
            api_key=os.environ.get("SILICONFLOW_API_KEY"),
            base_url=os.environ.get("SILICONFLOW_BASE_URL"),
            dimension=1024,
        )
        vecs = provider.embed_batch(["hello", "world"])
        assert len(vecs) == 2

    run_test("OpenAIEmbedding", "registry", test_registry)
    run_test("OpenAIEmbedding", "embed", test_embed)
    run_test("OpenAIEmbedding", "embed_batch", test_embed_batch)


# ── 4. SentenceTransformers ────────────────────────────────────────────

def test_sentence_transformers():
    section("SentenceTransformers (local model)")
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        skip_test("SentenceTransformers", "all", "sentence-transformers not installed")
        return

    from agentbase.core.embeddings import SentenceTransformersProvider, embedding_registry

    def test_registry():
        assert embedding_registry.has("sentence-transformers")

    def test_embed():
        provider = SentenceTransformersProvider(model="all-MiniLM-L6-v2")
        vec = provider.embed("hello world")
        assert len(vec) == 384

    def test_embed_batch():
        provider = SentenceTransformersProvider(model="all-MiniLM-L6-v2")
        vecs = provider.embed_batch(["hello", "world"])
        assert len(vecs) == 2

    run_test("SentenceTransformers", "registry", test_registry)
    run_test("SentenceTransformers", "embed", test_embed)
    run_test("SentenceTransformers", "embed_batch", test_embed_batch)


# ── 5. Tavily Search ───────────────────────────────────────────────────

def test_tavily_search():
    section("Tavily Search")
    try:
        import tavily  # noqa: F401
    except ImportError:
        skip_test("TavilySearch", "all", "tavily-python package not installed")
        return

    from agentbase.core.search import TavilySearch, search_registry

    def test_registry():
        assert search_registry.has("tavily")

    def test_search():
        provider = TavilySearch(api_key=os.environ.get("TAVILY_API_KEY", ""))
        results = provider.search("Python programming language", max_results=2)
        assert len(results) > 0

    run_test("TavilySearch", "registry", test_registry)
    run_test("TavilySearch", "search", test_search)


# ── 6. Langfuse Tracer ─────────────────────────────────────────────────

def test_langfuse_tracer():
    section("Langfuse Tracer")
    try:
        import langfuse  # noqa: F401
    except ImportError:
        skip_test("LangfuseTracer", "all", "langfuse package not installed")
        return

    from agentbase.core.tracer import LangfuseTracer, tracer_registry

    def test_registry():
        assert tracer_registry.has("langfuse")

    def test_init():
        tracer = LangfuseTracer()
        assert tracer is not None

    run_test("LangfuseTracer", "registry", test_registry)
    run_test("LangfuseTracer", "init", test_init)


# ── 7. OpenTelemetry Tracer ────────────────────────────────────────────

def test_opentelemetry_tracer():
    section("OpenTelemetry Tracer")
    try:
        import opentelemetry  # noqa: F401
    except ImportError:
        skip_test("OpenTelemetryTracer", "all", "opentelemetry not installed")
        return

    from agentbase.core.tracer import OpenTelemetryTracer, trace, tracer_registry

    def test_registry():
        assert tracer_registry.has("opentelemetry")

    def test_init_and_trace():
        tracer = OpenTelemetryTracer(service_name="agentbase-test")
        assert tracer is not None
        with trace(tracer, "test_span", agent="default") as span:
            span.add_event("event_1")

    run_test("OpenTelemetryTracer", "registry", test_registry)
    run_test("OpenTelemetryTracer", "init_and_trace", test_init_and_trace)


# ── 8. Neo4j Graph Provider ────────────────────────────────────────────

def test_neo4j_graph():
    section("Neo4j Graph Provider")
    try:
        import neo4j  # noqa: F401
    except ImportError:
        skip_test("Neo4jGraph", "all", "neo4j package not installed")
        return

    from agentbase.core.graph import Neo4jGraphProvider, graph_registry

    def test_registry():
        assert graph_registry.has("neo4j")

    def test_crud():
        from agentbase.core.graph import Entity
        provider = Neo4jGraphProvider(
            uri="bolt://127.0.0.1:7687",
            user="neo4j",
            password="test12345",
        )
        entity = Entity(name="Python", label="Language", description="A programming language", properties={"typed": True})
        entity_id = provider.add_entity(entity)
        results = provider.search_entities("Python")
        assert len(results) > 0
        if entity_id:
            provider.delete_entity(entity_id)
        provider.close()

    run_test("Neo4jGraph", "registry", test_registry)
    run_test("Neo4jGraph", "crud", test_crud)


# ── 9. LLM Document Parser ─────────────────────────────────────────────

def test_llm_document_parser():
    section("LLM Document Parser")
    try:
        import openai  # noqa: F401
    except ImportError:
        skip_test("LLMDocumentParser", "all", "openai package not installed")
        return

    import tempfile

    from agentbase.extensions.parsers import LLMDocumentParser

    def test_parse():
        test_content = "# Test Document\n\nThis is a test document about Python programming."
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(test_content)
            f.flush()
            parser = LLMDocumentParser(
                api_key=os.environ.get("DEEPSEEK_API_KEY"),
                base_url="https://api.deepseek.com/v1",
                model="deepseek-chat",
            )
            result = parser.parse(Path(f.name))
        assert len(result) > 0

    run_test("LLMDocumentParser", "parse", test_parse)


# ── 10. OCR Parser ─────────────────────────────────────────────────────

def test_ocr_parser():
    section("OCR Parser")
    import shutil

    if not shutil.which("tesseract"):
        skip_test("OCRParser", "all", "tesseract binary not installed on system")
        return

    import tempfile

    from PIL import Image, ImageDraw

    from agentbase.extensions.parsers import OCRParser

    def test_parse():
        img = Image.new("RGB", (200, 50), color="white")
        draw = ImageDraw.Draw(img)
        draw.text((10, 10), "Hello OCR", fill="black")
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            img.save(f, "PNG")
            f.flush()
            parser = OCRParser()
            result = parser.parse(Path(f.name))
        assert len(result) > 0

    run_test("OCRParser", "parse", test_parse)


# ── 11. MySQL Checkpointer ─────────────────────────────────────────────

def test_mysql_checkpointer():
    section("MySQL Checkpointer")
    from agentbase.registry.checkpointers import checkpointer_registry

    def test_registry():
        assert checkpointer_registry.has("mysql")

    run_test("MySQLSaver", "registry", test_registry)


# ── Main ───────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Provider Verification: All Built-in Providers")
    print("=" * 60)

    test_inmemory_tracer()
    test_redis_queue()
    test_openai_embedding()
    test_sentence_transformers()
    test_tavily_search()
    test_langfuse_tracer()
    test_opentelemetry_tracer()
    test_neo4j_graph()
    test_llm_document_parser()
    test_ocr_parser()
    test_mysql_checkpointer()

    # Summary
    print(f"\n{'=' * 60}")
    print("  SUMMARY")
    print(f"{'=' * 60}")
    total = len(results)
    passed = sum(1 for _, _, s in results if s == PASS)
    failed = sum(1 for _, _, s in results if s == FAIL)
    skipped = sum(1 for _, _, s in results if s == SKIP)

    for provider, name, status in results:
        print(f"  [{status}] {provider}::{name}")

    print(f"\n  Total: {passed} passed, {failed} failed, {skipped} skipped / {total}")
    print(f"{'=' * 60}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
