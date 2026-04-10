"""Agent Loop v2 阶段间数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

TaskOutcome = Literal["success", "failed", "needs_user_input"]
ReviewStatus = Literal["confirmed", "reopen", "skip"]

if TYPE_CHECKING:
    from app.domain.models.task import Task
    from app.llm.types import LLMTool


@dataclass
class ContextResource:
    """Agent 可用资源（工具或技能），统一承载检索结果。"""
    name: str
    description: str
    kind: Literal["tool", "skill"]
    llm_tool: "LLMTool | None" = None   # kind="tool" 时有效，传给 LLM function calling


@dataclass
class ReasoningContext:
    """Reasoner 阶段输出，作为 Actor 的输入。"""
    # Memory 层
    goal: str
    recent_messages: list[dict]
    summary_text: str
    blackboard_snippets: list[str]
    # Agent 身份（由 Reasoner 从 Agent 对象提取）
    soul: str = ""                       # agent.soul_md or agent.system_prompt
    role: str = ""                       # agent.role_md
    skill_instructions: str = ""         # 当前 task 的 skill instructions（plan/act 均可有）
    # 检索结果：工具（kind="tool"）+ 可用技能（kind="skill"）
    resources: list[ContextResource] = field(default_factory=list)
    # Observer 阶段可用工具（由 Reasoner 按 agent.tool_list 过滤后填充）
    observer_tools: list["LLMTool"] = field(default_factory=list)
    # 当前待执行 task（plan or atomic，统一字段）
    current_task: "Task | None" = None
    # Token 估算（给 guard 用）
    token_estimate: int = 0


class PlannedTask(BaseModel):
    """Planner 输出的单个任务。"""
    title: str
    description: str
    skill_name: str | None = None       # Planner 指定，None 表示不用 skill
    use_subagent: bool = False          # Planner 指定，True 表示由独立 sub-agent 执行
    subagent_template: str | None = None  # 可选的 sub-agent template（如 planner 专用模板）
    inherit_memory: bool = True         # sub-agent 是否继承 session 历史记忆



@dataclass
class ToolCallRecord:
    """Actor 内单次工具调用记录（用于 ActorResult 和审计）。"""
    tool_name: str
    arguments: dict[str, Any]
    result: str
    is_error: bool = False


@dataclass
class ConversationTurn:
    """Actor 内单轮 LLM 交互记录，完整保留给 Observer 评估。"""
    round: int
    messages_sent: list[Any]           # list[LLMMessage]，避免循环导入用 Any
    llm_text: str                       # 本轮 LLM 文本回复
    tool_calls: list[ToolCallRecord] = field(default_factory=list)


@dataclass
class ActorResult:
    """Actor 对单个 task 的执行结果（plan 或 atomic 均适用）。"""
    task_id: str
    success: bool                                                   # 执行层成功（未抛异常）
    output: str                                                     # 最后一轮 LLM 文本
    tool_calls_made: list[ToolCallRecord] = field(default_factory=list)
    conversation_turns: list[ConversationTurn] = field(default_factory=list)
    task_outputs: dict[str, Any] = field(default_factory=dict)     # 额外输出（如 planned_task_ids）
    actor_mode: Literal["text", "tool_use", "skill", "plan"] = "text"
    plan_task_count: int | None = None  # plan 模式专用；0 = 空计划（目标已达成）
    skill_used: str | None = None
    error: str | None = None


@dataclass
class TaskReview:
    """Observer 对单个任务的复核判断。"""
    task_id: str
    current_status: str      # "FINISHED" | "PENDING"
    review_status: ReviewStatus  # confirmed | reopen | skip
    reasoning: str           # 判断依据（必填）


@dataclass
class ObserverVerdict:
    """Observer 阶段输出。"""
    task_outcome: TaskOutcome          # success / failed / needs_user_input
    task_result: str                   # 写入 task.result 的内容
    summary: str                       # 写入 Memory（role=assistant）
    task_reviews: list[TaskReview] = field(default_factory=list)
