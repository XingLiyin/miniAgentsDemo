"""AgentTemplate 文件存储。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.storage.file.base import write_json_atomic, read_json, list_json_ids

_TEMPLATE_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def make_template_id(scope: str, workspace_dir: str, name: str) -> str:
    """确定性 ID：相同 (scope, workspace_dir, name) 永远得到相同 UUID。"""
    return str(uuid.uuid5(_TEMPLATE_NS, f"{scope}:{workspace_dir}:{name}"))


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

    def list_all_dicts(self) -> list[dict[str, Any]]:
        result = []
        for tid in self.list_ids():
            d = self.get(tid)
            if d:
                result.append(d)
        return result

    # ── 查询 ─────────────────────────────────────────────────────────────────

    def find_by_name(self, name: str, workspace_dir: str = "") -> dict[str, Any] | None:
        """优先级：workspace > global。"""
        global_hit = None
        for d in self.list_all_dicts():
            if d.get("name") != name:
                continue
            if d.get("scope") == "workspace" and d.get("workspace_dir") == workspace_dir:
                return d
            if d.get("scope", "global") == "global":
                global_hit = d
        return global_hit

    def list_for_workspace(self, workspace_dir: str) -> list[dict[str, Any]]:
        """global 全部 + 匹配 workspace_dir 的 workspace，同名 workspace 优先。"""
        by_name: dict[str, dict[str, Any]] = {}
        for d in self.list_all_dicts():
            name = d.get("name", "")
            scope = d.get("scope", "global")
            if scope == "global":
                by_name.setdefault(name, d)
            elif scope == "workspace" and d.get("workspace_dir") == workspace_dir:
                by_name[name] = d
        return list(by_name.values())

    def list_global(self) -> list[dict[str, Any]]:
        return [d for d in self.list_all_dicts() if d.get("scope", "global") == "global"]

    def delete_workspace(self, workspace_dir: str) -> int:
        """删除指定 workspace 的全部记录，返回删除数量。"""
        count = 0
        for d in self.list_all_dicts():
            if d.get("scope") == "workspace" and d.get("workspace_dir") == workspace_dir:
                self.delete(d["id"])
                count += 1
        return count
