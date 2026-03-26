"""Agent 文件存储。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.storage.file.base import write_json_atomic, read_json, list_json_ids


class AgentStore:
    """将 Agent 序列化为 data/agents/{id}.json。"""

    def _path(self, agent_id: str) -> Path:
        return get_settings().data_dir / "agents" / f"{agent_id}.json"

    def save(self, data: dict[str, Any]) -> None:
        write_json_atomic(self._path(data["id"]), data)

    def get(self, agent_id: str) -> dict[str, Any] | None:
        return read_json(self._path(agent_id))

    def list_ids(self) -> list[str]:
        return list_json_ids(get_settings().data_dir / "agents")
