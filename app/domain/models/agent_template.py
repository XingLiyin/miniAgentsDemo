"""AgentTemplate domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentTemplate:
    """Static agent template used to initialize runtime agents."""

    id: str
    name: str

    version: str = "1.0.0"
    description: str = ""
    act_tool_list: list[str] = field(default_factory=list)
    observe_tool_list: list[str] = field(default_factory=list)
    mcp_act_servers: list[str] = field(default_factory=list)
    mcp_observe_servers: list[str] = field(default_factory=list)
    source_dir: str = ""
    has_spawn_permission: bool = False

    # "global" = built-in from resources/agents/; "workspace" = user-defined from {workspace_dir}/.agents/
    scope: str = "global"
    workspace_dir: str = ""  # non-empty only when scope == "workspace"

    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "act_tool_list": self.act_tool_list,
            "observe_tool_list": self.observe_tool_list,
            "mcp_act_servers": self.mcp_act_servers,
            "mcp_observe_servers": self.mcp_observe_servers,
            "source_dir": self.source_dir,
            "has_spawn_permission": self.  has_spawn_permission,
            "scope": self.scope,
            "workspace_dir": self.workspace_dir,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentTemplate":
        return cls(
            id=d["id"],
            name=d["name"],
            version=d.get("version", "1.0.0"),
            description=d.get("description", ""),
            act_tool_list=d.get("act_tool_list", []),
            observe_tool_list=d.get("observe_tool_list", []),
            mcp_act_servers=d.get("mcp_act_servers", []),
            mcp_observe_servers=d.get("mcp_observe_servers", []),
            source_dir=d.get("source_dir", ""),
            has_spawn_permission=d.get("has_spawn_permission", False),
            scope=d.get("scope", "global"),
            workspace_dir=d.get("workspace_dir", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )
