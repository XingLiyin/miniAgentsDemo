"""Agent Loop v2 阶段间数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel


@dataclass
class SkillMeta:
    """Reasoner 检索出的技能元数据（轻量，不含完整 instructions）。"""
    name: str
    description: str


@dataclass
class ReasoningContext:
    """Reasoner 阶段输出，作为 Planner 和 Actor 的输入。"""
    # Memory 层
    goal: str
    recent_messages: list[dict]
    summary_text: str
    blackboard_snippets: list[str]
    # 检索结果
    relevant_tools: list[Any]           # list[LLMTool]，避免循环导入用 Any
    relevant_skills: list[SkillMeta]
    # Token 估算（给 guard 用）
    token_estimate: int = 0


class PlannedTask(BaseModel):
    """Planner 输出的单个任务。"""
    title: str
    description: str
    skill_name: str | None = None       # Planner 指定，None 表示不用 skill


@dataclass
class TaskPlan:
    """Planner 阶段完整输出。"""
    tasks: list[PlannedTask] = field(default_factory=list)


@dataclass
class ToolCallRecord:
    """Actor 内单次工具调用记录（用于 ActorResult 和审计）。"""
    tool_name: str
    arguments: dict[str, Any]
    result: str
    is_error: bool = False


@dataclass
class ActorResult:
    """Actor 对单个 atomic task 的执行结果。"""
    task_id: str
    success: bool
    output: str
    tool_calls_made: list[ToolCallRecord] = field(default_factory=list)
    hitl_task_id: str | None = None     # 非 None 表示已暂停等待用户输入
    actor_mode: Literal["text", "tool_use", "skill"] = "text"
    skill_used: str | None = None
    error: str | None = None


@dataclass
class ObserverVerdict:
    """Observer 阶段输出。"""
    done: bool
    summary: str        # 写入 Memory（role=assistant）
    reasoning: str      # 只进日志
