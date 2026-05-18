"""Memory 文件存储。

布局：
  data/memory/{agent_id}/messages.jsonl   # 追加写
  data/memory/{agent_id}/summaries.json   # 覆盖写（最新摘要）
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.storage.file.base import append_jsonl, read_jsonl, write_json_atomic, write_jsonl_atomic, read_json


class MemoryStore:
    """消息流与摘要的文件存储（以 agent_id 为 key）。

    类级别写穿缓存：所有实例共享同一份内存缓存，热路径上的 read_messages /
    count_messages / read_window 不再触发磁盘 I/O。写入时先落盘再更新缓存，
    保持崩溃安全性。
    """

    # class-level: shared across all instances
    _cache: dict[str, list[dict]] = {}
    _locks: dict[str, threading.Lock] = {}
    _meta_lock = threading.Lock()

    # ── lock helpers ──────────────────────────────────────────────────────────

    def _key_lock(self, agent_id: str) -> threading.Lock:
        with self._meta_lock:
            if agent_id not in self._locks:
                self._locks[agent_id] = threading.Lock()
            return self._locks[agent_id]

    def _get_cached(self, agent_id: str) -> list[dict]:
        """Return the cached messages list. Caller must hold the agent lock."""
        if agent_id not in self._cache:
            self._cache[agent_id] = read_jsonl(self._messages_path(agent_id))
        return self._cache[agent_id]

    # ── path helpers ──────────────────────────────────────────────────────────

    def _messages_path(self, agent_id: str) -> Path:
        return get_settings().data_dir / "memory" / agent_id / "messages.jsonl"

    def _summaries_path(self, agent_id: str) -> Path:
        return get_settings().data_dir / "memory" / agent_id / "summaries.json"

    # ── public API ────────────────────────────────────────────────────────────

    def append_message(self, agent_id: str, record: dict[str, Any]) -> None:
        """追加一条消息记录。"""
        append_jsonl(self._messages_path(agent_id), record)
        with self._key_lock(agent_id):
            if agent_id in self._cache:
                self._cache[agent_id].append(record)

    def read_messages(self, agent_id: str) -> list[dict[str, Any]]:
        """读取全部消息记录。"""
        with self._key_lock(agent_id):
            return list(self._get_cached(agent_id))

    def read_window(self, agent_id: str, n: int) -> list[dict[str, Any]]:
        """读取最近 n 条消息。"""
        with self._key_lock(agent_id):
            msgs = self._get_cached(agent_id)
            return list(msgs[-n:])

    def save_summary(self, agent_id: str, summary: dict[str, Any]) -> None:
        """覆盖写入最新摘要。"""
        write_json_atomic(self._summaries_path(agent_id), summary)

    def get_summary(self, agent_id: str) -> dict[str, Any] | None:
        """读取最新摘要，不存在返回 None。"""
        return read_json(self._summaries_path(agent_id))

    def bulk_write_messages(self, agent_id: str, messages: list[dict[str, Any]]) -> None:
        """一次性写入一批消息（用于 memory 继承）。单次 write_jsonl_atomic 代替 N 次 append_jsonl。"""
        write_jsonl_atomic(self._messages_path(agent_id), messages)
        with self._key_lock(agent_id):
            self._cache[agent_id] = list(messages)

    def rewrite_messages(self, agent_id: str, messages: list[dict[str, Any]]) -> None:
        """原子性替换活跃消息窗口；原文件内容追加到 messages.bak.jsonl 作为审计 log。"""
        path = self._messages_path(agent_id)
        if path.exists():
            bak = path.with_name("messages.bak.jsonl")
            with bak.open("a", encoding="utf-8") as f:
                f.write(path.read_text(encoding="utf-8"))
        write_jsonl_atomic(path, messages)
        with self._key_lock(agent_id):
            self._cache[agent_id] = list(messages)

    def count_messages(self, agent_id: str) -> int:
        with self._key_lock(agent_id):
            return len(self._get_cached(agent_id))

    def delete_agent(self, agent_id: str) -> None:
        """删除该 agent 的全部记忆文件。"""
        with self._key_lock(agent_id):
            self._cache.pop(agent_id, None)
        with self._meta_lock:
            self._locks.pop(agent_id, None)
        import shutil
        d = get_settings().data_dir / "memory" / agent_id
        if d.exists():
            shutil.rmtree(d)
