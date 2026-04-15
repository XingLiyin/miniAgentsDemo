"""AgentController：统一管理需要访问 agent 内部状态的控制工具。

与 ToolGateway 的区别：
- ToolGateway：外部工具，handler 签名为 (arguments) -> ToolResult
- AgentController：控制工具，handler 持有 agent / task / services，
  且可通过 ControlSignal 修改调用方的执行流。

内置控制工具（按 scope 分组）：
  actor        : request_human_input    — 阻塞当前线程等待用户回答（HitlStore）
  observer_plan: submit_plan            — 创建 atomic 子任务 + 评估当前 plan task（proceed_to_review=False）
  observer     : submit_task_assessment — 评估当前 atomic task，继续第二轮复核（proceed_to_review=True）
  observer     : replan                 — 取消 session 全部 pending tasks，创建新 plan task（proceed_to_review=False）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Any, Callable

from app.config.settings import get_settings
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


# ── request_human_input 工具说明 ───────────────────────────────────────────────────

@tool_result
def request_human_input(
    prompt: Annotated[str, "The question or instruction to show the user"],
    context: Annotated[str, "Optional background context for the user"] = "",
) -> ToolResult:
    """Pause execution and request input from the human user.
    Use when you need information or a decision that only the user can provide."""
    import json
    output = json.dumps({"prompt": prompt, "context": context}, ensure_ascii=False)
    return ToolResult(content=output)   # 触发信号，Actor 特殊处理，函数体不执行


# ── replan 工具说明 ───────────────────────────────────────────────────

@tool_result
def update_task_metadata(
    title: Annotated[str, "简短的任务标题（≤20字）"],
    description: Annotated[str, "任务描述（≤80字）"],
) -> ToolResult:
    """将生成的标题和描述保存到目标任务。调用一次后任务即完成。"""
    return ToolResult(content="ok")


@tool_result
def replan(
    reason: Annotated[str, "The reason for replanning"],
    summary: Annotated[str, "Concise summary of this replanning action (1-3 sentences), describe what has been done for this task"] = "",
) -> ToolResult:
    """Pause execution and request input from the human user.
    Use when you need information or a decision that only the user can provide."""
    import json
    output = json.dumps({"reason": reason, "summary": summary}, ensure_ascii=False)
    return ToolResult(content=output)   # 触发信号，Actor 特殊处理，函数体不执行



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
            "  user_prompt: the user prompt that triggered this task, or empty if none. "
            "  skill_name: one of the available skills, or null; "
            "  use_subagent: true if the task should run in an independent sub-agent "
            "(long-running, isolated, or parallelisable work); false for sequential inline tasks. "
            "  inherit_memory: true (default) for sub-agents that need session history; "
            "false for fully isolated tasks with no need for conversation context."
        ),
    ],
    task_outcome: Annotated[
        str,
        "Outcome of the current plan task: "
        "'success' if planning completed successfully (even if tasks=[]); "
        "'failed' if planning itself could not be completed.",
    ] = "success",
    task_result: Annotated[
        str,
        "Brief description of what the plan covers, or why planning failed.",
    ] = "",
    summary: Annotated[
        str,
        "Concise summary of this planning turn (1-3 sentences), written to agent memory.",
    ] = "",
) -> ToolResult:
    """Submit the decomposed task plan and assessment of the current planning turn. Call exactly once per turn."""
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
        from app.runtime.observer import submit_task_assessment

        self.register(request_human_input,    self._handle_request_human_input,    scope="actor")
        self.register(update_task_metadata,   self._handle_update_task_metadata,   scope="actor")
        self.register(submit_plan,            self._handle_submit_plan,            scope="observer")
        self.register(submit_task_assessment, self._handle_submit_task_assessment, scope="observer")
        self.register(replan,                 self._handle_replan,                 scope="observer")

    # ── 内置 handler：request_human_input ──────────────────────────────────

    def _handle_request_human_input(
        self,
        args: dict,
        agent: "Agent",
        task: "Task",
    ) -> ControlResult:
        """阻塞等待用户回答，恢复 session 后返回。

        不创建 user_input task；通过 HitlStore + threading.Event 阻塞当前工作线程。
        """
        from app.runtime.hitl_store import get_hitl_store
        from app.runtime.sse_bus import get_sse_bus

        prompt = args.get("prompt", "")
        session_id = task.session_id
        agent_id = task.assigned_agent_id

        self._session_svc.transition(session_id, "WAITING_INPUT")
        try:
            from app.common.utils import now_iso
            get_sse_bus().push(session_id, {
                "type": "message",
                "role": "assistant",
                "content": prompt,
                "created_at": now_iso(),
            })
            get_sse_bus().push(session_id, {
                "type": "waiting_input",
                "prompt": prompt,
                "input_type": "user_input",
                "task_title": "等待用户输入",
            })
        except Exception:
            pass

        answer = get_hitl_store().wait(session_id, agent_id, prompt, "user_input")
        self._session_svc.transition(session_id, "RUNNING")

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
        """Observer 调用：将计划结构化为 Task 记录并批量写入，同时收集当前 plan task 的评估字段。

        agent 在 Observer 上下文中为 None，统一从 task 取 session_id / assigned_agent_id。
        proceed_to_review=False：任务刚创建，无需进入第二轮复核。
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
                user_prompt=spec.get("user_prompt", ""),
                title=spec.get("title", ""),
                description=spec.get("description", ""),
                inputs=inputs,
            )
            task_ids.append(t.id)
            titles.append(spec.get("title", ""))

        # assessment 字段：LLM 提供则优先使用，否则自动生成
        task_outcome = args.get("task_outcome", "success")
        if task_outcome not in ("success", "failed", "needs_user_input"):
            task_outcome = "success"
        default_result = (
            f"Planned {len(task_ids)} tasks: {', '.join(titles)}"
            if task_ids
            else "No further tasks needed — goal already achieved."
        )
        task_result = args.get("task_result") or default_result
        summary     = args.get("summary")     or default_result

        logger.debug("AgentController: submit_plan created %d tasks", len(task_ids))
        return ControlResult(
            tool_result=ToolResult(content=default_result),
            signal=ControlSignal.NONE,
            signal_data={
                "task_outcome":      task_outcome,
                "task_result":       task_result,
                "summary":           summary,
                "proceed_to_review": False,
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
        user_prompt = args.get("user_prompt", "")

        cancelled = self._task_svc.cancel_pending(task.session_id)
        settings = get_settings()
        self._task_svc.create(
            session_id=task.session_id,
            creator_agent_id=task.assigned_agent_id,
            user_prompt=user_prompt,
            title=f"Replan For: {reason}",
            description=f"{summary}\n\n请基于最新情况重新制定计划。",
            inputs={
                "use_subagent": True,
                "inherit_memory": True,
                "subagent_template": settings.default_planner_template_name,
            },
        )
        logger.debug(
            "AgentController: replan cancelled %d pending tasks, new plan task created for session %s",
            cancelled, task.session_id,
        )
        return ControlResult(
            tool_result=ToolResult(content=f"Cancelled {cancelled} tasks. New plan task created."),
            signal=ControlSignal.NONE,
            signal_data={
                "task_outcome":      "success",
                "task_result":       reason,
                "summary":           summary,
                "proceed_to_review": False,
            },
        )

    # ── 内置 handler：submit_task_assessment ───────────────────────────────

    def _handle_submit_task_assessment(
        self,
        args: dict,
        agent: "Agent",
        task: "Task",
    ) -> ControlResult:
        """解析当前 task 评估结果，标记 proceed_to_review=True 以触发第二轮复核。"""
        task_outcome = args.get("task_outcome", "failed")
        if task_outcome not in ("success", "failed", "needs_user_input"):
            task_outcome = "failed"

        return ControlResult(
            tool_result=ToolResult(content=""),
            signal=ControlSignal.NONE,
            signal_data={
                "task_outcome":      task_outcome,
                "task_result":       args.get("task_result", ""),
                "summary":           args.get("summary", ""),
                "proceed_to_review": True,
            },
        )

    # ── 内置 handler：update_task_metadata ─────────────────────────────────

    def _handle_update_task_metadata(
        self,
        args: dict,
        agent: "Agent",
        task: "Task",
    ) -> ControlResult:
        """将生成的 title / description 写入目标 task，并推 SSE 通知。

        目标 task id 从当前 task.settings["target_task_id"] 读取，
        由 metadata_filler 子代理在创建时注入。
        """
        target_task_id = task.settings.get("target_task_id", "")
        title = str(args.get("title", "")).strip()
        description = str(args.get("description", "")).strip()

        if target_task_id:
            try:
                target = self._task_svc.get(target_task_id)
                if title:
                    target.title = title
                if description:
                    target.description = description
                self._task_svc.save(target)
                try:
                    from app.runtime.sse_bus import get_sse_bus
                    get_sse_bus().push(target.session_id, {"type": "task_updated", "task": target.to_dict()})
                except Exception:
                    pass
                logger.debug(
                    "AgentController: updated metadata for task %s: title=%r", target_task_id, title
                )
            except Exception:
                logger.exception("AgentController: failed to update metadata for task %s", target_task_id)

        return ControlResult(
            tool_result=ToolResult(content="ok"),
            signal=ControlSignal.TASK_COMPLETE,
            signal_data={"task_outcome": "success", "task_result": "metadata updated", "summary": ""},
        )
