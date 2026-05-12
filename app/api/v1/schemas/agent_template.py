"""AgentTemplate API Schema 定义。"""

from __future__ import annotations

from pydantic import BaseModel


class AgentTemplateResponse(BaseModel):
    id: str
    name: str
    version: str
    description: str
    scope: str = "global"
    source_dir: str
    workspace_dir: str = ""
    created_at: str
    updated_at: str
