"""Agent 文件存储。"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.storage.file.base import write_json_atomic, read_json, list_json_ids


class AgentStore:
    """将 Agent 序列化为 data/agents/{session_id}/{agent_id}.json。

    类级别写穿缓存：AgentStore() 在 deps.py 中被多处直接实例化（非单例），
    因此缓存必须放在类变量上，保证所有实例共享同一份内存缓存。
    """

    _cache: dict[tuple, dict] = {}           # (session_id, agent_id) → raw dict
    _locks: dict[str, threading.Lock] = {}
    _meta_lock = threading.Lock()

    def _path(self, session_id: str, agent_id: str) -> Path:
        return get_settings().data_dir / "agents" / session_id / f"{agent_id}.json"

    def _key_lock(self, session_id: str) -> threading.Lock:
        with self._meta_lock:
            if session_id not in self._locks:
                self._locks[session_id] = threading.Lock()
            return self._locks[session_id]

    def save(self, data: dict[str, Any]) -> None:
        sid, aid = data["session_id"], data["id"]
        write_json_atomic(self._path(sid, aid), data)
        with self._key_lock(sid):
            self._cache[(sid, aid)] = dict(data)

    def get(self, session_id: str, agent_id: str) -> dict[str, Any] | None:
        with self._key_lock(session_id):
            cached = self._cache.get((session_id, agent_id))
            if cached is not None:
                return dict(cached)
        result = read_json(self._path(session_id, agent_id))
        if result is not None:
            with self._key_lock(session_id):
                self._cache[(session_id, agent_id)] = result
            return dict(result)
        return None

    def list_by_session(self, session_id: str) -> list[str]:
        """O(1)：直接列出 session 子目录下的所有 agent ID。"""
        return list_json_ids(get_settings().data_dir / "agents" / session_id)

    def delete(self, session_id: str, agent_id: str) -> None:
        with self._key_lock(session_id):
            self._cache.pop((session_id, agent_id), None)
        self._path(session_id, agent_id).unlink(missing_ok=True)

    def delete_session(self, session_id: str) -> None:
        """删除 session 下所有 agent 文件（单次 rmtree）。"""
        with self._key_lock(session_id):
            for key in [k for k in self._cache if k[0] == session_id]:
                del self._cache[key]
        with self._meta_lock:
            self._locks.pop(session_id, None)
        import shutil
        d = get_settings().data_dir / "agents" / session_id
        if d.exists():
            shutil.rmtree(d)
