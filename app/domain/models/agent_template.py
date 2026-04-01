"""AgentTemplate domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentTemplate:
    """Static agent template used to initialize runtime agents."""

    id: str
    name: str

    # Legacy fallback field used when the four markdown fields are empty.
    system_prompt: str = ""

    soul_md: str = ""
    role_md: str = ""
    tools_md: str = ""
    style_md: str = ""

    version: str = "1.0.0"
    tool_list: list[str] = field(default_factory=list)
    tool_list_ready: bool = False
    skill_list: list[str] = field(default_factory=list)
    source_dir: str = ""
    inject_style: bool = False
    has_spawn_permission: bool = False
    description: str = ""

    summary_threshold: int = 20
    short_window_size: int = 20

    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "system_prompt": self.system_prompt,
            "soul_md": self.soul_md,
            "role_md": self.role_md,
            "tools_md": self.tools_md,
            "style_md": self.style_md,
            "version": self.version,
            "tool_list": self.tool_list,
            "tool_list_ready": self.tool_list_ready,
            "skill_list": self.skill_list,
            "source_dir": self.source_dir,
            "inject_style": self.inject_style,
            "has_spawn_permission": self.has_spawn_permission,
            "description": self.description,
            "summary_threshold": self.summary_threshold,
            "short_window_size": self.short_window_size,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentTemplate":
        return cls(
            id=d["id"],
            name=d["name"],
            system_prompt=d.get("system_prompt", ""),
            soul_md=d.get("soul_md", ""),
            role_md=d.get("role_md", ""),
            tools_md=d.get("tools_md", ""),
            style_md=d.get("style_md", ""),
            version=d.get("version", "1.0.0"),
            tool_list=d.get("tool_list", []),
            tool_list_ready=d.get("tool_list_ready", False),
            skill_list=d.get("skill_list", []),
            source_dir=d.get("source_dir", ""),
            inject_style=d.get("inject_style", False),
            has_spawn_permission=d.get("has_spawn_permission", False),
            description=d.get("description", ""),
            summary_threshold=d.get("summary_threshold", 20),
            short_window_size=d.get("short_window_size", 20),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )
