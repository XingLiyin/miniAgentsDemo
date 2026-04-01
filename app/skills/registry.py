"""SkillRegistry：内存索引 + Level 1 缓存。"""

from __future__ import annotations

import logging
from pathlib import Path

from app.skills.definition import SkillDefinition, SkillMetadata
from app.skills.loader import SkillLoader

logger = logging.getLogger(__name__)


class SkillRegistry:
    """内存索引，存储所有 SkillMetadata（Level 1），支持按需加载 Level 2。"""

    def __init__(self) -> None:
        self._skills: dict[str, SkillMetadata] = {}
        self._loader = SkillLoader()

    def register(self, metadata: SkillMetadata) -> None:
        """注册单个 SkillMetadata（重复注册会覆盖）。"""
        self._skills[metadata.name] = metadata
        logger.debug("SkillRegistry: registered skill '%s'", metadata.name)
        self._sync_added([metadata])

    def unregister(self, name: str) -> None:
        """注销指定 Skill 并同步到外部 DB。"""
        if name in self._skills:
            del self._skills[name]
            self._sync_removed([name])
            logger.debug("SkillRegistry: unregistered skill '%s'", name)

    def load_from_dir(self, skills_dir: Path) -> None:
        """扫描目录，批量注册所有发现的 Skill（一次性批量同步到外部 DB）。"""
        newly_registered: list[SkillMetadata] = []
        for metadata in self._loader.scan(skills_dir):
            self._skills[metadata.name] = metadata
            newly_registered.append(metadata)
            logger.debug("SkillRegistry: registered skill '%s'", metadata.name)
        if newly_registered:
            self._sync_added(newly_registered)
        logger.info(
            "SkillRegistry: loaded %d skill(s) from '%s'",
            len(self._skills), skills_dir,
        )

    def get_metadata(self, name: str) -> SkillMetadata | None:
        return self._skills.get(name)

    def list_all(self) -> list[SkillMetadata]:
        return list(self._skills.values())

    def get_metadata_block(self) -> str:
        """生成 Available Skills 文本块（Level 1 内容）。

        格式：
          ## Available Skills (assign to tasks where appropriate)
          - code-review: 审查代码质量...
        """
        if not self._skills:
            return ""
        lines = ["## Available Skills (assign to tasks where appropriate)"]
        for m in self._skills.values():
            lines.append(f"- {m.name}: {m.description}")
        return "\n".join(lines)

    # ── 内部同步 helpers ──────────────────────────────────────────────────

    def _sync_added(self, metadatas: list[SkillMetadata]) -> None:
        try:
            from app.skills.skill_sync import get_skill_sync_service
            get_skill_sync_service().on_skills_added(metadatas)
        except Exception:
            logger.warning("SkillRegistry: sync-added failed", exc_info=True)

    def _sync_removed(self, names: list[str]) -> None:
        try:
            from app.skills.skill_sync import get_skill_sync_service
            get_skill_sync_service().on_skills_removed(names)
        except Exception:
            logger.warning("SkillRegistry: sync-removed failed for %s", names, exc_info=True)

    def load_definition(self, name: str) -> SkillDefinition | None:
        """加载 Level 2 内容（SKILL.md 主体）。"""
        meta = self._skills.get(name)
        if meta is None:
            logger.warning("SkillRegistry: skill '%s' not found", name)
            return None
        try:
            instructions = self._loader.load_instructions(meta.skill_dir)
            return SkillDefinition(metadata=meta, instructions=instructions)
        except Exception as e:
            logger.warning("SkillRegistry: failed to load instructions for '%s': %s", name, e)
            return None


# ── 全局单例 ──────────────────────────────────────────────────────────────

_registry: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    """获取全局 SkillRegistry（首次调用时从 settings.skills_dir 扫描）。"""
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
        from app.config.settings import get_settings
        _registry.load_from_dir(get_settings().skills_dir)
    return _registry
