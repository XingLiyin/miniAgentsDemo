"""AgentTemplate API Schema 定义。"""

from __future__ import annotations

from pydantic import BaseModel


class AgentTemplateResponse(BaseModel):
    id: str
    name: str
    system_prompt: str
    act_tool_list: list[str]
    observe_tool_list: list[str]
    description: str
    version: str
    inject_style: bool
    summary_threshold: int
    short_window_size: int
    has_spawn_permission: bool
    created_at: str
    updated_at: str
