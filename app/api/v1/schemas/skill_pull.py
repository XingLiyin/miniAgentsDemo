from __future__ import annotations

from pydantic import BaseModel


class RemoteCatalogItem(BaseModel):
    id: str
    name: str
    description: str | None
    domain: str | None
    create_time: str | None
    is_pulled: bool


class PullSkillResponse(BaseModel):
    skill_id: str
    name: str
