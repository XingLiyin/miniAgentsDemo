"""Session API Schema 定义。"""

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, field_validator


class InitialTaskConfig(BaseModel):
    """第一个 task 的创建配置。
    不传则默认由 root agent 直接执行（不创建 sub-agent）。
    """
    title: Optional[str] = None                  # None 时用 "{user_prompt[:80]}"
    description: Optional[str] = None            # None 时用 user_prompt 前 200 字
    use_subagent: bool = False                   # 是否委托给 sub-agent
    subagent_template: Optional[str] = None      # use_subagent=True 时指定模板名，None 则用系统默认


class CreateSessionRequest(BaseModel):
    user_prompt: str
    template_id: Optional[str] = None
    token_budget: Optional[int] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    working_dir: Optional[str] = None
    initial_task: Optional[InitialTaskConfig] = None


class SessionResponse(BaseModel):
    id: str
    user_prompt: str
    goal: str

    @field_validator('user_prompt', mode='before')
    @classmethod
    def coerce_user_prompt(cls, v: Any) -> str:
        if isinstance(v, str):
            return v
        if isinstance(v, list):
            return '\n'.join(p.get('text', '') for p in v if isinstance(p, dict) and p.get('type') == 'text')
        return str(v) if v is not None else ''

    @field_validator('goal', mode='before')
    @classmethod
    def coerce_goal(cls, v: Any) -> str:
        if isinstance(v, str):
            return v
        if isinstance(v, list):
            return '\n'.join(p.get('text', '') for p in v if isinstance(p, dict) and p.get('type') == 'text')
        return str(v) if v is not None else ''
    status: str
    template_id: Optional[str] = None
    root_agent_id: Optional[str] = None
    token_budget: int
    input_tokens_used: int
    output_tokens_used: int
    context_tokens: int = 0
    failure_counter: int
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    working_dir: str = ""
    created_at: str
    updated_at: str


# Keep backward compat alias
SessionObject = SessionResponse
