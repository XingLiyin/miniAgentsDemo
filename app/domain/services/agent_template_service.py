"""AgentTemplate 领域服务（Phase 1）。"""

from __future__ import annotations

from app.common.errors import AppError
from app.common.utils import new_template_id, now_iso
from app.domain.models.agent_template import AgentTemplate
from app.storage.file.agent_template_store import AgentTemplateStore


class AgentTemplateService:
    """AgentTemplate CRUD。"""

    def __init__(self, store: AgentTemplateStore) -> None:
        self._store = store

    def create(
        self,
        name: str,
        system_prompt: str,
        tool_list: list[str] | None = None,
        description: str = "",
        summary_threshold: int = 20,
        short_window_size: int = 20,
    ) -> AgentTemplate:
        now = now_iso()
        tpl = AgentTemplate(
            id=new_template_id(),
            name=name,
            system_prompt=system_prompt,
            tool_list=tool_list or [],
            description=description,
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
