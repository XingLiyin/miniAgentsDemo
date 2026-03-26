"""ToolCall 审计日志存储（只追加写）。

布局：data/tool_calls/{session_id}.jsonl
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.storage.file.base import append_jsonl, read_jsonl


class ToolCallStore:
    """工具调用审计日志（只追加写）。"""

    def _path(self, session_id: str) -> Path:
        return get_settings().data_dir / "tool_calls" / f"{session_id}.jsonl"

    def append(self, session_id: str, record: dict[str, Any]) -> None:
        """追加一条工具调用审计记录。"""
        append_jsonl(self._path(session_id), record)

    def read_all(self, session_id: str) -> list[dict[str, Any]]:
        """读取 session 全部工具调用记录。"""
        return read_jsonl(self._path(session_id))
