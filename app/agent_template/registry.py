"""AgentTemplateRegistry：内存索引 + 启动时批量 upsert。

设计：
  - global 模板（resources/agents/）：启动时扫描，写入 store，内存保留 AgentDefMetadata 索引
  - workspace 模板（{working_dir}/.agents/）：session 创建时由 sync_workspace() 写入 store，
    读取时直接从 store 获取，不再使用 TTL 缓存或实时扫描

优先级（按 name 查找时）：workspace > global
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.agent_template.definition import AgentDefContent, AgentDefMetadata, ToolSpec
from app.agent_template.loader import AgentLoader
from app.domain.models.agent_template import AgentTemplate
from app.domain.services.agent_template_service import AgentTemplateService

logger = logging.getLogger(__name__)


def _metadata_from_template(tpl: AgentTemplate) -> AgentDefMetadata:
    """从 store AgentTemplate 重建 AgentDefMetadata（用于 workspace 模板）。"""
    return AgentDefMetadata(
        name=tpl.name,
        version=tpl.version,
        description=tpl.description,
        act_tool_spec=ToolSpec(required=tpl.act_tool_list),
        observe_tool_spec=ToolSpec(required=tpl.observe_tool_list),
        agent_dir=Path(tpl.source_dir) if tpl.source_dir else Path("."),
        mcp_act_servers=tpl.mcp_act_servers,
        mcp_observe_servers=tpl.mcp_observe_servers,
    )


class AgentTemplateRegistry:
    """内存索引，存储 global AgentDefMetadata（Level 1），支持按需加载 Level 2。"""

    def __init__(self, template_service: AgentTemplateService) -> None:
        self._agents: dict[str, AgentDefMetadata] = {}       # global，key=name
        self._agents_by_id: dict[str, AgentDefMetadata] = {} # global，key=template UUID
        self._loader = AgentLoader()
        self._template_svc = template_service

    def load_from_dir(self, agents_dir: Path) -> None:
        """扫描内置目录，批量 upsert global 模板到 store 并建立内存索引。"""
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
                scope="global",
            )
            self._agents_by_id[tpl.id] = metadata

        logger.info(
            "AgentTemplateRegistry: loaded %d global agent(s) from '%s'",
            len(self._agents), agents_dir,
        )

    def sync_workspace(self, working_dir: str) -> None:
        """扫描 working_dir/.agents/ 并将结果持久化到 store。由 SessionManager 在 session 创建时调用。"""
        ws_agents_dir = Path(working_dir) / ".agents"
        meta_list = self._loader.scan(ws_agents_dir)
        self._template_svc.sync_workspace(working_dir, meta_list)

    # ── 查询 ─────────────────────────────────────────────────────────────────

    def get_metadata(self, name: str, workspace_dir: str = "") -> AgentDefMetadata | None:
        """优先级：workspace > global。global 模板返回内存索引（含 subagents 等完整字段）。"""
        if workspace_dir:
            tpl = self._template_svc.get_by_name_for_workspace(name, workspace_dir)
            if tpl is not None:
                if tpl.scope == "global" and tpl.name in self._agents:
                    return self._agents[tpl.name]
                return _metadata_from_template(tpl)
        # 无 workspace_dir，或 workspace 中未找到：查 global 内存索引
        return self._agents.get(name)

    def get_metadata_by_id(self, template_id: str) -> AgentDefMetadata | None:
        """按 store UUID 查找，仅覆盖 global（workspace 模板有真实 UUID，但不在内存索引里）。"""
        if template_id in self._agents_by_id:
            return self._agents_by_id[template_id]
        # 兜底：从 store 重建（workspace 模板按 ID 查）
        try:
            tpl = self._template_svc.get(template_id)
            return _metadata_from_template(tpl)
        except Exception:
            return None

    def list_all(self, workspace_dir: str = "") -> list[AgentDefMetadata]:
        """返回可见模板列表（workspace > global，同名去重）。数据来自 store。"""
        if workspace_dir:
            templates = self._template_svc.list_for_workspace(workspace_dir)
        else:
            templates = self._template_svc.list_global()

        result: list[AgentDefMetadata] = []
        for tpl in templates:
            if tpl.scope == "global" and tpl.name in self._agents:
                result.append(self._agents[tpl.name])
            else:
                result.append(_metadata_from_template(tpl))
        return result

    def load_content(self, name: str, workspace_dir: str = "") -> AgentDefContent | None:
        """Level 2：从 source_dir 读取四个文件正文。优先级：workspace > global。"""
        tpl = self._template_svc.get_by_name_for_workspace(name, workspace_dir)
        if tpl is None:
            logger.warning("AgentTemplateRegistry: agent '%s' not found", name)
            return None
        return self._load_from_source(tpl.source_dir, name)

    def load_content_by_id(self, template_id: str) -> AgentDefContent | None:
        """Level 2：按 UUID 直接从 source_dir 读取四个文件正文（无需名字/workspace 优先级判断）。"""
        try:
            tpl = self._template_svc.get(template_id)
        except Exception:
            logger.warning("AgentTemplateRegistry: template id '%s' not found", template_id)
            return None
        return self._load_from_source(tpl.source_dir, template_id)

    def _load_from_source(self, source_dir: str, label: str) -> AgentDefContent | None:
        if not source_dir:
            logger.warning("AgentTemplateRegistry: '%s' has no source_dir", label)
            return None
        try:
            return self._loader.load_content(Path(source_dir))
        except Exception as e:
            logger.warning("AgentTemplateRegistry: failed to load content for '%s': %s", label, e)
            return None
