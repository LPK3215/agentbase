"""Test MySQLSaver checkpointer with real checkpoint operations."""
import pymysql

from agentbase.runtime.checkpoint_mysql import MySQLSaver

DSN_HOST = "127.0.0.1"
DSN_PORT = 3307
DSN_USER = "agentbase"
DSN_PASS = "agentbase"
DSN_DB = "agentbase"

conn = pymysql.connect(
    host=DSN_HOST, port=DSN_PORT, user=DSN_USER, password=DSN_PASS,
    database=DSN_DB, charset="utf8mb4",
    cursorclass=pymysql.cursors.DictCursor,
)

saver = MySQLSaver(conn)
saver.setup()
print("[PASS] MySQLSaver setup OK")

# Test put
config = {"configurable": {"thread_id": "test-1", "checkpoint_ns": ""}}
checkpoint = {
    "id": "ckpt-001",
    "channel_values": {},
    "channel_versions": {},
    "versions_seen": {},
    "pending_sends": [],
}
saved_config = saver.put(config, checkpoint, {"source": "input", "step": 1, "writes": {}}, {})
print(f"[PASS] Put OK: {saved_config}")

# Test get_tuple
result = saver.get_tuple({"configurable": {"thread_id": "test-1"}})
assert result is not None, "get_tuple returned None"
assert result.checkpoint["id"] == "ckpt-001"
print(f"[PASS] Get OK: checkpoint_id={result.checkpoint['id']}")
print(f"       metadata: {result.metadata}")

# Test list
results = list(saver.list({"configurable": {"thread_id": "test-1"}}))
assert len(results) >= 1
print(f"[PASS] List OK: {len(results)} checkpoint(s)")

# Test put_writes
saver.put_writes(
    {"configurable": {"thread_id": "test-1", "checkpoint_ns": "", "checkpoint_id": "ckpt-001"}},
    [("my_channel", "hello")],
    task_id="task-001",
)
print("[PASS] Put writes OK")

# Test get_tuple with writes
result2 = saver.get_tuple({"configurable": {"thread_id": "test-1"}})
assert len(result2.pending_writes) >= 1
print(f"[PASS] Get with writes OK: {len(result2.pending_writes)} write(s)")

# Test delete_thread
saver.delete_thread("test-1")
result3 = saver.get_tuple({"configurable": {"thread_id": "test-1"}})
assert result3 is None
print("[PASS] Delete thread OK")

from pathlib import Path

# Test factory
from agentbase.config.schema import CheckpointerConfig
from agentbase.factories.checkpointer_factory import build_checkpointer

spec = CheckpointerConfig(type="mysql", dsn=f"mysql://{DSN_USER}:{DSN_PASS}@{DSN_HOST}:{DSN_PORT}/{DSN_DB}")
factory_saver = build_checkpointer(spec, root_dir=Path("."))
print(f"[PASS] Factory build OK: {type(factory_saver).__name__}")

# Cleanup
conn.close()
print("\n=== All MySQLSaver tests passed! ===")
