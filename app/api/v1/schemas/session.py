"""Session API Schema 定义。"""

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel


class InitialTaskConfig(BaseModel):
    """第一个 task 的创建配置。
    不传则默认由 root agent 直接执行（不创建 sub-agent）。
    """
    title: Optional[str] = None                  # None 时用 "Plan: {goal[:80]}"
    use_subagent: bool = False                   # 是否委托给 sub-agent
    subagent_template: Optional[str] = None      # use_subagent=True 时指定模板名，None 则用系统默认


class CreateSessionRequest(BaseModel):
    goal: str
    template_id: Optional[str] = None
    token_budget: Optional[int] = None
    root_max_turns: Optional[int] = None
    llm_name: Optional[str] = None
    initial_task: Optional[InitialTaskConfig] = None


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
