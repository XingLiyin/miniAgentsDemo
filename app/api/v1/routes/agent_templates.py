"""AgentTemplate 相关路由（Phase 1）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.v1.deps import get_agent_template_service
from app.api.v1.schemas.agent_template import CreateAgentTemplateRequest, AgentTemplateResponse
from app.common.errors import AppError

router = APIRouter()


@router.post("", response_model=AgentTemplateResponse, status_code=201)
def create_template(req: CreateAgentTemplateRequest) -> AgentTemplateResponse:
    """创建 AgentTemplate。"""
    svc = get_agent_template_service()
    tpl = svc.create(
        name=req.name,
        system_prompt=req.system_prompt,
        tool_list=req.tool_list,
        description=req.description,
        summary_threshold=req.summary_threshold,
        short_window_size=req.short_window_size,
    )
    return AgentTemplateResponse(**tpl.to_dict())


@router.get("", response_model=list[AgentTemplateResponse])
def list_templates() -> list[AgentTemplateResponse]:
    """列出所有 AgentTemplate。"""
    svc = get_agent_template_service()
    return [AgentTemplateResponse(**t.to_dict()) for t in svc.list_all()]


@router.get("/{template_id}", response_model=AgentTemplateResponse)
def get_template(template_id: str) -> AgentTemplateResponse:
    """获取 AgentTemplate 详情。"""
    try:
        svc = get_agent_template_service()
        tpl = svc.get(template_id)
        return AgentTemplateResponse(**tpl.to_dict())
    except AppError as e:
        status = 404 if e.code == "TEMPLATE_NOT_FOUND" else 400
        raise HTTPException(status_code=status, detail={"code": e.code, "message": e.message})


@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: str) -> None:
    """删除 AgentTemplate。"""
    try:
        svc = get_agent_template_service()
        svc.delete(template_id)
    except AppError as e:
        status = 404 if e.code == "TEMPLATE_NOT_FOUND" else 400
        raise HTTPException(status_code=status, detail={"code": e.code, "message": e.message})
