"""Memory 与 Blackboard 相关路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.v1.deps import get_memory_service, get_blackboard_service
from app.api.v1.schemas.memory import AppendMessageRequest, MessageResponse, SummaryResponse
from app.common.errors import AppError

router = APIRouter()


@router.get("/sessions/{session_id}/messages", response_model=list[MessageResponse])
def list_session_messages(session_id: str) -> list[MessageResponse]:
    """获取 session 下所有 agent 的全量消息，按时间排序。"""
    from app.storage.file.agent_store import AgentStore
    mem_svc = get_memory_service()
    agent_ids = AgentStore().list_by_session(session_id)
    all_msgs: list[dict] = []
    for agent_id in agent_ids:
        all_msgs.extend(mem_svc.get_all_messages(agent_id))
    all_msgs.sort(key=lambda m: m.get("created_at", ""))
    return [MessageResponse(**m) for m in all_msgs]


@router.get("/agents/{agent_id}/messages", response_model=list[MessageResponse])
def list_messages(agent_id: str, limit: int = 50) -> list[MessageResponse]:
    """列出该 agent 最近的消息记录。"""
    svc = get_memory_service()
    messages = svc.get_window(agent_id, limit)
    return [MessageResponse(**m) for m in messages]


@router.post("/agents/{agent_id}/messages", response_model=MessageResponse, status_code=201)
def append_message(agent_id: str, req: AppendMessageRequest) -> MessageResponse:
    """手动追加消息（用于测试/调试）。"""
    svc = get_memory_service()
    item = svc.append_message(
        agent_id=agent_id,
        role=req.role,
        content=req.content,
        task_id=req.task_id,
    )
    return MessageResponse(**item.to_dict())


@router.get("/agents/{agent_id}/summary", response_model=SummaryResponse)
def get_summary(agent_id: str) -> SummaryResponse:
    """获取该 agent 的最新摘要。"""
    svc = get_memory_service()
    summary = svc.get_summary(agent_id)
    if summary is None:
        raise HTTPException(status_code=404, detail={"code": "SUMMARY_NOT_FOUND", "message": "No summary yet"})
    return SummaryResponse(**summary.to_dict())
