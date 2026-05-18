"""Session 文件存储。"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.storage.file.base import write_json_atomic, read_json, list_json_ids


class SessionStore:
    """将 Session 序列化为 data/sessions/{id}.json。

    类级别写穿缓存：session_svc.add_tokens() 在每个 LLM round 触发一次磁盘
    read+write，缓存后变为纯内存读，只保留磁盘写。
    """

    _cache: dict[str, dict] = {}
    _locks: dict[str, threading.Lock] = {}
    _meta_lock = threading.Lock()

    def _path(self, session_id: str) -> Path:
        return get_settings().data_dir / "sessions" / f"{session_id}.json"

    def _key_lock(self, session_id: str) -> threading.Lock:
        with self._meta_lock:
            if session_id not in self._locks:
                self._locks[session_id] = threading.Lock()
            return self._locks[session_id]

    def save(self, data: dict[str, Any]) -> None:
        write_json_atomic(self._path(data["id"]), data)
        with self._key_lock(data["id"]):
            self._cache[data["id"]] = dict(data)

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._key_lock(session_id):
            if session_id in self._cache:
                return dict(self._cache[session_id])
        result = read_json(self._path(session_id))
        if result is not None:
            with self._key_lock(session_id):
                self._cache[session_id] = result
            return dict(result)
        return None

    def list_ids(self) -> list[str]:
        return list_json_ids(get_settings().data_dir / "sessions")

    def delete(self, session_id: str) -> None:
        with self._key_lock(session_id):
            self._cache.pop(session_id, None)
        with self._meta_lock:
            self._locks.pop(session_id, None)
        self._path(session_id).unlink(missing_ok=True)
