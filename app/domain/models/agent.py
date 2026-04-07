"""Agent 领域模型（Phase 1）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LoopGuard:
    """Agent Loop Guard 运行时计数。"""
    turns_used: int = 0
    max_turns: int = 20
    actor_max_tool_rounds: int = 5      # 单个 atomic task 内最多工具调用轮次

    def to_dict(self) -> dict[str, Any]:
        return {
            "turns_used": self.turns_used,
            "max_turns": self.max_turns,
            "actor_max_tool_rounds": self.actor_max_tool_rounds,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LoopGuard":
        return cls(
            turns_used=d.get("turns_used", 0),
            max_turns=d.get("max_turns", 20),
            actor_max_tool_rounds=d.get("actor_max_tool_rounds", 5),
        )


@dataclass
class Agent:
    """Agent 运行时实例。

    状态流转：IDLE → RUNNING → FINISHED / FAILED
                       ↕
                    WAITING  （调用 spawn_agents 后等待子任务完成）
    """
    id: str
    session_id: str
    template_id: str | None
    name: str
    status: str                                 # IDLE | RUNNING | WAITING | FINISHED | FAILED

    system_prompt: str = ""
    tool_list: list[str] = field(default_factory=list)
    skill_list: list[str] = field(default_factory=list)
    soul_path: str | None = None
    loop_guard: LoopGuard = field(default_factory=LoopGuard)

    # LLM 配置（继承自 template 或 session 创建时指定）
    llm_name: str = ""

    # Spawn 字段
    has_spawn_permission: bool = False          # 是否允许调用 spawn_agents
    spawn_depth: int = 0                        # 嵌套深度（root=0）
    parent_task_id: str | None = None          # 本 agent 正在执行的 Task（sub-agent 填充）
    spawned_task_ids: list[str] = field(default_factory=list)  # WAITING 时派生的子 task_id

    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "template_id": self.template_id,
            "name": self.name,
            "status": self.status,
            "system_prompt": self.system_prompt,
            "tool_list": self.tool_list,
            "skill_list": self.skill_list,
            "soul_path": self.soul_path,
            "loop_guard": self.loop_guard.to_dict(),
            "llm_name": self.llm_name,
            "has_spawn_permission": self.has_spawn_permission,
            "spawn_depth": self.spawn_depth,
            "parent_task_id": self.parent_task_id,
            "spawned_task_ids": self.spawned_task_ids,
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
            system_prompt=d.get("system_prompt", ""),
            tool_list=d.get("tool_list", []),
            skill_list=d.get("skill_list", []),
            soul_path=d.get("soul_path"),
            loop_guard=LoopGuard.from_dict(d.get("loop_guard", {})),
            llm_name=d.get("llm_name", ""),
            has_spawn_permission=d.get("has_spawn_permission", False),
            spawn_depth=d.get("spawn_depth", 0),
            parent_task_id=d.get("parent_task_id"),
            spawned_task_ids=d.get("spawned_task_ids", []),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )
