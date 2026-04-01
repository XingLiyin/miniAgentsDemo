"""Session 相关路由（Phase 1）。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from pydantic import BaseModel

from app.api.v1.deps import get_session_manager, get_session_service, get_task_service
from app.api.v1.schemas.session import CreateSessionRequest, SessionResponse
from app.api.v1.schemas.task import TaskResponse
from app.common.errors import AppError


class SendMessageRequest(BaseModel):
    content: str


class AnswerInputRequest(BaseModel):
    task_id: str
    content: str

router = APIRouter()


@router.get("", response_model=list[SessionResponse])
def list_sessions() -> list[SessionResponse]:
    """列出所有会话，按创建时间倒序。"""
    svc = get_session_service()
    sessions = [svc.get(sid) for sid in svc.list_ids()]
    sessions.sort(key=lambda s: s.created_at, reverse=True)
    return [SessionResponse(**s.to_dict()) for s in sessions]


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


@router.post("/{session_id}/messages", response_model=SessionResponse)
async def send_message(session_id: str, req: SendMessageRequest) -> SessionResponse:
    """Send a user message to a session. Re-opens the session if it has ended."""
    try:
        mgr = get_session_manager()
        session = mgr.continue_session(session_id, req.content)
        return SessionResponse(**session.to_dict())
    except AppError as e:
        status = 404 if e.code == "SESSION_NOT_FOUND" else 400
        raise HTTPException(status_code=status, detail={"code": e.code, "message": e.message})


@router.post("/{session_id}/input", response_model=SessionResponse)
async def answer_input(session_id: str, req: AnswerInputRequest) -> SessionResponse:
    """Submit a user answer for a WAITING_INPUT task and resume the agent loop."""
    try:
        mgr = get_session_manager()
        session = mgr.answer_input(session_id, req.task_id, req.content)
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
