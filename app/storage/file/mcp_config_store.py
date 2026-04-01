"""MCP Server 配置文件存储。

布局：data/mcp_configs/{name}.json
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.storage.file.base import list_json_ids, read_json, write_json_atomic


class MCPConfigStore:
    """将 MCP Server 配置序列化为 data/mcp_configs/{name}.json。"""

    def _path(self, name: str) -> Path:
        safe_name = name.replace("/", "_").replace("\\", "_")
        return get_settings().data_dir / "mcp_configs" / f"{safe_name}.json"

    def save(self, data: dict[str, Any]) -> None:
        write_json_atomic(self._path(data["name"]), data)

    def get(self, name: str) -> dict[str, Any] | None:
        return read_json(self._path(name))

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_all(self) -> list[dict[str, Any]]:
        ids = list_json_ids(get_settings().data_dir / "mcp_configs")
        result = []
        for name in ids:
            data = self.get(name)
            if data:
                result.append(data)
        return result
