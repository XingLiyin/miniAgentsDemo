"""Blackboard 文件存储。

布局：data/blackboard/{session_id}/{topic}.jsonl（追加写）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.storage.file.base import append_jsonl, read_jsonl


class BlackboardStore:
    """黑板 topic 文件存储。"""

    def _path(self, session_id: str, topic: str) -> Path:
        return get_settings().data_dir / "blackboard" / session_id / f"{topic}.jsonl"

    def append(self, session_id: str, topic: str, entry: dict[str, Any]) -> None:
        """追加一条黑板条目。"""
        append_jsonl(self._path(session_id, topic), entry)

    def read_all(self, session_id: str, topic: str) -> list[dict[str, Any]]:
        """读取 topic 全部条目。"""
        return read_jsonl(self._path(session_id, topic))

    def read_since(self, session_id: str, topic: str, cursor: int) -> list[dict[str, Any]]:
        """读取 cursor 之后的增量条目（cursor 为已读条目数）。"""
        return self.read_all(session_id, topic)[cursor:]

    def count(self, session_id: str, topic: str) -> int:
        return len(self.read_all(session_id, topic))

    def list_topics(self, session_id: str) -> list[str]:
        """列出 session 下所有 topic 名。"""
        d = get_settings().data_dir / "blackboard" / session_id
        if not d.exists():
            return []
        return [p.stem for p in d.glob("*.jsonl")]
