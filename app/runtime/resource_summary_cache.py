"""资源描述总结的持久化缓存。

按 sha256(name + "\\x00" + raw) 为键存储 summary：描述一旦变化（MCP 升级、
skill 文档修改）键即失效，自动触发重算。内存 + 落盘双层，跨进程复用。
"""

from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path

from app.storage.file.base import read_json, write_json_atomic

logger = logging.getLogger(__name__)


class DescriptionSummaryCache:
    """线程安全的描述总结缓存，落盘为单个 JSON 文件。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, str] = self._load()

    @staticmethod
    def key(name: str, raw: str) -> str:
        return hashlib.sha256(f"{name}\x00{raw}".encode("utf-8")).hexdigest()

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._data.get(key)

    def put(self, key: str, summary: str) -> None:
        with self._lock:
            self._data[key] = summary
            snapshot = dict(self._data)
        try:
            write_json_atomic(self._path, snapshot)
        except Exception:
            logger.warning("DescriptionSummaryCache: failed to persist to %s", self._path, exc_info=True)

    def _load(self) -> dict[str, str]:
        try:
            data = read_json(self._path)
        except Exception:
            logger.warning("DescriptionSummaryCache: failed to read %s, starting empty", self._path, exc_info=True)
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
