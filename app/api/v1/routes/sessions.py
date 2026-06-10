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
from app.storage.file.agent_store import AgentStore


def _session_response(session) -> SessionResponse:
    d = session.to_dict()
    if session.root_agent_id:
        agent_data = AgentStore().get(session.id, session.root_agent_id)
        if agent_data:
            d["context_tokens"] = agent_data.get("loop_guard", {}).get("context_tokens", 0)
    return SessionResponse(**d)


class SendMessageRequest(BaseModel):
    content: str | list           # str 纯文本 或 list[ContentPart dict] 多模态
    initial_task: InitialTaskConfig | None = None
    llm_provider: str | None = None
    llm_model: str | None = None


class AnswerInputRequest(BaseModel):
    content: str

router = APIRouter()


@router.get("", response_model=list[SessionResponse])
def list_sessions() -> list[SessionResponse]:
    """列出所有会话，按创建时间倒序。"""
    svc = get_session_service()
    sessions = [svc.get(sid) for sid in svc.list_ids()]
    sessions.sort(key=lambda s: s.created_at, reverse=True)
    return [_session_response(s) for s in sessions]


@router.post("", response_model=SessionResponse, status_code=202)
async def create_session(req: CreateSessionRequest) -> SessionResponse:
    """创建会话并异步启动 Agent Loop，返回 202 + session 对象。"""
    try:
        mgr = get_session_manager()
        session, agent_id = mgr.create_session(
            user_prompt=req.user_prompt,
            template_id=req.template_id,
            token_budget=req.token_budget,
            llm_provider=req.llm_provider,
            llm_model=req.llm_model,
            working_dir=req.working_dir,
            initial_task=req.initial_task,
        )
        # 异步启动 AgentLoop（在当前 asyncio event loop 中）
        mgr.schedule_loop(session.id, agent_id)
        return _session_response(session)
    except AppError as e:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message})


@router.get("/{session_id}", response_model=SessionResponse)
def get_session(session_id: str) -> SessionResponse:
    """获取会话详情。"""
    try:
        svc = get_session_service()
        session = svc.get(session_id)
        return _session_response(session)
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


@router.post("/{session_id}/interrupt", response_model=SessionResponse)
def interrupt_session(session_id: str) -> SessionResponse:
    """打断正在运行的 session。Agent 在下一个安全检查点停止，当前进度写入 memory。
    打断后可通过 POST /sessions/{session_id}/messages 发送新 prompt 恢复。
    """
    try:
        mgr = get_session_manager()
        session = mgr.interrupt_session(session_id)
        return _session_response(session)
    except AppError as e:
        status = 404 if e.code == "SESSION_NOT_FOUND" else 400
        raise HTTPException(status_code=status, detail={"code": e.code, "message": e.message})


@router.post("/{session_id}/cancel", response_model=SessionResponse)
def cancel_session(session_id: str) -> SessionResponse:
    """取消会话。"""
    try:
        mgr = get_session_manager()
        session = mgr.cancel_session(session_id)
        return _session_response(session)
    except AppError as e:
        status = 404 if e.code == "SESSION_NOT_FOUND" else 400
        raise HTTPException(status_code=status, detail={"code": e.code, "message": e.message})


@router.post("/{session_id}/messages", response_model=SessionResponse)
async def send_message(session_id: str, req: SendMessageRequest) -> SessionResponse:
    """Send a user message to a session. Re-opens the session if it has ended."""
    try:
        mgr = get_session_manager()
        session = mgr.continue_session(
            session_id, req.content,
            initial_task=req.initial_task,
            llm_provider=req.llm_provider,
            llm_model=req.llm_model,
        )
        return _session_response(session)
    except AppError as e:
        status = 404 if e.code == "SESSION_NOT_FOUND" else 400
        raise HTTPException(status_code=status, detail={"code": e.code, "message": e.message})


@router.post("/{session_id}/input", response_model=SessionResponse)
async def answer_input(session_id: str, req: AnswerInputRequest) -> SessionResponse:
    """Submit a user answer for a WAITING_INPUT task and resume the agent loop."""
    try:
        mgr = get_session_manager()
        session = mgr.answer_input(session_id, req.content)
        return _session_response(session)
    except AppError as e:
        status = 404 if e.code == "SESSION_NOT_FOUND" else 400
        raise HTTPException(status_code=status, detail={"code": e.code, "message": e.message})


@router.get("/{session_id}/stream")
async def stream_session_events(session_id: str, request: Request) -> StreamingResponse:
    """SSE stream：实时推送 session 下的所有事件。"""
    from app.api.v1.deps import get_memory_service
    from app.common.sse_bus import get_sse_bus

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

            all_tasks = task_svc.list_by_session(session_id)
            normal_tasks = [t for t in all_tasks if not t.settings.get("_daemon")]
            daemon_tasks = [t for t in all_tasks if t.settings.get("_daemon")]

            agent_id = session.root_agent_id or ""
            messages: list = []
            if agent_id:
                messages = mem_svc.get_window(agent_id, n=500)

            init_event = {
                "type": "init",
                "session": _session_response(session).model_dump(),
                "tasks": [TaskResponse(**t.to_dict()).model_dump() for t in normal_tasks],
                "daemon_tasks": [TaskResponse(**t.to_dict()).model_dump() for t in daemon_tasks],
                "messages": messages,
            }
            yield f"data: {json.dumps(init_event, default=str)}\n\n"

            # 每次连接都发全量历史快照，确保重连后 items 状态正确
            from app.storage.file.event_store import get_event_store
            history = get_event_store().load(session_id)
            if history:
                history_event = {"type": "history", "events": history}
                yield f"data: {json.dumps(history_event, default=str)}\n\n"

            # 若 session 正在等待用户输入，重放 waiting_input 事件（断线重连恢复输入框）
            if session.status == "WAITING_INPUT":
                from app.storage.file.hitl_store import get_hitl_store
                pending = get_hitl_store().get_pending(session_id)
                # 注意：prompt 可能为空字符串（ask_human 任务状态下不向前端展示 prompt），
                # 因此用"是否存在等待"而非 prompt 真值来决定是否重放输入框。
                if pending is not None:
                    replay_prompt = pending.prompt
                    replay_input_type = pending.input_type
                    replay_waiting = True
                elif "_hitl_prompt" in session.metadata:
                    replay_prompt = session.metadata["_hitl_prompt"]
                    replay_input_type = session.metadata.get("_hitl_input_type", "user_input")
                    replay_waiting = True
                else:
                    replay_prompt = ""
                    replay_input_type = "user_input"
                    replay_waiting = False
                if replay_waiting:
                    replay = {
                        "type": "waiting_input",
                        "prompt": replay_prompt,
                        "input_type": replay_input_type,
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
