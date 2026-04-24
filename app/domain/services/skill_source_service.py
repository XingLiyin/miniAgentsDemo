"""RemoteSkillSourceService：远端 skill 来源的注册与生命周期管理。"""

from __future__ import annotations

import logging

from app.common.errors import AppError
from app.skills.definition import RemoteSkillSourceConfig
from app.skills.registry import SkillRegistry
from app.storage.file.remote_skill_source_store import RemoteSkillSourceStore

logger = logging.getLogger(__name__)


class RemoteSkillSourceService:
    """远端 skill 来源的 CRUD + 启动恢复。"""

    def __init__(self, registry: SkillRegistry, store: RemoteSkillSourceStore) -> None:
        self._registry = registry
        self._store = store

    def register(self, config: RemoteSkillSourceConfig) -> dict:
        """注册远端 skill 来源，持久化配置，返回存储的配置 dict。"""
        if self._store.get(config.source_name) is not None:
            raise AppError(
                "SKILL_SOURCE_ALREADY_EXISTS",
                f"Remote skill source '{config.source_name}' already registered",
            )
        self._registry.register_remote_source(config)
        data = _config_to_dict(config)
        self._store.save(data)
        logger.info("RemoteSkillSourceService: registered '%s'", config.source_name)
        return data

    def delete(self, source_name: str) -> None:
        """注销远端 skill 来源，断开连接，删除持久化配置。"""
        if self._store.get(source_name) is None:
            raise AppError(
                "SKILL_SOURCE_NOT_FOUND",
                f"Remote skill source '{source_name}' not found",
            )
        self._registry.unregister_remote_source(source_name)
        self._store.delete(source_name)

    def get(self, source_name: str) -> dict:
        data = self._store.get(source_name)
        if data is None:
            raise AppError(
                "SKILL_SOURCE_NOT_FOUND",
                f"Remote skill source '{source_name}' not found",
            )
        return data

    def list_all(self) -> list[dict]:
        return self._store.list_all()

    def restore_all(self) -> None:
        """应用启动时从持久化配置恢复所有远端 skill 来源。"""
        configs = self._store.list_all()
        if not configs:
            return
        logger.info(
            "RemoteSkillSourceService.restore_all: restoring %d source(s)", len(configs)
        )
        for data in configs:
            source_name = data.get("source_name", "<unknown>")
            try:
                config = _dict_to_config(data)
                self._registry.register_remote_source(config)
                logger.info(
                    "RemoteSkillSourceService.restore_all: restored '%s'", source_name
                )
            except Exception:
                logger.warning(
                    "RemoteSkillSourceService.restore_all: failed to restore '%s', skipping",
                    source_name, exc_info=True,
                )


# ── 全局单例 ──────────────────────────────────────────────────────────────────

_service: RemoteSkillSourceService | None = None


def get_remote_skill_source_service() -> RemoteSkillSourceService:
    global _service
    if _service is None:
        from app.skills.registry import get_skill_registry
        _service = RemoteSkillSourceService(
            registry=get_skill_registry(),
            store=RemoteSkillSourceStore(),
        )
    return _service


# ── 序列化工具 ────────────────────────────────────────────────────────────────

def _config_to_dict(config: RemoteSkillSourceConfig) -> dict:
    return {
        "source_name": config.source_name,
        "mcp_type": config.mcp_type,
        "mcp_url": config.mcp_url,
        "mcp_timeout": config.mcp_timeout,
        "mcp_command": config.mcp_command,
        "mcp_args": config.mcp_args,
        "mcp_env": config.mcp_env,
        "mcp_tool_list_skills": config.mcp_tool_list_skills,
        "mcp_tool_load_skill_md": config.mcp_tool_load_skill_md,
        "mcp_tool_get_skill_files": config.mcp_tool_get_skill_files,
        "mcp_tool_load_skill_reference": config.mcp_tool_load_skill_reference,
        "mcp_tool_exec_skill_script": config.mcp_tool_exec_skill_script,
    }


def _dict_to_config(data: dict) -> RemoteSkillSourceConfig:
    return RemoteSkillSourceConfig(
        source_name=data["source_name"],
        mcp_type=data["mcp_type"],
        mcp_url=data.get("mcp_url"),
        mcp_timeout=data.get("mcp_timeout", 30),
        mcp_command=data.get("mcp_command"),
        mcp_args=data.get("mcp_args") or [],
        mcp_env=data.get("mcp_env") or {},
        mcp_tool_list_skills=data.get("mcp_tool_list_skills", "listSkills"),
        mcp_tool_load_skill_md=data.get("mcp_tool_load_skill_md", "loadSkillMd"),
        mcp_tool_get_skill_files=data.get("mcp_tool_get_skill_files", "getSkillFiles"),
        mcp_tool_load_skill_reference=data.get("mcp_tool_load_skill_reference", "loadSkillReference"),
        mcp_tool_exec_skill_script=data.get("mcp_tool_exec_skill_script", "execSkillScript"),
    )
