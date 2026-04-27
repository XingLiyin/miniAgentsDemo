"""控制工具 Provider：将 Agent 内部控制逻辑以 Provider 模式暴露为 ToolDefinition。

@control_tool 装饰器：声明工具 schema（name / description / input_schema），
  存入模块级注册表 _CONTROL_SCHEMAS，不绑定 handler 逻辑。

ControlToolProvider：持有 task_svc / session_svc（不污染 CallContext 或 ToolGateway）；
  list_definitions() 返回 ToolDefinition 列表，handler 闭包捕获 self（类比 _map_function_tool）；
  call(tool_name, arguments, ctx) 按工具名分发到 _handle_* 方法。

register_control_tools()：创建 ControlToolProvider 实例，批量调用 registry.register_as_control()。
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Annotated

from app.config.settings import get_settings
from app.runtime.types import PlannedTask
from app.tools.definition import CallContext, ToolDefinition, ToolResult
from app.tools.tool_decorator import _extract_input_schema

if TYPE_CHECKING:
    from app.domain.services.session_service import SessionService
    from app.domain.services.task_service import TaskService
    from app.tools.registry import ToolRegistry


# ── @control_tool 装饰器 ──────────────────────────────────────────────────────

_CONTROL_SCHEMAS: list[ToolDefinition] = []


def control_tool(fn):
    """声明控制工具 schema，存入模块级注册表 _CONTROL_SCHEMAS。

    只提取 schema（name / description / input_schema），不绑定 handler 逻辑。
    handler 实现在 ControlToolProvider 的 _handle_* 方法中，通过 self 访问 task_svc / session_svc。
    """
    from agent_framework._tools import tool as _af_tool
    ft = _af_tool(fn)

    module = sys.modules.get(fn.__module__)
    if hasattr(ft, "input_model") and ft.input_model is not None and module is not None:
        ft.input_model.model_rebuild(_types_namespace=vars(module), raise_errors=False)

    stub = ToolDefinition(
        name=ft.name,
        description=ft.description or "",
        input_schema=_extract_input_schema(ft),
        handler=lambda args, ctx=None: ToolResult(content=""),  # placeholder, never called
    )
    _CONTROL_SCHEMAS.append(stub)
    return stub  # 返回 ToolDefinition stub，调用方可直接用 .to_llm_tool()


# ── 工具 schema 声明 ──────────────────────────────────────────────────────────

@control_tool
def request_human_input(
    prompt: Annotated[str, "The question or instruction to show the user"],
    context: Annotated[str, "Optional background context for the user"] = "",
) -> ToolResult:
    """Pause execution and request input from the human user.
    Use when you need information or a decision that only the user can provide."""


@control_tool
def update_task_metadata(
    title: Annotated[str, "简短的任务标题（≤20字）"],
    description: Annotated[str, "任务描述（≤80字）"],
    session_goal: Annotated[str, "对整个 session 整体目的的理解（≤60字）；首次设定或确认用户方向发生根本性转变时填写，否则留空"] = "",
) -> ToolResult:
    """将生成的标题、描述和 session goal 保存。调用一次后任务即完成。"""


@control_tool
def replan(
    reason: Annotated[str, "The reason for replanning"],
    summary: Annotated[str, "Concise summary of this replanning action (1-3 sentences)"] = "",
) -> ToolResult:
    """Trigger a replan: cancel all pending tasks and create a new plan task."""


@control_tool
def submit_task_assessment(
    task_outcome: Annotated[
        str,
        "Outcome of the current task: "
        "'success' if completed successfully; "
        "'failed' if it could not be completed (system decides whether to retry); "
        "'active' if this turn made progress but the task is not yet complete (task re-queued for another actor turn); "
        "'needs_user_input' if completion cannot be determined without user confirmation.",
    ],
    task_result: Annotated[str, "Complete description of current progress: what was accomplished, what was produced or modified, what remains, and why the task could not be completed if applicable. This field is written directly to memory and read by the next actor turn — be thorough, not a one-liner."],
    task_reviews: Annotated[
        list,
        "Optional reviews for FINISHED/PENDING sibling tasks in the session. "
        "Each entry: task_title (str, exact match from task list), "
        "review_status ('confirmed'|'reopen'|'skip'), reasoning (str, required). "
        "Omit tasks you have no information about. "
        "Leave empty if no session task list was provided.",
    ] = [],
    next_step_hint: Annotated[
        str,
        "Optional. If there are obvious risks, blockers, or important concerns the next actor turn should be aware of, describe them here. Leave empty if nothing notable.",
    ] = "",
) -> ToolResult:
    """Submit your assessment of the current task's execution result, and optionally review sibling tasks in one call."""


