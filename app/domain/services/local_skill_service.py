"""本地 Skill 管理服务。"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.common.errors import AppError
from app.skills.loader import SkillLoader
from app.storage.file.skill_pull_store import SkillPullStore


class LocalSkillService:
    def __init__(self, skills_dir: Path, loader: SkillLoader, pull_store: SkillPullStore) -> None:
        self._skills_dir = skills_dir
        self._loader = loader
        self._pull_store = pull_store

    def list_skills(self) -> list[dict]:
        result = []
        for metadata, skill_dir in self._loader.scan(self._skills_dir):
            result.append({
                "skill_id": skill_dir.name,
                "name": metadata.name,
                "description": metadata.description,
                "version": metadata.version,
                "triggers": metadata.triggers,
            })
        return result

    def delete_skill(self, skill_id: str) -> None:
        if not skill_id or "/" in skill_id or "\\" in skill_id or skill_id in (".", ".."):
            raise AppError("LOCAL_SKILL_INVALID_ID", f"Invalid skill_id: '{skill_id}'")

        skill_dir = (self._skills_dir / skill_id).resolve()
        try:
            skill_dir.relative_to(self._skills_dir.resolve())
        except ValueError:
            raise AppError("LOCAL_SKILL_INVALID_ID", f"Invalid skill_id: '{skill_id}'")

        if not skill_dir.exists() or not (skill_dir / "SKILL.md").exists():
            raise AppError("LOCAL_SKILL_NOT_FOUND", f"Skill '{skill_id}' not found")

        try:
            shutil.rmtree(skill_dir)
        except Exception as e:
            raise AppError("LOCAL_SKILL_DELETE_FAILED", f"Failed to delete skill '{skill_id}': {e}")

        self._pull_store.remove_pulled_by_folder(skill_id)
