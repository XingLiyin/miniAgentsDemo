"""AgentTemplate 文件存储。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.storage.file.base import write_json_atomic, read_json, list_json_ids


class AgentTemplateStore:
    """将 AgentTemplate 序列化为 data/agent_templates/{id}.json。"""

    def _path(self, template_id: str) -> Path:
        return get_settings().data_dir / "agent_templates" / f"{template_id}.json"

    def save(self, data: dict[str, Any]) -> None:
        write_json_atomic(self._path(data["id"]), data)

    def get(self, template_id: str) -> dict[str, Any] | None:
        return read_json(self._path(template_id))

    def delete(self, template_id: str) -> bool:
        path = self._path(template_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_ids(self) -> list[str]:
        return list_json_ids(get_settings().data_dir / "agent_templates")
