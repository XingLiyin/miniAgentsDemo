"""LLM Provider 配置文件存储。

布局：data/llm_configs/{name}.json
注意：api_key 以明文存储，仅适合本地开发环境。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.storage.file.base import write_json_atomic, read_json, list_json_ids


class LLMConfigStore:
    """将 LLMProviderConfig 序列化为 data/llm_configs/{name}.json。"""

    def _path(self, name: str) -> Path:
        # 文件名用 name，需替换不安全字符
        safe_name = name.replace("/", "_").replace("\\", "_")
        return get_settings().data_dir / "llm_configs" / f"{safe_name}.json"

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
        """读取全部已保存的 LLM 配置。"""
        ids = list_json_ids(get_settings().data_dir / "llm_configs")
        result = []
        for name in ids:
            data = self.get(name)
            if data:
                result.append(data)
        return result
