"""AgentTemplate API Schema 定义。"""

from __future__ import annotations

from pydantic import BaseModel


class AgentTemplateResponse(BaseModel):
    id: str
    name: str
    version: str
    description: str
    act_tool_list: list[str]
    observe_tool_list: list[str]
    mcp_act_servers: list[str]
    mcp_observe_servers: list[str]
    source_dir: str
    has_spawn_permission: bool
    created_at: str
    updated_at: str
