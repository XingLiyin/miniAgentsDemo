"""Session 相关路由（Phase 1）。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from app.api.v1.deps import get_session_manager, get_session_service, get_task_service
from app.api.v1.schemas.session import CreateSessionRequest, SessionResponse
from app.api.v1.schemas.task import TaskResponse
from app.common.errors import AppError

router = APIRouter()


@router.post("", response_model=SessionResponse, status_code=202)
async def create_session(req: CreateSessionRequest) -> SessionResponse:
    """创建会话并异步启动 Agent Loop，返回 202 + session 对象。"""
    try:
        mgr = get_session_manager()
        session, agent_id = mgr.create_session(
            goal=req.goal,
            template_id=req.template_id,
            token_budget=req.token_budget,
            root_max_turns=req.root_max_turns,
        )
        # 异步启动 AgentLoop（在当前 asyncio event loop 中）
        mgr.schedule_loop(session.id, agent_id)
        return SessionResponse(**session.to_dict())
    except AppError as e:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message})


@router.get("/{session_id}", response_model=SessionResponse)
def get_session(session_id: str) -> SessionResponse:
    """获取会话详情。"""
    try:
        svc = get_session_service()
        session = svc.get(session_id)
        return SessionResponse(**session.to_dict())
    except AppError as e:
        status = 404 if e.code == "SESSION_NOT_FOUND" else 400
        raise HTTPException(status_code=status, detail={"code": e.code, "message": e.message})


@router.post("/{session_id}/cancel", response_model=SessionResponse)
def cancel_session(session_id: str) -> SessionResponse:
    """取消会话。"""
    try:
        mgr = get_session_manager()
        session = mgr.cancel_session(session_id)
        return SessionResponse(**session.to_dict())
    except AppError as e:
        status = 404 if e.code == "SESSION_NOT_FOUND" else 400
        raise HTTPException(status_code=status, detail={"code": e.code, "message": e.message})


@router.get("/{session_id}/tasks", response_model=list[TaskResponse])
def list_session_tasks(session_id: str) -> list[TaskResponse]:
    """列出 session 下的所有 Task。"""
    try:
        task_svc = get_task_service()
        tasks = task_svc.list_by_session(session_id)
        return [TaskResponse(**t.to_dict()) for t in tasks]
    except AppError as e:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message})
