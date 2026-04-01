"""Skill 同步服务：将 SkillRegistry 事件推送到外部 Skill 存储。

SkillRegistry 在注册/注销 skill 时调用此服务；
外部向量 DB 负责 embedding 生成与语义检索。
"""

from __future__ import annotations

import logging

from app.skills.definition import SkillMetadata
from app.skills.skill_store_client import get_skill_store_client

logger = logging.getLogger(__name__)


class SkillSyncService:
    """桥接 SkillRegistry 事件 → SkillStoreClient。"""

    def on_skills_added(self, metadatas: list[SkillMetadata]) -> None:
        """Skill 注册后调用；推送到外部 DB。"""
        if not metadatas:
            return
        records = [
            {
                "name": m.name,
                "description": m.description,
                "triggers": m.triggers,
                "version": m.version,
            }
            for m in metadatas
        ]
        get_skill_store_client().upsert(records)

    def on_skills_removed(self, names: list[str]) -> None:
        """Skill 注销后调用；从外部 DB 删除。"""
        if not names:
            return
        get_skill_store_client().delete(names)


# ── 全局单例 ──────────────────────────────────────────────────────────────

_sync_service: SkillSyncService | None = None


def get_skill_sync_service() -> SkillSyncService:
    global _sync_service
    if _sync_service is None:
        _sync_service = SkillSyncService()
    return _sync_service
