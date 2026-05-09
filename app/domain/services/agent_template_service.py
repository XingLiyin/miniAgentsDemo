"""AgentTemplate 领域服务。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.common.errors import AppError
from app.common.utils import new_template_id, now_iso
from app.domain.models.agent_template import AgentTemplate
from app.storage.file.agent_template_store import AgentTemplateStore

if TYPE_CHECKING:
    from app.agent_template.definition import AgentDefMetadata

logger = logging.getLogger(__name__)


class AgentTemplateService:
    """AgentTemplate CRUD + workspace 同步。"""

    def __init__(self, store: AgentTemplateStore) -> None:
        self._store = store

    # ── 基础 CRUD ────────────────────────────────────────────────────────────

    def get(self, template_id: str) -> AgentTemplate:
        data = self._store.get(template_id)
        if data is None:
            raise AppError("TEMPLATE_NOT_FOUND", f"AgentTemplate {template_id} not found")
        return AgentTemplate.from_dict(data)

    def delete(self, template_id: str) -> None:
        if not self._store.delete(template_id):
            raise AppError("TEMPLATE_NOT_FOUND", f"AgentTemplate {template_id} not found")

    def list_all(self) -> list[AgentTemplate]:
        """返回 store 中所有模板（global + workspace），供内部或管理用途。"""
        return [AgentTemplate.from_dict(d) for d in self._store.list_all_dicts()]

    def list_global(self) -> list[AgentTemplate]:
        """仅返回内置全局模板（scope='global'）。"""
        return [
            AgentTemplate.from_dict(d)
            for d in self._store.list_all_dicts()
            if d.get("scope", "global") == "global"
        ]

    def list_for_workspace(self, working_dir: str) -> list[AgentTemplate]:
        """返回 workspace 可见模板：global 全部 + 匹配 working_dir 的 workspace，同名时 workspace 优先。"""
        workspace_by_name: dict[str, AgentTemplate] = {}
        global_list: list[AgentTemplate] = []

        for d in self._store.list_all_dicts():
            scope = d.get("scope", "global")
            if scope == "workspace":
                if d.get("workspace_dir", "") == working_dir:
                    tpl = AgentTemplate.from_dict(d)
                    workspace_by_name[tpl.name] = tpl
            else:
                global_list.append(AgentTemplate.from_dict(d))

        result = list(workspace_by_name.values())
        seen = set(workspace_by_name.keys())
        for tpl in global_list:
            if tpl.name not in seen:
                result.append(tpl)
        return result

    # ── 按 name 查找（优先级感知）────────────────────────────────────────────

    def get_by_name(self, name: str) -> AgentTemplate | None:
        """按 name 查找全局模板，未找到返回 None。"""
        return self._find_by_scope(name, "global")

    def get_by_name_for_workspace(self, name: str, working_dir: str = "") -> AgentTemplate | None:
        """优先级查找：workspace(working_dir) > global。"""
        if working_dir:
            ws = self._find_by_scope(name, "workspace", working_dir)
            if ws:
                return ws
        return self._find_by_scope(name, "global")

    # ── 启动时批量 upsert（内置模板）────────────────────────────────────────

    def upsert_by_name(
        self,
        name: str,
        version: str,
        description: str,
        source_dir: str = "",
        scope: str = "global",
        workspace_dir: str = "",
    ) -> AgentTemplate:
        """按 name + scope(+ workspace_dir) 做 upsert。启动扫描时 scope='global'。"""
        existing = self._find_by_scope(name, scope, workspace_dir)
        now = now_iso()

        if existing is not None:
            existing.version = version
            existing.description = description
            existing.source_dir = source_dir
            existing.updated_at = now
            self._store.save(existing.to_dict())
            return existing

        tpl = AgentTemplate(
            id=new_template_id(),
            name=name,
            version=version,
            description=description,
            source_dir=source_dir,
            scope=scope,
            workspace_dir=workspace_dir,
            created_at=now,
            updated_at=now,
        )
        self._store.save(tpl.to_dict())
        logger.debug("AgentTemplateService: upserted template '%s' (scope=%s)", name, scope)
        return tpl

    # ── workspace 同步────────────────────────────────────────────────────────

    def sync_workspace(self, working_dir: str, metadata_list: list[AgentDefMetadata]) -> list[AgentTemplate]:
        """session 创建时调用：将扫描到的 .agents/ 目录模板持久化到 store。
        已存在则更新，磁盘上消失的旧条目则删除。
        """
        result: list[AgentTemplate] = []
        synced_names: set[str] = set()

        for meta in metadata_list:
            tpl = self.upsert_by_name(
                name=meta.name,
                version=meta.version,
                description=meta.description,
                source_dir=str(meta.agent_dir),
                scope="workspace",
                workspace_dir=working_dir,
            )
            synced_names.add(meta.name)
            result.append(tpl)

        self._purge_stale_workspace(working_dir, synced_names)
        logger.info(
            "AgentTemplateService: synced %d workspace template(s) for '%s'",
            len(result), working_dir,
        )
        return result

    # ── 兼容旧调用方 ─────────────────────────────────────────────────────────

    def get_or_prepare(self, template_id: str, llm_client=None) -> AgentTemplate:
        return self.get(template_id)

    # ── 内部工具 ─────────────────────────────────────────────────────────────

    def _find_by_scope(self, name: str, scope: str, workspace_dir: str = "") -> AgentTemplate | None:
        for d in self._store.list_all_dicts():
            if d.get("name") != name:
                continue
            if d.get("scope", "global") != scope:
                continue
            if scope == "workspace" and d.get("workspace_dir", "") != workspace_dir:
                continue
            return AgentTemplate.from_dict(d)
        return None

    def _purge_stale_workspace(self, working_dir: str, keep_names: set[str]) -> None:
        for d in self._store.list_all_dicts():
            if d.get("scope") == "workspace" and d.get("workspace_dir") == working_dir:
                if d.get("name") not in keep_names:
                    self._store.delete(d["id"])
                    logger.debug(
                        "AgentTemplateService: removed stale workspace template '%s' from '%s'",
                        d.get("name"), working_dir,
                    )
