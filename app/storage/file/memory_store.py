"""Memory 文件存储。

布局：
  data/memory/{session_id}/messages.jsonl   # 追加写
  data/memory/{session_id}/summaries.json   # 覆盖写（最新摘要）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.storage.file.base import append_jsonl, read_jsonl, write_json_atomic, read_json


class MemoryStore:
    """消息流与摘要的文件存储。"""

    def _messages_path(self, session_id: str) -> Path:
        return get_settings().data_dir / "memory" / session_id / "messages.jsonl"

    def _summaries_path(self, session_id: str) -> Path:
        return get_settings().data_dir / "memory" / session_id / "summaries.json"

    def append_message(self, session_id: str, record: dict[str, Any]) -> None:
        """追加一条消息记录。"""
        append_jsonl(self._messages_path(session_id), record)

    def read_messages(self, session_id: str) -> list[dict[str, Any]]:
        """读取全部消息记录。"""
        return read_jsonl(self._messages_path(session_id))

    def read_window(self, session_id: str, n: int) -> list[dict[str, Any]]:
        """读取最近 n 条消息。"""
        return self.read_messages(session_id)[-n:]

    def save_summary(self, session_id: str, summary: dict[str, Any]) -> None:
        """覆盖写入最新摘要。"""
        write_json_atomic(self._summaries_path(session_id), summary)

    def get_summary(self, session_id: str) -> dict[str, Any] | None:
        """读取最新摘要，不存在返回 None。"""
        return read_json(self._summaries_path(session_id))

    def count_messages(self, session_id: str) -> int:
        return len(self.read_messages(session_id))

    def delete_session(self, session_id: str) -> None:
        import shutil
        d = get_settings().data_dir / "memory" / session_id
        if d.exists():
            shutil.rmtree(d)
