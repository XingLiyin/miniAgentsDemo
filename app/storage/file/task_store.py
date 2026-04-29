"""Task 文件存储。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.storage.file.base import write_json_atomic, read_json, list_json_ids


class TaskStore:
    """将 Task 序列化为 data/tasks/{session_id}/{task_id}.json。

    save/get/delete 在知道 session_id 时走 O(1) 直接路径；
    get/delete 在不知道 session_id 时回退到跨目录 glob（仅用于 API 路由等边缘场景）。
    """

    def _path(self, session_id: str, task_id: str) -> Path:
        return get_settings().data_dir / "tasks" / session_id / f"{task_id}.json"

    def save(self, data: dict[str, Any]) -> None:
        write_json_atomic(self._path(data["session_id"], data["id"]), data)

    def get(self, task_id: str, session_id: str | None = None) -> dict[str, Any] | None:
        if session_id:
            return read_json(self._path(session_id, task_id))
        # 没有 session_id 时跨目录扫描（O(num_sessions)，仅 API 路由使用）
        base = get_settings().data_dir / "tasks"
        if not base.exists():
            return None
        for path in base.glob(f"*/{task_id}.json"):
            return read_json(path)
        return None

    def list_by_session(self, session_id: str) -> list[str]:
        """O(1)：直接列出 session 子目录下的所有 task ID。"""
        return list_json_ids(get_settings().data_dir / "tasks" / session_id)

    def delete(self, task_id: str, session_id: str | None = None) -> None:
        if session_id:
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
        import shutil
        d = get_settings().data_dir / "tasks" / session_id
        if d.exists():
            shutil.rmtree(d)
