"""Session 文件存储。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.storage.file.base import write_json_atomic, read_json, list_json_ids


class SessionStore:
    """将 Session 序列化为 data/sessions/{id}.json。"""

    def _path(self, session_id: str) -> Path:
        return get_settings().data_dir / "sessions" / f"{session_id}.json"

    def save(self, data: dict[str, Any]) -> None:
        write_json_atomic(self._path(data["id"]), data)

    def get(self, session_id: str) -> dict[str, Any] | None:
        return read_json(self._path(session_id))

    def list_ids(self) -> list[str]:
        return list_json_ids(get_settings().data_dir / "sessions")

    def delete(self, session_id: str) -> None:
        self._path(session_id).unlink(missing_ok=True)
