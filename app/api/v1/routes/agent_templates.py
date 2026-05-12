"""AgentTemplate 相关路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.v1.deps import get_agent_template_service, get_agent_template_syncer
from app.api.v1.schemas.agent_template import AgentTemplateResponse
from app.common.errors import AppError

router = APIRouter()


@router.get("", response_model=list[AgentTemplateResponse])
def list_templates(workspace_dir: str = Query(default="")) -> list[AgentTemplateResponse]:
    """列出可用 AgentTemplate。
    workspace_dir 非空时先同步 .agents/ 目录再返回该 workspace 的模板（workspace 同名覆盖 global）；
    否则仅返回内置全局模板。
    """
    if workspace_dir:
        get_agent_template_syncer().sync_workspace(workspace_dir)
    svc = get_agent_template_service()
    templates = svc.list_for_workspace(workspace_dir) if workspace_dir else svc.list_global()
    return [AgentTemplateResponse(**t.to_dict()) for t in templates]


@router.get("/{name}", response_model=AgentTemplateResponse)
def get_template(name: str, workspace_dir: str = Query(default="")) -> AgentTemplateResponse:
    """按 name 获取 AgentTemplate 详情，有 workspace_dir 时先同步。"""
    try:
        if workspace_dir:
            get_agent_template_syncer().sync_workspace(workspace_dir)
        svc = get_agent_template_service()
        tpl = svc.get(name, workspace_dir)
        return AgentTemplateResponse(**tpl.to_dict())
    except AppError as e:
        status = 404 if e.code == "TEMPLATE_NOT_FOUND" else 400
        raise HTTPException(status_code=status, detail={"code": e.code, "message": e.message})
