"""Task 文件存储。"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.storage.file.base import write_json_atomic, read_json, list_json_ids


class TaskStore:
    """将 Task 序列化为 data/tasks/{session_id}/{task_id}.json。

    类级别写穿缓存：list_by_session() 首次调用后结果缓存在内存，避免
    Observer / TaskManager 每次调用都触发 N 次 read_json。

    save/get/delete 在知道 session_id 时走 O(1) 直接路径；
    get/delete 在不知道 session_id 时回退到跨目录 glob（仅用于 API 路由等边缘场景）。
    """

    _cache: dict[tuple, dict] = {}           # (session_id, task_id) → raw dict
    _session_index: dict[str, set] = {}      # session_id → {task_id, ...}
    _locks: dict[str, threading.Lock] = {}
    _meta_lock = threading.Lock()

    def _path(self, session_id: str, task_id: str) -> Path:
        return get_settings().data_dir / "tasks" / session_id / f"{task_id}.json"

    def _key_lock(self, session_id: str) -> threading.Lock:
        with self._meta_lock:
            if session_id not in self._locks:
                self._locks[session_id] = threading.Lock()
            return self._locks[session_id]

    def save(self, data: dict[str, Any]) -> None:
        sid, tid = data["session_id"], data["id"]
        write_json_atomic(self._path(sid, tid), data)
        with self._key_lock(sid):
            self._cache[(sid, tid)] = dict(data)
            self._session_index.setdefault(sid, set()).add(tid)

    def get(self, task_id: str, session_id: str | None = None) -> dict[str, Any] | None:
        if session_id:
            with self._key_lock(session_id):
                cached = self._cache.get((session_id, task_id))
                if cached is not None:
                    return dict(cached)
            result = read_json(self._path(session_id, task_id))
            if result is not None:
                with self._key_lock(session_id):
                    self._cache[(session_id, task_id)] = result
                    self._session_index.setdefault(session_id, set()).add(task_id)
                return dict(result)
            return None
        # 没有 session_id 时跨目录扫描（O(num_sessions)，仅 API 路由使用）
        base = get_settings().data_dir / "tasks"
        if not base.exists():
            return None
        for path in base.glob(f"*/{task_id}.json"):
            return read_json(path)
        return None

    def list_by_session(self, session_id: str) -> list[str]:
        """O(1)：直接列出 session 子目录下的所有 task ID。"""
        with self._key_lock(session_id):
            if session_id in self._session_index:
                return list(self._session_index[session_id])
        ids = list_json_ids(get_settings().data_dir / "tasks" / session_id)
        with self._key_lock(session_id):
            self._session_index[session_id] = set(ids)
        return ids

    def delete(self, task_id: str, session_id: str | None = None) -> None:
        if session_id:
            with self._key_lock(session_id):
                self._cache.pop((session_id, task_id), None)
                if session_id in self._session_index:
                    self._session_index[session_id].discard(task_id)
            self._path(session_id, task_id).unlink(missing_ok=True)
            return
        base = get_settings().data_dir / "tasks"
        if not base.exists():
            return
        for path in base.glob(f"*/{task_id}.json"):
            path.unlink(missing_ok=True)
            return

    def delete_session(self, session_id: str) -> None:
        """删除 session 下所有 task 文件（单次 rmtree，O(1) 调用开销）。"""
        with self._key_lock(session_id):
            for key in [k for k in self._cache if k[0] == session_id]:
                del self._cache[key]
            self._session_index.pop(session_id, None)
        with self._meta_lock:
            self._locks.pop(session_id, None)
        import shutil
        d = get_settings().data_dir / "tasks" / session_id
        if d.exists():
            shutil.rmtree(d)
