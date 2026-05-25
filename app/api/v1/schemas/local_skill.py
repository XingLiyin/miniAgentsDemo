from __future__ import annotations

from pydantic import BaseModel


class LocalSkillResponse(BaseModel):
    skill_id: str
    name: str
    description: str
    version: str
    triggers: list[str]
