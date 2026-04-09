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

    def list_by_session(self, session_id: str) -> list[str]:
        """返回属于该 session 的所有 agent_id 列表。"""
        result = []
        for aid in self.list_ids():
            data = self.get(aid)
            if data and data.get("session_id") == session_id:
                result.append(aid)
        return result

    def delete(self, agent_id: str) -> None:
        self._path(agent_id).unlink(missing_ok=True)
