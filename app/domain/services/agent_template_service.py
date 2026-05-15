"""AgentTemplate 领域服务（无状态，直接读 store）。"""

from __future__ import annotations

from app.common.errors import AppError
from app.config.settings import resolve_working_dir
from app.domain.models.agent_template import AgentTemplate
from app.storage.file.agent_template_store import AgentTemplateStore


class AgentTemplateService:
    """按 name 查找模板元数据，构造 AgentTemplate 对象供 API 层使用。不读取模板文件。"""

    def __init__(self, store: AgentTemplateStore) -> None:
        self._store = store

    def get_by_name(self, name: str, workspace_dir: str = "") -> AgentTemplate | None:
        d = self._store.find_by_name(name, resolve_working_dir(workspace_dir))
        return AgentTemplate.from_dict(d) if d else None

    def get(self, name: str, workspace_dir: str = "") -> AgentTemplate:
        tpl = self.get_by_name(name, workspace_dir)
        if tpl is None:
            raise AppError("TEMPLATE_NOT_FOUND", f"AgentTemplate '{name}' not found")
        return tpl

    def list_global(self) -> list[AgentTemplate]:
        return [AgentTemplate.from_dict(d) for d in self._store.list_global()]

    def list_for_workspace(self, workspace_dir: str) -> list[AgentTemplate]:
        return [AgentTemplate.from_dict(d) for d in self._store.list_for_workspace(resolve_working_dir(workspace_dir))]
