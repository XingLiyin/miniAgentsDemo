"""Skill 拉取状态存储。

布局：data/skill_pull_config.json
格式：{"pulled": {"<remote_id>": "<local_folder_name>", ...}}
"""

from __future__ import annotations

from pathlib import Path

from app.config.settings import get_settings
from app.storage.file.base import read_json, write_json_atomic


class SkillPullStore:
    def _path(self) -> Path:
        return get_settings().data_dir / "skill_pull_config.json"

    def _load(self) -> dict:
        return read_json(self._path()) or {"pulled": {}}

    def is_pulled(self, remote_id: str) -> bool:
        return remote_id in self._load()["pulled"]

    def record_pulled(self, remote_id: str, local_folder: str) -> None:
        data = self._load()
        data["pulled"][remote_id] = local_folder
        write_json_atomic(self._path(), data)

    def remove_pulled(self, remote_id: str) -> None:
        data = self._load()
        data["pulled"].pop(remote_id, None)
        write_json_atomic(self._path(), data)

    def remove_pulled_by_folder(self, local_folder: str) -> None:
        """删除本地目录对应的所有 pulled 记录（反查 remote_id → local_folder）。"""
        data = self._load()
        to_delete = [k for k, v in data["pulled"].items() if v == local_folder]
        if not to_delete:
            return
        for k in to_delete:
            del data["pulled"][k]
        write_json_atomic(self._path(), data)

    def get_pulled_map(self) -> dict[str, str]:
        return self._load()["pulled"]
