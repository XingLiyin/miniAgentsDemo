"""远端 Skill 来源配置的文件存储。

布局：data/remote_skill_sources/{source_name}.json
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.storage.file.base import list_json_ids, read_json, write_json_atomic


class RemoteSkillSourceStore:
    """将 RemoteSkillSourceConfig 序列化为 data/remote_skill_sources/{name}.json。"""

    def _path(self, source_name: str) -> Path:
        safe = source_name.replace("/", "_").replace("\\", "_")
        return get_settings().data_dir / "remote_skill_sources" / f"{safe}.json"

    def save(self, data: dict[str, Any]) -> None:
        write_json_atomic(self._path(data["source_name"]), data)

    def get(self, source_name: str) -> dict[str, Any] | None:
        return read_json(self._path(source_name))

    def delete(self, source_name: str) -> bool:
        path = self._path(source_name)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_all(self) -> list[dict[str, Any]]:
        ids = list_json_ids(get_settings().data_dir / "remote_skill_sources")
        result = []
        for name in ids:
            data = self.get(name)
            if data:
                result.append(data)
        return result
