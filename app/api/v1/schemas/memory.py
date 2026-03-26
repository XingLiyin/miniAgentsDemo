"""Memory API Schema 定义。"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel


class AppendMessageRequest(BaseModel):
    role: str           # user | assistant | tool
    content: str
    task_id: Optional[str] = None


class MessageResponse(BaseModel):
    id: str
    session_id: str
    agent_id: str
    role: str
    content: str
    task_id: Optional[str] = None
    created_at: str


class SummaryResponse(BaseModel):
    session_id: str
    agent_id: str
    summary_text: str
    covered_up_to: int
    created_at: str


# Keep backward compat aliases
MessageObject = MessageResponse
ContextObject = SummaryResponse