@control_tool
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
            "  use_subagent: true if the task should run in an independent sub-agent; "
            "  subagent_template: template name for the sub-agent (e.g. 'planner'); empty uses system default; "
            "  inherit_memory: true (default) for sub-agents that need session history."
        ),
    ],
) -> ToolResult:
    """Submit the decomposed task plan. Call exactly once per turn. Each task: title (str, short imperative), description (str, WHAT not HOW), use_subagent (bool), inherit_memory (bool, default true), skill_name (str or null), user_prompt (str or empty)."""


@control_tool
def submit_task(
    title: Annotated[str, "Short imperative title for the task (≤20 chars)"],
    description: Annotated[str, "WHAT to achieve — not HOW, no tool names or arguments (≤80 chars)"],
    skill_name: Annotated[str, "Skill to assign to the task, or empty string if none"] = "",
    use_subagent: Annotated[bool, "True if the task should run in an independent sub-agent"] = False,
    subagent_template: Annotated[str, "Template name for the sub-agent (e.g. 'planner'); empty uses system default"] = "",
    inherit_memory: Annotated[bool, "True (default) for sub-agents that need session history"] = True,
    user_prompt: Annotated[str, "The user prompt that triggered this task, or empty"] = "",
) -> ToolResult:
    """Create a single new task in the current session. The current task continues running.
    Use when you need to delegate work to another task/agent without replacing the current plan."""



# ── ControlToolProvider ───────────────────────────────────────────────────────

