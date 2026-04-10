"""Session API Schema 定义。"""

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel


class CreateSessionRequest(BaseModel):
    goal: str
    template_id: Optional[str] = None
    token_budget: Optional[int] = None
    root_max_turns: Optional[int] = None
    llm_name: Optional[str] = None


class SessionResponse(BaseModel):
    id: str
    goal: str
    status: str
    template_id: Optional[str] = None
    root_agent_id: Optional[str] = None
    token_budget: int
    token_used: int
    root_max_turns: int
    failure_counter: int
    created_at: str
    updated_at: str


# Keep backward compat alias
SessionObject = SessionResponse
