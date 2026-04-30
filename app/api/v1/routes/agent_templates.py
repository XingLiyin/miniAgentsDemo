"""AgentTemplate 相关路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.v1.deps import get_agent_template_registry, get_agent_template_service
from app.api.v1.schemas.agent_template import AgentTemplateResponse
from app.common.errors import AppError

router = APIRouter()


@router.get("", response_model=list[AgentTemplateResponse])
def list_templates(workspace_dir: str = Query(default="")) -> list[AgentTemplateResponse]:
    """列出可用 AgentTemplate。
    workspace_dir 非空时返回该 workspace 的模板（workspace 同名覆盖 global）；
    否则仅返回内置全局模板。
    """
    svc = get_agent_template_service()
    templates = svc.list_for_workspace(workspace_dir) if workspace_dir else svc.list_global()
    return [AgentTemplateResponse(**t.to_dict()) for t in templates]


@router.post("/sync", response_model=list[AgentTemplateResponse])
def sync_workspace_templates(workspace_dir: str = Query()) -> list[AgentTemplateResponse]:
    """重新扫描 workspace_dir/.agents/ 并刷新 store 中的 workspace 模板。
    返回刷新后该 workspace 的全部可见模板列表。
    """
    if not workspace_dir:
        raise HTTPException(status_code=400, detail={"code": "MISSING_PARAM", "message": "workspace_dir is required"})
    registry = get_agent_template_registry()
    registry.sync_workspace(workspace_dir)
    svc = get_agent_template_service()
    templates = svc.list_for_workspace(workspace_dir)
    return [AgentTemplateResponse(**t.to_dict()) for t in templates]


@router.get("/{template_id}", response_model=AgentTemplateResponse)
def get_template(template_id: str) -> AgentTemplateResponse:
    """获取 AgentTemplate 详情（按 UUID）。"""
    try:
        svc = get_agent_template_service()
        tpl = svc.get(template_id)
        return AgentTemplateResponse(**tpl.to_dict())
    except AppError as e:
        status = 404 if e.code == "TEMPLATE_NOT_FOUND" else 400
        raise HTTPException(status_code=status, detail={"code": e.code, "message": e.message})
