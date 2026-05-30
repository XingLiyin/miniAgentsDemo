"""Agent 领域模型（Phase 1）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LoopGuard:
    """Agent Loop Guard 运行时计数。"""
    actor_max_tool_rounds: int = 100      # 单个 atomic task 内最多工具调用轮次
    observer_max_tool_rounds: int = 5    # observer ReAct 循环最多轮次
    context_tokens: int = 0             # 最近一次 LLM 调用的 prompt_tokens（当前窗口大小）
    context_message_count: int = 0      # 上次 LLM 调用时 memory 消息数，用于增量 token 估算

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor_max_tool_rounds": self.actor_max_tool_rounds,
            "observer_max_tool_rounds": self.observer_max_tool_rounds,
            "context_tokens": self.context_tokens,
            "context_message_count": self.context_message_count,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LoopGuard":
        return cls(
            actor_max_tool_rounds=d.get("actor_max_tool_rounds", 50),
            observer_max_tool_rounds=d.get("observer_max_tool_rounds", 5),
            context_tokens=d.get("context_tokens", 0),
            context_message_count=d.get("context_message_count", 0),
        )
    
@dataclass
class AgentCapability:
    """Agent 能力配置。"""
    instruction_md: str = ""
    tools: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    subagents: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instruction_md": self.instruction_md,
            "tools": self.tools,
            "mcp_servers": self.mcp_servers,
            "skills": self.skills,
            "subagents": self.subagents,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentCapability":
        return cls(
            instruction_md=d.get("instruction_md", ""),
            tools=d.get("tools", []),
            mcp_servers=d.get("mcp_servers", []),
            skills=d.get("skills", []),
            subagents=d.get("subagents", []),
        )


@dataclass
class Agent:
    """Agent 运行时实例。

    状态流转：IDLE → RUNNING → FINISHED / FAILED
                       ↕
                    WAITING  （调用 spawn_agents 后等待子任务完成）
    """
    # metadata
    id: str
    session_id: str
    template_id: str
    name: str
    status: str                                 # IDLE | RUNNING | WAITING | FINISHED | FAILED
    # context engineering
    actor: AgentCapability = field(default_factory=AgentCapability)
    observer: AgentCapability = field(default_factory=AgentCapability)
    inherit_memory: bool = True                 # False = spawn 时跳过记忆复制

    loop_guard: LoopGuard = field(default_factory=LoopGuard)
    
    # Spawn 字段
    has_spawn_permission: bool = False          # 是否允许 spawn sub-agent
    spawn_depth: int = 0                        # 嵌套深度（root=0）

    settings: dict[str, Any] = field(default_factory=dict)  # 运行时配置，如 working_dir
    tracking_tasks: list[str] = field(default_factory=list)  # 需要拉取 blackboard 的 task id 列表
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "template_id": self.template_id,
            "name": self.name,
            "status": self.status,
            "actor": self.actor.to_dict(),
            "observer": self.observer.to_dict(),
            "loop_guard": self.loop_guard.to_dict(),
            "inherit_memory": self.inherit_memory,
            "has_spawn_permission": self.has_spawn_permission,
            "spawn_depth": self.spawn_depth,
            "settings": self.settings,
            "tracking_tasks": self.tracking_tasks,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Agent":
        return cls(
            id=d["id"],
            session_id=d["session_id"],
            template_id=d.get("template_id") or d.get("template_name") or "",
            name=d["name"],
            status=d["status"],
            actor=AgentCapability.from_dict(d.get("actor", {})),
            observer=AgentCapability.from_dict(d.get("observer", {})),
            loop_guard=LoopGuard.from_dict(d.get("loop_guard", {})),
            inherit_memory=d.get("inherit_memory", True),
            has_spawn_permission=d.get("has_spawn_permission", False),
            spawn_depth=d.get("spawn_depth", 0),
            settings=d.get("settings", {}),
            tracking_tasks=d.get("tracking_tasks", []),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )
