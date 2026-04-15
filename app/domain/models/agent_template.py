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
    act_tool_list: list[str] = field(default_factory=list)    # Actor 阶段显式工具
    observe_tool_list: list[str] = field(default_factory=list)  # Observer 阶段显式工具
    mcp_act_servers: list[str] = field(default_factory=list)    # Actor 阶段订阅的 MCP server
    mcp_observe_servers: list[str] = field(default_factory=list)  # Observer 阶段订阅的 MCP server
    skill_list: list[str] = field(default_factory=list)
    source_dir: str = ""
    inject_style: bool = False
    has_spawn_permission: bool = False
    planner_template_name: str = ""  # 指定 plan sub-agent 使用的模板名；空则用系统默认
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
            "act_tool_list": self.act_tool_list,
            "observe_tool_list": self.observe_tool_list,
            "mcp_act_servers": self.mcp_act_servers,
            "mcp_observe_servers": self.mcp_observe_servers,
            "skill_list": self.skill_list,
            "source_dir": self.source_dir,
            "inject_style": self.inject_style,
            "has_spawn_permission": self.has_spawn_permission,
            "planner_template_name": self.planner_template_name,
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
            act_tool_list=d.get("act_tool_list", []),
            observe_tool_list=d.get("observe_tool_list", []),
            mcp_act_servers=d.get("mcp_act_servers", []),
            mcp_observe_servers=d.get("mcp_observe_servers", []),
            skill_list=d.get("skill_list", []),
            source_dir=d.get("source_dir", ""),
            inject_style=d.get("inject_style", False),
            has_spawn_permission=d.get("has_spawn_permission", False),
            planner_template_name=d.get("planner_template_name", ""),
            description=d.get("description", ""),
            summary_threshold=d.get("summary_threshold", 20),
            short_window_size=d.get("short_window_size", 20),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )
