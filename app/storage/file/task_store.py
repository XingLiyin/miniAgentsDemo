"""Task 文件存储。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.storage.file.base import write_json_atomic, read_json, list_json_ids


class TaskStore:
    """将 Task 序列化为 data/tasks/{id}.json。"""

    def _path(self, task_id: str) -> Path:
        return get_settings().data_dir / "tasks" / f"{task_id}.json"

    def save(self, data: dict[str, Any]) -> None:
        write_json_atomic(self._path(data["id"]), data)

    def get(self, task_id: str) -> dict[str, Any] | None:
        return read_json(self._path(task_id))

    def list_ids(self) -> list[str]:
        return list_json_ids(get_settings().data_dir / "tasks")
