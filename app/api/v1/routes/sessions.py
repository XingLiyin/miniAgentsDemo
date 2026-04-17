"""Session 相关路由（Phase 1）。"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from pydantic import BaseModel

from app.api.v1.deps import get_session_manager, get_session_service, get_task_service
from app.api.v1.schemas.session import CreateSessionRequest, InitialTaskConfig, SessionResponse
from app.api.v1.schemas.task import TaskResponse
from app.common.errors import AppError


class SendMessageRequest(BaseModel):
    content: str
    initial_task: InitialTaskConfig | None = None


class AnswerInputRequest(BaseModel):
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
            user_prompt=req.user_prompt,
            template_id=req.template_id,
            token_budget=req.token_budget,
            root_max_turns=req.root_max_turns,
            llm_name=req.llm_name,
            initial_task=req.initial_task,
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


@router.delete("/{session_id}", status_code=204)
def delete_session(session_id: str) -> Response:
    """删除会话及其所有关联数据（任务、记忆、工具调用、Agent 等）。"""
    try:
        mgr = get_session_manager()
        mgr.delete_session(session_id)
        return Response(status_code=204)
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
        session = mgr.continue_session(session_id, req.content, initial_task=req.initial_task)
        return SessionResponse(**session.to_dict())
    except AppError as e:
        status = 404 if e.code == "SESSION_NOT_FOUND" else 400
        raise HTTPException(status_code=status, detail={"code": e.code, "message": e.message})


@router.post("/{session_id}/input", response_model=SessionResponse)
async def answer_input(session_id: str, req: AnswerInputRequest) -> SessionResponse:
    """Submit a user answer for a WAITING_INPUT task and resume the agent loop."""
    try:
        mgr = get_session_manager()
        session = mgr.answer_input(session_id, req.content)
        return SessionResponse(**session.to_dict())
    except AppError as e:
        status = 404 if e.code == "SESSION_NOT_FOUND" else 400
        raise HTTPException(status_code=status, detail={"code": e.code, "message": e.message})


@router.get("/{session_id}/stream")
async def stream_session_events(session_id: str, request: Request) -> StreamingResponse:
    """SSE stream：实时推送 session 下的所有事件。"""
    from app.api.v1.deps import get_memory_service
    from app.runtime.sse_bus import get_sse_bus

    try:
        svc = get_session_service()
        session = svc.get(session_id)
    except AppError as e:
        raise HTTPException(status_code=404, detail={"code": e.code, "message": e.message})

    sse_bus = get_sse_bus()

    async def generate():
        q = sse_bus.create_subscription(session_id)
        try:
            # 发送初始快照
            task_svc = get_task_service()
            mem_svc = get_memory_service()

            tasks = [t for t in task_svc.list_by_session(session_id) if not t.settings.get("_daemon")]
            task_data = [TaskResponse(**t.to_dict()).model_dump() for t in tasks]

            agent_id = session.root_agent_id or ""
            messages: list = []
            if agent_id:
                messages = mem_svc.get_window(agent_id, n=500)

            init_event = {
                "type": "init",
                "session": SessionResponse(**session.to_dict()).model_dump(),
                "tasks": task_data,
                "messages": messages,
            }
            yield f"data: {json.dumps(init_event, default=str)}\n\n"

            # 发送历史事件快照（用于断线重连后恢复完整聊天记录）
            from app.runtime.event_store import get_event_store
            history = get_event_store().load(session_id)
            if history:
                history_event = {"type": "history", "events": history}
                yield f"data: {json.dumps(history_event, default=str)}\n\n"

            # 若 session 正在等待用户输入，重放 waiting_input 事件（断线重连恢复输入框）
            if session.status == "WAITING_INPUT":
                from app.runtime.hitl_store import get_hitl_store
                pending = get_hitl_store().get_pending(session_id)
                if pending:
                    replay = {
                        "type": "waiting_input",
                        "prompt": pending.prompt,
                        "input_type": pending.input_type,
                        "task_title": "等待用户输入",
                    }
                    yield f"data: {json.dumps(replay, default=str)}\n\n"

            # 流式推送后续事件
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                    if event.get("type") == "done":
                        break
                except asyncio.TimeoutError:
                    yield 'data: {"type":"ping"}\n\n'
        finally:
            sse_bus.remove_subscription(session_id, q)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/{session_id}/tasks", response_model=list[TaskResponse])
def list_session_tasks(session_id: str) -> list[TaskResponse]:
    """列出 session 下的所有 Task。"""
    try:
        task_svc = get_task_service()
        tasks = [t for t in task_svc.list_by_session(session_id) if not t.settings.get("_daemon")]
        return [TaskResponse(**t.to_dict()) for t in tasks]
    except AppError as e:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message})
