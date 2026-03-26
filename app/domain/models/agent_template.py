"""AgentTemplate 领域模型（Phase 1）。

AgentTemplate 是静态配置模板，与运行时 Agent 实例分离。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentTemplate:
    """Agent 模板（静态配置，不含运行时状态）。"""
    id: str
    name: str
    system_prompt: str
    tool_list: list[str] = field(default_factory=list)    # 允许使用的工具名称列表
    has_spawn_permission: bool = False                     # Phase 2：是否允许 spawn 子 Agent
    description: str = ""

    # Memory 配置
    summary_threshold: int = 20
    short_window_size: int = 20

    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "system_prompt": self.system_prompt,
            "tool_list": self.tool_list,
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
            system_prompt=d["system_prompt"],
            tool_list=d.get("tool_list", []),
            has_spawn_permission=d.get("has_spawn_permission", False),
            description=d.get("description", ""),
            summary_threshold=d.get("summary_threshold", 20),
            short_window_size=d.get("short_window_size", 20),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )
