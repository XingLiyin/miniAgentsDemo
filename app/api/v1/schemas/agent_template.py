"""AgentTemplate API Schema 定义。"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel


class CreateAgentTemplateRequest(BaseModel):
    name: str
    system_prompt: str
    tool_list: list[str] = []
    description: str = ""
    summary_threshold: int = 20
    short_window_size: int = 20


class AgentTemplateResponse(BaseModel):
    id: str
    name: str
    system_prompt: str
    tool_list: list[str]
    description: str
    summary_threshold: int
    short_window_size: int
    has_spawn_permission: bool
    created_at: str
    updated_at: str
