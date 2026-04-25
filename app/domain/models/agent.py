"""Agent 领域模型（Phase 1）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LoopGuard:
    """Agent Loop Guard 运行时计数。"""
    turns_used: int = 0
    max_turns: int = 20
    actor_max_tool_rounds: int = 50      # 单个 atomic task 内最多工具调用轮次
    observer_max_tool_rounds: int = 5    # observer ReAct 循环最多轮次
    context_tokens: int = 0             # 最近一次 LLM 调用的 prompt_tokens（当前窗口大小）
    context_limit: int = 180_000        # 触发 compaction 的 prompt_tokens 阈值

    def to_dict(self) -> dict[str, Any]:
        return {
            "turns_used": self.turns_used,
            "max_turns": self.max_turns,
            "actor_max_tool_rounds": self.actor_max_tool_rounds,
            "observer_max_tool_rounds": self.observer_max_tool_rounds,
            "context_tokens": self.context_tokens,
            "context_limit": self.context_limit,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LoopGuard":
        return cls(
            turns_used=d.get("turns_used", 0),
            max_turns=d.get("max_turns", 20),
            actor_max_tool_rounds=d.get("actor_max_tool_rounds", 50),
            observer_max_tool_rounds=d.get("observer_max_tool_rounds", 50),
            context_tokens=d.get("context_tokens", 0),
            context_limit=d.get("context_limit", 180_000),
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
    llm_provider: str = ""                      # LLM provider 名称
    llm_model: str = ""                         # 指定模型，空 = 用 provider 的 default_model

    # context engineering
    soul_md: str = ""                           # 驱动 Actor 阶段 system prompt（执行人格）
    role_md: str = ""                           # 驱动 Observer 阶段 system prompt（评判准则）
    act_tool_list: list[str] = field(default_factory=list)      # Actor 阶段显式工具
    observe_tool_list: list[str] = field(default_factory=list)  # Observer 阶段显式工具
    mcp_act_servers: list[str] = field(default_factory=list)    # Actor 阶段订阅的 MCP server
    mcp_observe_servers: list[str] = field(default_factory=list)  # Observer 阶段订阅的 MCP server
    skill_list: list[str] = field(default_factory=list)
    soul_path: str | None = None
    inherit_memory: bool = True                 # False = spawn 时跳过记忆复制

    loop_guard: LoopGuard = field(default_factory=LoopGuard)
    
    # Spawn 字段
    has_spawn_permission: bool = False          # 是否允许 spawn sub-agent
    spawn_depth: int = 0                        # 嵌套深度（root=0）
    parent_task_id: str | None = None          # 本 agent 正在执行的 Task（sub-agent 填充）

    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "template_id": self.template_id,
            "name": self.name,
            "status": self.status,
            "soul_md": self.soul_md,
            "role_md": self.role_md,
            "act_tool_list": self.act_tool_list,
            "observe_tool_list": self.observe_tool_list,
            "mcp_act_servers": self.mcp_act_servers,
            "mcp_observe_servers": self.mcp_observe_servers,
            "skill_list": self.skill_list,
            "soul_path": self.soul_path,
            "loop_guard": self.loop_guard.to_dict(),
            "inherit_memory": self.inherit_memory,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "has_spawn_permission": self.has_spawn_permission,
            "spawn_depth": self.spawn_depth,
            "parent_task_id": self.parent_task_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Agent":
        return cls(
            id=d["id"],
            session_id=d["session_id"],
            template_id=d.get("template_id"),
            name=d["name"],
            status=d["status"],
            soul_md=d.get("soul_md", ""),
            role_md=d.get("role_md", ""),
            act_tool_list=d.get("act_tool_list", []),
            observe_tool_list=d.get("observe_tool_list", []),
            mcp_act_servers=d.get("mcp_act_servers", []),
            mcp_observe_servers=d.get("mcp_observe_servers", []),
            skill_list=d.get("skill_list", []),
            soul_path=d.get("soul_path"),
            loop_guard=LoopGuard.from_dict(d.get("loop_guard", {})),
            inherit_memory=d.get("inherit_memory", True),
            llm_provider=d.get("llm_provider", ""),
            llm_model=d.get("llm_model", ""),
            has_spawn_permission=d.get("has_spawn_permission", False),
            spawn_depth=d.get("spawn_depth", 0),
            parent_task_id=d.get("parent_task_id"),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )
