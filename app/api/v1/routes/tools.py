"""ToolCall 审计相关路由（Phase 1）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.v1.schemas.tool import ToolCallResponse
from app.domain.models.tool_call import ToolCall
from app.storage.file.tool_call_store import ToolCallStore

router = APIRouter()


@router.get("/sessions/{session_id}/tool-calls", response_model=list[ToolCallResponse])
def list_tool_calls(session_id: str) -> list[ToolCallResponse]:
    """列出 session 的工具调用审计记录。"""
    store = ToolCallStore()
    records = store.read_all(session_id)
    # 只返回最终状态（SUCCEEDED/FAILED），过滤中间 RUNNING 记录
    seen_ids: set[str] = set()
    final_records = []
    for r in reversed(records):
        if r.get("id") not in seen_ids:
            seen_ids.add(r["id"])
            final_records.append(r)
    final_records.reverse()
    return [ToolCallResponse(**r) for r in final_records]
