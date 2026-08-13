#!/usr/bin/env python
"""Cookbook: 注册自定义 Queue Provider。

演示如何通过 @register_queue_provider 装饰器注册一个自定义的
异步任务队列 Provider，替换默认的 MemoryRequestQueue。

本示例实现一个 FileRequestQueue：将任务持久化到 JSON 文件，
进程重启后任务不丢失。

运行方式:
    python examples/custom_queue.py
    python examples/custom_queue.py --help
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

from agentbase.core.queue import (
    MemoryRequestQueue,
    Task,
    TaskStatus,
    queue_registry,
)


class FileRequestQueue:
    """基于 JSON 文件持久化的请求队列。

    每个任务存储为单独的 JSON 文件，索引文件记录所有任务 ID。
    适合单机持久化场景，不支持多进程并发（仅用于演示）。
    """

    def __init__(self, *, data_dir: str = ".") -> None:
        self._data_dir = Path(data_dir) / "queue_tasks"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._index_file = self._data_dir / "index.json"
        self._memory = MemoryRequestQueue()  # 内存缓存
        self._load_from_disk()

    def _task_file(self, task_id: str) -> Path:
        return self._data_dir / f"{task_id}.json"

    def _load_from_disk(self) -> None:
        """启动时从磁盘加载所有任务。"""
        if self._index_file.exists():
            task_ids = json.loads(self._index_file.read_text(encoding="utf-8"))
            for tid in task_ids:
                tf = self._task_file(tid)
                if tf.exists():
                    data = json.loads(tf.read_text(encoding="utf-8"))
                    data["status"] = TaskStatus(data["status"])
                    task = Task(**data)
                    self._memory._tasks[tid] = task

    def _save_task(self, task: Task) -> None:
        """将单个任务保存到磁盘。"""
        self._task_file(task.id).write_text(
            json.dumps(task.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # 更新索引
        task_ids = list(self._memory._tasks.keys())
        self._index_file.write_text(
            json.dumps(task_ids, indent=2),
            encoding="utf-8",
        )

    def submit(self, *, agent_name: str, message: str, **kwargs: Any) -> Task:
        task = self._memory.submit(agent_name=agent_name, message=message, **kwargs)
        self._save_task(task)
        return task

    def get_task(self, task_id: str) -> Task | None:
        return self._memory.get_task(task_id)

    def list_tasks(self, **kwargs: Any) -> list[Task]:
        return self._memory.list_tasks(**kwargs)

    def cancel(self, task_id: str) -> bool:
        result = self._memory.cancel(task_id)
        if result:
            task = self._memory.get_task(task_id)
            if task:
                self._save_task(task)
        return result

    def update_task(self, task_id: str, **fields: Any) -> Task | None:
        task = self._memory.update_task(task_id, **fields)
        if task:
            self._save_task(task)
        return task

    def process_one(self, handler: Callable[[Task], dict[str, Any]]) -> Task | None:
        task = self._memory.process_one(handler)
        if task:
            self._save_task(task)
        return task


def main() -> None:
    parser = argparse.ArgumentParser(
        description="注册并测试自定义 File Queue Provider",
    )
    parser.parse_args()

    print("=" * 60)
    print("Cookbook: 自定义 Queue Provider (File-based)")
    print("=" * 60)

    # 1. 注册前
    print("\n1. 注册前已注册 Provider:", queue_registry.names())

    # 2. 注册（直接注册 factory 类）
    queue_registry.register("file", FileRequestQueue, override=True)
    print("2. 已注册 'file' Provider")

    # 3. 创建队列实例（使用临时目录）
    with tempfile.TemporaryDirectory() as tmpdir:
        queue = queue_registry.create("file", data_dir=tmpdir)
        print(f"3. 创建队列: {queue.__class__.__name__}, data_dir={tmpdir}")

        # 4. 提交任务
        task1 = queue.submit(agent_name="default", message="Hello AgentBase")
        task2 = queue.submit(agent_name="coder", message="Write some code")
        print("\n4. 提交 2 个任务:")
        print(f"   Task 1: id={task1.id[:8]}..., agent={task1.agent_name}, message='{task1.message}'")
        print(f"   Task 2: id={task2.id[:8]}..., agent={task2.agent_name}, message='{task2.message}'")

        # 5. 验证持久化
        task_files = list(Path(tmpdir).glob("queue_tasks/*.json"))
        print(f"\n5. 磁盘上的任务文件数: {len(task_files) - 1}")  # -1 for index.json

        # 6. 查询任务
        retrieved = queue.get_task(task1.id)
        print(f"\n6. 查询 Task 1: agent={retrieved.agent_name}, status={retrieved.status.value}")

        # 7. 列出任务
        all_tasks = queue.list_tasks()
        print(f"7. 列出所有任务: {len(all_tasks)} 个")

        # 8. 处理任务
        def handler(task: Task) -> dict[str, Any]:
            return {"output": f"Processed: {task.message}"}

        processed = queue.process_one(handler)
        print(f"\n8. 处理一个任务: {processed.id[:8]}..., result={processed.result}")
        print(f"   状态变更: {TaskStatus.PENDING.value} → {processed.status.value}")

    # 9. 验证注册表
    print(f"\n9. 最终已注册 Provider: {queue_registry.names()}")

    print("\n" + "=" * 60)
    print("[OK] 示例完成！在 config 中设置 queue.provider: file 即可使用")
    print("=" * 60)


if __name__ == "__main__":
    main()
