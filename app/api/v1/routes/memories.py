"""Memory 与 Blackboard 相关路由（Phase 1）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.v1.deps import get_memory_service, get_blackboard_service
from app.api.v1.schemas.memory import AppendMessageRequest, MessageResponse, SummaryResponse
from app.common.errors import AppError

router = APIRouter()


@router.get("/sessions/{session_id}/messages", response_model=list[MessageResponse])
def list_messages(session_id: str, limit: int = 50) -> list[MessageResponse]:
    """列出 session 最近的消息记录。"""
    svc = get_memory_service()
    messages = svc.get_window(session_id, limit)
    return [MessageResponse(**m) for m in messages]


@router.post("/sessions/{session_id}/messages", response_model=MessageResponse, status_code=201)
def append_message(session_id: str, req: AppendMessageRequest) -> MessageResponse:
    """手动追加消息（用于测试/调试）。"""
    svc = get_memory_service()
    item = svc.append_message(
        session_id=session_id,
        agent_id="manual",
        role=req.role,
        content=req.content,
        task_id=req.task_id,
    )
    return MessageResponse(**item.to_dict())


@router.get("/sessions/{session_id}/summary", response_model=SummaryResponse)
def get_summary(session_id: str) -> SummaryResponse:
    """获取 session 最新摘要。"""
    svc = get_memory_service()
    summary = svc.get_summary(session_id)
    if summary is None:
        raise HTTPException(status_code=404, detail={"code": "SUMMARY_NOT_FOUND", "message": "No summary yet"})
    return SummaryResponse(**summary.to_dict())
