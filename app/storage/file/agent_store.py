"""Agent 文件存储。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.storage.file.base import write_json_atomic, read_json, list_json_ids


class AgentStore:
    """将 Agent 序列化为 data/agents/{session_id}/{agent_id}.json。"""

    def _path(self, session_id: str, agent_id: str) -> Path:
        return get_settings().data_dir / "agents" / session_id / f"{agent_id}.json"

    def save(self, data: dict[str, Any]) -> None:
        write_json_atomic(self._path(data["session_id"], data["id"]), data)

    def get(self, session_id: str, agent_id: str) -> dict[str, Any] | None:
        return read_json(self._path(session_id, agent_id))

    def list_by_session(self, session_id: str) -> list[str]:
        """O(1)：直接列出 session 子目录下的所有 agent ID。"""
        return list_json_ids(get_settings().data_dir / "agents" / session_id)

    def delete(self, session_id: str, agent_id: str) -> None:
        self._path(session_id, agent_id).unlink(missing_ok=True)

    def delete_session(self, session_id: str) -> None:
        """删除 session 下所有 agent 文件（单次 rmtree）。"""
        import shutil
        d = get_settings().data_dir / "agents" / session_id
        if d.exists():
            shutil.rmtree(d)
