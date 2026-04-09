"""AgentController：统一管理需要访问 agent 内部状态的控制工具。

与 ToolGateway 的区别：
- ToolGateway：外部工具，handler 签名为 (arguments) -> ToolResult
- AgentController：控制工具，handler 持有 agent / task / services，
  且可通过 ControlSignal 修改调用方的执行流。

内置控制工具（按 scope 分组）：
  actor        : request_human_input — 创建 user_input task，阻塞等待用户回答
  observer_plan: submit_plan         — Observer 读取文本计划，创建 atomic 子任务并返回 verdict
  observer     : submit_observation  — 解析观察结果，按需创建 spawn_planner task
  observer_opt : replan              — 取消 session 全部 pending tasks，创建新 plan task（仅 planner agent）
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Any, Callable

from app.runtime.types import PlannedTask
from app.tools.definition import ToolDefinition, ToolResult
from app.tools.tool_decorator import tool_result

if TYPE_CHECKING:
    from app.domain.models.agent import Agent
    from app.domain.models.task import Task
    from app.domain.services.session_service import SessionService
    from app.domain.services.task_service import TaskService
    from app.llm.types import LLMTool

logger = logging.getLogger(__name__)


# ── 执行流信号 ──────────────────────────────────────────────────────────────

class ControlSignal(Enum):
    NONE = "none"                  # 正常返回，调用方继续执行
    BLOCK_FOR_INPUT = "block"      # 已阻塞等待用户输入（blocking 在 handler 内完成）
    TASK_COMPLETE = "complete"     # 任务完成，调用方应退出循环


# ── 控制工具执行结果 ────────────────────────────────────────────────────────

@dataclass
class ControlResult:
    tool_result: ToolResult                           # 返回给 LLM 的内容
    signal: ControlSignal = ControlSignal.NONE
    signal_data: dict[str, Any] = field(default_factory=dict)


# ── 内部描述符 ──────────────────────────────────────────────────────────────

@dataclass
class _ControlToolDef:
    name: str
    schema: ToolDefinition
    handler: Callable[[dict, "Agent", "Task"], ControlResult]
    scope: str = "actor"


# ── submit_plan 工具声明 ────────────────────────────────────────────────────

@tool_result
def submit_plan(
    tasks: Annotated[
        list[PlannedTask],
        (
            "Ordered list of tasks for this turn. Empty list means the goal is already complete. "
            "Each task: "
            "  title: short imperative title; "
            "  description: WHAT to achieve — not HOW, no tool names or arguments; "
            "  skill_name: one of the available skills, or null; "
            "  use_subagent: true if the task should run in an independent sub-agent "
            "(long-running, isolated, or parallelisable work); false for sequential inline tasks. "
            "  inherit_memory: true (default) for sub-agents that need session history; "
            "false for fully isolated tasks with no need for conversation context."
        ),
    ],
) -> ToolResult:
    """Submit the decomposed task plan. Call exactly once per turn."""
    return ToolResult(content="")


# ── AgentController ─────────────────────────────────────────────────────────

class AgentController:
    """管理所有控制工具的注册与分发。

    每个工具注册时携带一个 scope 标签（"actor" / "planner" / "observer"），
    get_llm_schemas(scope=...) 只返回对应 scope 的工具 schema，避免跨组件污染。
    """

    def __init__(
        self,
        task_svc: "TaskService",
        session_svc: "SessionService",
    ) -> None:
        self._task_svc = task_svc
        self._session_svc = session_svc
        self._tools: dict[str, _ControlToolDef] = {}
        self._register_defaults()

    # ── 公开接口 ───────────────────────────────────────────────────────────

    def can_handle(self, tool_name: str) -> bool:
        return tool_name in self._tools

    def get_llm_schemas(self, scope: str | None = None) -> list["LLMTool"]:
        """返回指定 scope 的 LLM schema 列表；scope=None 返回全部。"""
        tools = self._tools.values()
        if scope is not None:
            tools = (t for t in tools if t.scope == scope)
        return [t.schema.to_llm_tool() for t in tools]

    def dispatch(
        self,
        tool_name: str,
        arguments: dict,
        agent: "Agent",
        task: "Task",
    ) -> ControlResult:
        """分发到对应控制工具的 handler 并返回 ControlResult。"""
        if tool_name not in self._tools:
            raise KeyError(f"AgentController: unknown control tool '{tool_name}'")
        return self._tools[tool_name].handler(arguments, agent, task)

    def register(
        self,
        schema: ToolDefinition,
        handler: Callable[[dict, "Agent", "Task"], ControlResult],
        scope: str = "actor",
    ) -> None:
        """注册控制工具（供扩展使用）。"""
        self._tools[schema.name] = _ControlToolDef(schema.name, schema, handler, scope)

    # ── 默认注册 ───────────────────────────────────────────────────────────

    def _register_defaults(self) -> None:
        from app.tools.builtins import request_human_input
        from app.runtime.observer import replan, submit_observation

        self.register(request_human_input, self._handle_request_human_input, scope="actor")
        self.register(submit_plan,         self._handle_submit_plan,         scope="observer_plan")
        self.register(submit_observation,  self._handle_submit_observation,  scope="observer")
        self.register(replan,              self._handle_replan,              scope="observer")

    # ── 内置 handler：request_human_input ──────────────────────────────────

    def _handle_request_human_input(
        self,
        args: dict,
        agent: "Agent",
        task: "Task",
    ) -> ControlResult:
        """创建 user_input task，同步阻塞直到用户回答，恢复 session 后返回。"""
        prompt = args.get("prompt", "")
        context = args.get("context", "")

        hitl_task = self._task_svc.create(
            session_id=task.session_id,
            creator_agent_id=task.assigned_agent_id,
            task_type="user_input",
            title="等待用户输入",
            description=prompt,
            inputs={"prompt": prompt, "context": context, "inline": True},
        )
        self._task_svc.transition(hitl_task.id, "ACTIVE")
        self._session_svc.transition(task.session_id, "WAITING_INPUT")

        answer = ""
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            refreshed = self._task_svc.get(hitl_task.id)
            if refreshed.status == "FINISHED":
                answer = refreshed.result or ""
                break
            time.sleep(1)

        self._session_svc.transition(task.session_id, "RUNNING")

        return ControlResult(
            tool_result=ToolResult(content=answer),
            signal=ControlSignal.BLOCK_FOR_INPUT,
            signal_data={"prompt": prompt, "answer": answer},
        )

    # ── 内置 handler：submit_plan ───────────────────────────────────────────

    def _handle_submit_plan(
        self,
        args: dict,
        agent: "Agent",
        task: "Task",
    ) -> ControlResult:
        """Observer 调用：将文本计划结构化为 Task 记录，批量写入存储，返回 verdict。

        agent 在 Observer 上下文中为 None，统一从 task 取 session_id / assigned_agent_id。
        """
        task_ids: list[str] = []
        titles: list[str] = []

        for spec in args.get("tasks", []):
            inputs: dict = {}
            skill_name = spec.get("skill_name") or None
            if skill_name:
                inputs["skill_name"] = skill_name
            if bool(spec.get("use_subagent", False)):
                inputs["use_subagent"] = True
                inputs["template_name"] = spec.get("subagent_template") or None
                inputs["inherit_memory"] = bool(spec.get("inherit_memory", True))

            t = self._task_svc.create(
                session_id=task.session_id,
                creator_agent_id=task.assigned_agent_id,
                task_type="atomic",
                title=spec.get("title", ""),
                description=spec.get("description", ""),
                inputs=inputs,
            )
            task_ids.append(t.id)
            titles.append(spec.get("title", ""))

        done = len(task_ids) == 0
        summary = (
            f"Planned {len(task_ids)} tasks: {', '.join(titles)}"
            if task_ids
            else "No further tasks needed — goal already achieved."
        )
        logger.debug("AgentController: submit_plan created %d tasks", len(task_ids))
        return ControlResult(
            tool_result=ToolResult(content=summary),
            signal=ControlSignal.NONE,
            signal_data={
                "task_success":       True,
                "task_result":        summary,
                "done":               done,
                "summary":            summary,
                "reasoning":          f"submit_plan: {len(task_ids)} tasks created",
                "needs_user_confirm": False,
            },
        )

    # ── 内置 handler：submit_observation ───────────────────────────────────

    def _handle_submit_observation(
        self,
        args: dict,
        agent: "Agent",
        task: "Task",
    ) -> ControlResult:
        """解析观察结果字段；若需要 spawn planner，立即创建 plan task。

        create_plan_task 在此处触发，而非由 AgentLoop 在收到 verdict 后再判断，
        原因：spawn_planner 是 submit_observation 语义的直接副作用，AgentController 已持有
        TaskService，可在工具处理层完成，AgentLoop 无需关心 spawn_planner 字段。
        """
        task_success = bool(args.get("task_complete", False))
        done = bool(args.get("done", False))
        needs_replan = bool(args.get("spawn_planner", False))
        task_result = args.get("task_result", "")

        if needs_replan and task_success and not done:
            self._task_svc.create_plan_task(
                session_id=task.session_id,
                creator_agent_id=task.assigned_agent_id,
                title="Re-plan: discover follow-up tasks",
                description=task_result,
            )
            logger.debug(
                "AgentController: submit_observation triggered replan for session %s",
                agent.session_id,
            )

        return ControlResult(
            tool_result=ToolResult(content=""),
            signal=ControlSignal.NONE,
            signal_data={
                "task_success":       task_success,
                "task_result":        task_result,
                "done":               done,
                "summary":            args.get("summary", ""),
                "reasoning":          args.get("reasoning", ""),
                "needs_user_confirm": bool(args.get("needs_user_confirm", False)),
            },
        )

    # ── 内置 handler：replan ────────────────────────────────────────────────

    def _handle_replan(
        self,
        args: dict,
        agent: "Agent",
        task: "Task",
    ) -> ControlResult:
        """取消当前 planner agent 本轮产出的所有 PENDING tasks，创建新 plan task。"""
        reason  = args.get("reason", "")
        summary = args.get("summary", "")

        cancelled = self._task_svc.cancel_pending(task.session_id)
        self._task_svc.create_plan_task(
            session_id=task.session_id,
            creator_agent_id=task.assigned_agent_id,
            title="Replan: rebuild task list",
            description=reason,
        )
        logger.debug(
            "AgentController: replan cancelled %d pending tasks, new plan task created for session %s",
            cancelled, task.session_id,
        )
        return ControlResult(
            tool_result=ToolResult(content=f"Cancelled {cancelled} tasks. New plan task created."),
            signal=ControlSignal.NONE,
            signal_data={
                "task_success":       True,
                "task_result":        reason,
                "done":               False,
                "summary":            summary,
                "reasoning":          f"replan: {reason}",
                "needs_user_confirm": False,
            },
        )
