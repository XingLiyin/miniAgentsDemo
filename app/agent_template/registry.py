"""AgentTemplateRegistry：内存索引 + 启动时批量 upsert。

类比 app/skills/registry.py 的 SkillRegistry。
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.agent_template.definition import AgentDefContent, AgentDefMetadata
from app.agent_template.loader import AgentLoader
from app.domain.services.agent_template_service import AgentTemplateService

logger = logging.getLogger(__name__)


class AgentTemplateRegistry:
    """内存索引，存储所有 AgentDefMetadata（Level 1），支持按需加载 Level 2。"""

    def __init__(self, template_service: AgentTemplateService) -> None:
        self._agents: dict[str, AgentDefMetadata] = {}
        self._agents_by_id: dict[str, AgentDefMetadata] = {}
        self._loader = AgentLoader()
        self._template_svc = template_service

    def load_from_dir(self, agents_dir: Path) -> None:
        """扫描目录，批量 upsert AgentTemplate（类比 SkillRegistry.load_from_dir）。"""
        for metadata in self._loader.scan(agents_dir):
            self._agents[metadata.name] = metadata
            tpl = self._template_svc.upsert_by_name(
                name=metadata.name,
                version=metadata.version,
                description=metadata.description,
                act_tool_list=metadata.act_tool_spec.effective(),
                observe_tool_list=metadata.observe_tool_spec.effective(),
                mcp_act_servers=metadata.mcp_act_servers,
                mcp_observe_servers=metadata.mcp_observe_servers,
                source_dir=str(metadata.agent_dir),
            )
            self._agents_by_id[tpl.id] = metadata
            logger.debug("AgentTemplateRegistry: upserted template '%s'", metadata.name)

        logger.info(
            "AgentTemplateRegistry: loaded %d agent(s) from '%s'",
            len(self._agents), agents_dir,
        )

    def get_metadata(self, name: str) -> AgentDefMetadata | None:
        return self._agents.get(name)

    def get_metadata_by_id(self, template_id: str) -> AgentDefMetadata | None:
        return self._agents_by_id.get(template_id)

    def list_all(self) -> list[AgentDefMetadata]:
        return list(self._agents.values())

    def load_content(self, name: str) -> AgentDefContent | None:
        """Level 2：读取四个文件正文（类比 SkillRegistry.load_definition）。"""
        meta = self._agents.get(name)
        if meta is None:
            logger.warning("AgentTemplateRegistry: agent '%s' not found", name)
            return None
        try:
            return self._loader.load_content(meta.agent_dir)
        except Exception as e:
            logger.warning(
                "AgentTemplateRegistry: failed to load content for '%s': %s", name, e
            )
            return None


# ── 全局单例 ──────────────────────────────────────────────────────────────

_registry: AgentTemplateRegistry | None = None


def get_agent_template_registry() -> AgentTemplateRegistry:
    """获取全局 AgentTemplateRegistry（首次调用时从 settings.agents_dir 扫描）。"""
    global _registry
    if _registry is None:
        from app.api.v1.deps import get_agent_template_service
        from app.config.settings import get_settings
        _registry = AgentTemplateRegistry(
            template_service=get_agent_template_service()
        )
        _registry.load_from_dir(get_settings().agents_dir)
    return _registry
