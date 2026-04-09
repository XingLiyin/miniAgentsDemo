"""Task API Schema 定义。"""

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel


class TaskResponse(BaseModel):
    id: str
    session_id: str
    creator_agent_id: str
    assigned_agent_id: str
    type: str
    title: str
    status: str
    description: str = ""
    inputs: dict[str, Any] = {}
    result: Optional[str] = None
    outputs: dict[str, Any] = {}
    error: Optional[str] = None
    created_at: str
    updated_at: str


# Keep backward compat alias
TaskObject = TaskResponse
