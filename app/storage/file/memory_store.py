"""Memory 文件存储。

布局：
  data/memory/{agent_id}/messages.jsonl   # 追加写
  data/memory/{agent_id}/summaries.json   # 覆盖写（最新摘要）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.storage.file.base import append_jsonl, read_jsonl, write_json_atomic, write_jsonl_atomic, read_json


class MemoryStore:
    """消息流与摘要的文件存储（以 agent_id 为 key）。"""

    def _messages_path(self, agent_id: str) -> Path:
        return get_settings().data_dir / "memory" / agent_id / "messages.jsonl"

    def _summaries_path(self, agent_id: str) -> Path:
        return get_settings().data_dir / "memory" / agent_id / "summaries.json"

    def append_message(self, agent_id: str, record: dict[str, Any]) -> None:
        """追加一条消息记录。"""
        append_jsonl(self._messages_path(agent_id), record)

    def read_messages(self, agent_id: str) -> list[dict[str, Any]]:
        """读取全部消息记录。"""
        return read_jsonl(self._messages_path(agent_id))

    def read_window(self, agent_id: str, n: int) -> list[dict[str, Any]]:
        """读取最近 n 条消息。"""
        return self.read_messages(agent_id)[-n:]

    def save_summary(self, agent_id: str, summary: dict[str, Any]) -> None:
        """覆盖写入最新摘要。"""
        write_json_atomic(self._summaries_path(agent_id), summary)

    def get_summary(self, agent_id: str) -> dict[str, Any] | None:
        """读取最新摘要，不存在返回 None。"""
        return read_json(self._summaries_path(agent_id))

    def rewrite_messages(self, agent_id: str, messages: list[dict[str, Any]]) -> None:
        """原子性替换活跃消息窗口；原文件内容追加到 messages.bak.jsonl 作为审计 log。"""
        path = self._messages_path(agent_id)
        if path.exists():
            bak = path.with_name("messages.bak.jsonl")
            with bak.open("a", encoding="utf-8") as f:
                f.write(path.read_text(encoding="utf-8"))
        write_jsonl_atomic(path, messages)

    def count_messages(self, agent_id: str) -> int:
        return len(self.read_messages(agent_id))

    def delete_agent(self, agent_id: str) -> None:
        """删除该 agent 的全部记忆文件。"""
        import shutil
        d = get_settings().data_dir / "memory" / agent_id
        if d.exists():
            shutil.rmtree(d)
