"""AgentTemplate 领域服务（Phase 1）。"""

from __future__ import annotations

import logging

from app.common.errors import AppError
from app.common.utils import new_template_id, now_iso
from app.domain.models.agent_template import AgentTemplate
from app.storage.file.agent_template_store import AgentTemplateStore

logger = logging.getLogger(__name__)


class AgentTemplateService:
    """AgentTemplate CRUD。"""

    def __init__(self, store: AgentTemplateStore) -> None:
        self._store = store

    def create(
        self,
        name: str,
        system_prompt: str = "",
        act_tool_list: list[str] | None = None,
        observe_tool_list: list[str] | None = None,
        description: str = "",
        source_dir: str = "",
        summary_threshold: int = 20,
        short_window_size: int = 20,
    ) -> AgentTemplate:
        now = now_iso()
        tpl = AgentTemplate(
            id=new_template_id(),
            name=name,
            system_prompt=system_prompt,
            act_tool_list=act_tool_list or [],
            observe_tool_list=observe_tool_list or [],
            description=description,
            source_dir=source_dir,
            summary_threshold=summary_threshold,
            short_window_size=short_window_size,
            created_at=now,
            updated_at=now,
        )
        self._store.save(tpl.to_dict())
        return tpl

    def get(self, template_id: str) -> AgentTemplate:
        data = self._store.get(template_id)
        if data is None:
            raise AppError("TEMPLATE_NOT_FOUND", f"AgentTemplate {template_id} not found")
        return AgentTemplate.from_dict(data)

    def list_all(self) -> list[AgentTemplate]:
        results = []
        for tid in self._store.list_ids():
            data = self._store.get(tid)
            if data:
                results.append(AgentTemplate.from_dict(data))
        return results

    def delete(self, template_id: str) -> None:
        if not self._store.delete(template_id):
            raise AppError("TEMPLATE_NOT_FOUND", f"AgentTemplate {template_id} not found")

    def upsert_by_name(
        self,
        name: str,
        version: str,
        description: str,
        soul_md: str,
        role_md: str,
        tools_md: str,
        style_md: str,
        act_tool_list: list[str],
        observe_tool_list: list[str],
        source_dir: str = "",
    ) -> AgentTemplate:
        """按 name 做 upsert（目录扫描时调用）。

        name 已存在则更新各 md 字段；不存在则新建并分配 template_id。
        """
        existing = self._find_by_name(name)
        now = now_iso()

        if existing is not None:
            existing.version = version
            existing.description = description
            existing.soul_md = soul_md
            existing.role_md = role_md
            existing.tools_md = tools_md
            existing.style_md = style_md
            existing.act_tool_list = act_tool_list
            existing.observe_tool_list = observe_tool_list
            existing.source_dir = source_dir
            existing.updated_at = now
            self._store.save(existing.to_dict())
            logger.debug("AgentTemplateService: upserted (updated) template '%s'", name)
            return existing

        tpl = AgentTemplate(
            id=new_template_id(),
            name=name,
            version=version,
            description=description,
            soul_md=soul_md,
            role_md=role_md,
            tools_md=tools_md,
            style_md=style_md,
            act_tool_list=act_tool_list,
            observe_tool_list=observe_tool_list,
            source_dir=source_dir,
            created_at=now,
            updated_at=now,
        )
        self._store.save(tpl.to_dict())
        logger.debug("AgentTemplateService: upserted (created) template '%s'", name)
        return tpl

    def get_or_prepare(self, template_id: str, llm_client=None) -> AgentTemplate:
        """工具列表已在目录扫描时从 frontmatter 提取，直接返回模板。

        保留此方法签名以兼容调用方，llm_client 参数不再使用。
        """
        return self.get(template_id)

    def get_by_name(self, name: str) -> "AgentTemplate | None":
        """按 name 查找模板，未找到返回 None（不抛异常）。"""
        return self._find_by_name(name)

    # ── 内部工具 ──────────────────────────────────────────────────────────

    def _find_by_name(self, name: str) -> AgentTemplate | None:
        for tid in self._store.list_ids():
            data = self._store.get(tid)
            if data and data.get("name") == name:
                return AgentTemplate.from_dict(data)
        return None

