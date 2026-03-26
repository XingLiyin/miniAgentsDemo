"""ToolCall API Schema 定义。"""

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel


class ToolCallResponse(BaseModel):
    id: str
    session_id: str
    task_id: str
    agent_id: str
    tool_name: str
    status: str
    arguments: dict[str, Any] = {}
    result: Optional[str] = None
    error: Optional[str] = None
    started_at: str
    finished_at: str


# Keep backward compat alias
ToolCallObject = ToolCallResponse