class ControlToolProvider:
    """控制工具 Provider，类比 mcp_base._MCPProviderBase。

    - __init__ 持有 task_svc / session_svc
    - list_definitions() 返回 ToolDefinition 列表，handler 闭包捕获 self
      （类比 _MCPProviderBase._map_function_tool）
    - call() 按 tool_name 分发到 _handle_* 方法，方法内可访问 self._task_svc / self._session_svc
    """

    def __init__(
        self,
        task_svc: "TaskService",
        session_svc: "SessionService",
    ) -> None:
        self._task_svc = task_svc
        self._session_svc = session_svc

    # ── ToolProvider Protocol ──────────────────────────────────────────────────

    def list_definitions(self) -> list[ToolDefinition]:
        """返回控制工具定义列表。

        schema 全部来自模块级 _CONTROL_SCHEMAS（由 @control_tool 在导入时填充）。
        handler 闭包捕获 provider_ref（self），类比 _MCPProviderBase._map_function_tool。
        """
        return [self._map_schema(s) for s in _CONTROL_SCHEMAS]

    def call(self, tool_name: str, arguments: dict, ctx: CallContext | None = None) -> ToolResult:
        """按 tool_name 分发到对应 _handle_* 方法。"""
        method = getattr(self, f"_handle_{tool_name}", None)
        if method is None:
            from app.common.errors import AppError
            raise AppError("TOOL_NOT_FOUND", f"No handler for control tool: {tool_name!r}")
        return method(arguments, ctx)

    def _map_schema(self, schema: ToolDefinition) -> ToolDefinition:
        """将 schema stub 包装为真实 ToolDefinition，类比 _MCPProviderBase._map_function_tool。

        handler 闭包捕获 provider_ref，调用时派发到 provider.call()。
        """
        provider_ref = self
        tool_name = schema.name

        def handler(arguments: dict, ctx: CallContext | None = None) -> ToolResult:
            return provider_ref.call(tool_name, arguments, ctx)

        return ToolDefinition(
            name=schema.name,
            description=schema.description,
            input_schema=schema.input_schema,
            handler=handler,
        )

    # ── handler 方法 ──────────────────────────────────────────────────────────

    def _handle_request_human_input(self, args: dict, ctx: CallContext | None) -> ToolResult:
        from app.runtime.hitl_store import get_hitl_store
        from app.common.sse_bus import get_sse_bus
        from app.common.utils import now_iso

        prompt     = args.get("prompt", "")
        session_id = ctx.session_id if ctx else ""
        agent_id   = ctx.agent_id   if ctx else ""

        self._session_svc.transition(session_id, "WAITING_INPUT")
        try:
            get_sse_bus().push(session_id, {"type": "message", "role": "assistant",
                                            "content": prompt, "created_at": now_iso()})
            get_sse_bus().push(session_id, {"type": "waiting_input", "prompt": prompt,
                                            "input_type": "user_input", "task_title": "等待用户输入"})
        except Exception:
            pass

        answer = get_hitl_store().wait(session_id, agent_id, prompt, "user_input")
        self._session_svc.transition(session_id, "RUNNING")
        return ToolResult(content=answer)

    def _handle_submit_plan(self, args: dict, ctx: CallContext | None) -> ToolResult:
        task = ctx.task if ctx else None
        task_ids: list[str] = []
        titles:   list[str] = []

        for spec in args.get("tasks", []):
            inputs: dict = {}
            skill_name = spec.get("skill_name") or None
            if skill_name:
                inputs["skill_name"] = skill_name
            if bool(spec.get("use_subagent", False)):
                inputs["use_subagent"] = True
                subagent_template = spec.get("subagent_template") or None
                if subagent_template:
                    inputs["subagent_template"] = subagent_template
                inputs["inherit_memory"] = bool(spec.get("inherit_memory", True))
            t = self._task_svc.create(
                session_id=task.session_id if task else "",
                creator_agent_id=task.assigned_agent_id if task else "",
                user_prompt=spec.get("user_prompt", ""),
                title=spec.get("title", ""),
                description=spec.get("description", ""),
                inputs=inputs,
                parent_task_id=task.id if task else None,
            )
            task_ids.append(t.id)
            titles.append(spec.get("title", ""))

        result_text = (
            f"Planned {len(task_ids)} tasks: {', '.join(titles)}"
            if task_ids else "No further tasks needed — goal already achieved."
        )
        if task is not None:
            task.actor_done = True
        return ToolResult(content=result_text)

    def _handle_replan(self, args: dict, ctx: CallContext | None) -> ToolResult:
        task    = ctx.task if ctx else None
        reason  = args.get("reason", "")
        summary = args.get("summary", "")

        cancelled = self._task_svc.cancel_pending(task.session_id if task else "")
        settings  = get_settings()
        self._task_svc.create(
            session_id=task.session_id if task else "",
            creator_agent_id=task.assigned_agent_id if task else "",
            user_prompt=(task.user_prompt if task else "") or "",
            title=f"Replan For: {reason}",
            description=f"{summary}\n\n请基于最新情况重新制定计划。",
            inputs={"use_subagent": True, "inherit_memory": True,
                    "subagent_template": settings.default_planner_template_name},
        )
        if task is not None:
            task.actor_outcome     = "success"
            task.actor_result      = reason
            task.proceed_to_review = False
            self._task_svc.finish(task.id, result=reason)
            task.status = "FINISHED"
        return ToolResult(content=f"Cancelled {cancelled} tasks. New plan task created.")

    def _handle_submit_task_assessment(self, args: dict, ctx: CallContext | None) -> ToolResult:
        task = ctx.task if ctx else None
        task_outcome = args.get("task_outcome", "failed")
        if task_outcome not in ("success", "failed", "active", "needs_user_input"):
            task_outcome = "failed"
        task_result     = args.get("task_result", "")
        next_step_hint  = args.get("next_step_hint", "")
        actor_result    = f"{task_result}\n\n下一步建议：{next_step_hint}" if next_step_hint else task_result

        if task is not None:
            task.actor_outcome = task_outcome
            task.actor_result  = actor_result
            task.proceed_to_review = True
            outputs = {"progress_text": task.progress_text} if getattr(task, "progress_text", None) else None

            if task_outcome == "success":
                self._task_svc.finish(task.id, result=task_result, outputs=outputs)
                task.status = "FINISHED"
            elif task_outcome == "failed":
                self._task_svc.fail(task.id, error=task_result)
                task.status = "FAILED"
            elif task_outcome == "active":
                # Task made progress but is not done; re-queue for another actor turn
                self._task_svc.transition(task.id, "PENDING")
                task.status = "PENDING"
            else:  # needs_user_input
                task_outcome, task_result = self._confirm_with_user(task, task_result, outputs)
                task.actor_outcome = task_outcome
                task.actor_result  = task_result
                task.status = "FINISHED" if task_outcome == "success" else "FAILED"

        review_msg = ""
        task_reviews = args.get("task_reviews") or []
        if task_reviews and ctx:
            review_msg = self._apply_reviews(task_reviews, ctx)

        return ToolResult(content=f"Assessment recorded: outcome={task_outcome}. {task_result}{review_msg}")

    def _confirm_with_user(self, task, task_result: str, outputs) -> tuple[str, str]:
        """需要用户确认任务完成状态时阻塞等待，返回 (outcome, result)。"""
        from app.runtime.hitl_store import get_hitl_store
        from app.common.sse_bus import get_sse_bus
        from app.common.utils import now_iso

        session_id = task.session_id
        agent_id   = task.assigned_agent_id
        prompt = (
            f"任务「{task.title}」已执行，但系统无法自动判定完成状态。\n\n"
            f"执行结果：\n{task_result or '（无输出）'}\n\n"
            "请确认任务是否完成，或补充说明以便 Agent 重新规划。"
        )

        self._session_svc.transition(session_id, "WAITING_INPUT")
        try:
            get_sse_bus().push(session_id, {"type": "message", "role": "assistant",
                                            "content": prompt, "created_at": now_iso()})
            get_sse_bus().push(session_id, {"type": "waiting_input", "prompt": prompt,
                                            "input_type": "task_completion_confirm",
                                            "task_title": "请确认任务完成状态"})
        except Exception:
            pass

        answer = get_hitl_store().wait(session_id, agent_id, prompt, "task_completion_confirm")
        self._session_svc.transition(session_id, "RUNNING")

        if answer.startswith("用户已确认任务完成"):
            self._task_svc.finish(task.id, result=task_result, outputs=outputs)
            return "success", task_result
        else:
            prefix = "用户表示任务未完成，请重试。用户补充说明："
            feedback = answer[len(prefix):] if answer.startswith(prefix) else answer
            self._task_svc.fail(task.id, error=feedback or "用户确认任务未完成")
            return "failed", feedback or "用户确认任务未完成"

    def _handle_update_task_metadata(self, args: dict, ctx: CallContext | None) -> ToolResult:
        task           = ctx.task if ctx else None
        target_task_id = task.settings.get("target_task_id", "") if task else ""
        title          = str(args.get("title", "")).strip()
        description    = str(args.get("description", "")).strip()
        session_goal   = str(args.get("session_goal", "")).strip()

        session_id = ""
        if target_task_id:
            try:
                target = self._task_svc.get(target_task_id)
                session_id = target.session_id
                if title:
                    target.title = title
                if description:
                    target.description = description
                self._task_svc.save(target)
                try:
                    from app.common.sse_bus import get_sse_bus
                    get_sse_bus().push(session_id,
                                       {"type": "task_updated", "task": target.to_dict()})
                except Exception:
                    pass
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "control_tools: failed to update metadata for task %s", target_task_id)

        if session_goal and session_id:
            try:
                session = self._session_svc.get(session_id)
                session.goal = session_goal
                self._session_svc.save(session)
                try:
                    from app.common.sse_bus import get_sse_bus
                    get_sse_bus().push(session_id, {"type": "session_goal_updated", "goal": session_goal})
                except Exception:
                    pass
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "control_tools: failed to update session goal for session %s", session_id)

        if task is not None:
            task.actor_done    = True
            task.actor_outcome = "success"
            task.actor_result  = "metadata updated"
            task.actor_summary = ""
        return ToolResult(content="ok")

    def _handle_submit_task(self, args: dict, ctx: CallContext | None) -> ToolResult:
        task = ctx.task if ctx else None
        inputs: dict = {}
        skill_name = args.get("skill_name") or None
        if skill_name:
            inputs["skill_name"] = skill_name
        if bool(args.get("use_subagent", False)):
            inputs["use_subagent"] = True
            inputs["inherit_memory"] = bool(args.get("inherit_memory", True))
            subagent_template = args.get("subagent_template") or None
            if subagent_template:
                inputs["subagent_template"] = subagent_template
        t = self._task_svc.create(
            session_id=task.session_id if task else "",
            creator_agent_id=task.assigned_agent_id if task else "",
            user_prompt=args.get("user_prompt", ""),
            title=args.get("title", ""),
            description=args.get("description", ""),
            inputs=inputs,
            parent_task_id=task.id if task else None,
        )
        if task is not None:
            self._task_svc.transition(task.id, "SUSPENDED")
            task.status = "SUSPENDED"
            task.actor_done = True
        return ToolResult(content=f"Task created: id={t.id}, title={t.title!r}")

    def _apply_reviews(self, reviews: list, ctx: "CallContext") -> str:
        """Apply task reviews; returns a summary string."""
        _log = logging.getLogger(__name__)
        session_id = ctx.session_id if ctx else ""
        agent_id   = ctx.agent_id   if ctx else ""
        current_id = ctx.task.id    if ctx and ctx.task else ""

        session_tasks = self._task_svc.list_by_session(session_id)
        reviewable = {
            t.title: t for t in session_tasks
            if t.assigned_agent_id == agent_id
            and t.status in ("FINISHED", "PENDING")
            and t.id != current_id
        }

        applied: list[str] = []
        for item in reviews:
            if not isinstance(item, dict):
                continue
            title         = item.get("task_title", "")
            review_status = item.get("review_status", "")
            reasoning     = str(item.get("reasoning", ""))

            if not title or review_status not in ("confirmed", "reopen", "skip"):
                continue

            matched = reviewable.get(title)
            if matched is None:
                _log.warning("task_reviews: no reviewable task with title %r, skipping", title)
                continue

            try:
                if review_status == "reopen":
                    self._task_svc.reopen(matched.id)
                    _log.info("Task %s reopened by observer: %s", matched.id, reasoning)
                elif review_status == "skip":
                    self._task_svc.finish(matched.id, result=reasoning or "Completed indirectly per observer.")
                    _log.info("Task %s skipped by observer: %s", matched.id, reasoning)
            except Exception as e:
                _log.warning("task_reviews: failed to apply review for %s: %s", matched.id, e)

            applied.append(f"{title} → {review_status}")

        return f" Reviews applied: {', '.join(applied)}" if applied else ""



# ── 注册函数 ──────────────────────────────────────────────────────────────────

def register_control_tools(
    registry: "ToolRegistry",
    task_svc: "TaskService",
    session_svc: "SessionService",
) -> None:
    """创建 ControlToolProvider 并将所有控制工具注册到 ToolRegistry。"""
    provider = ControlToolProvider(task_svc=task_svc, session_svc=session_svc)
    for tool_def in provider.list_definitions():
        registry.register_as_control(tool_def)
