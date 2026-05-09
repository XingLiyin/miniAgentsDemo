"""控制工具：将 Agent 内部控制逻辑暴露为 ToolDefinition。

@control_tool 装饰器：同时声明 schema 和实现，直接生成完整 ToolDefinition 存入 _CONTROL_SCHEMAS。
  schema 从 Annotated 注解提取，task_svc / session_svc / ctx 不计入 schema。
  handler 闭包在调用时从 _svc 读取服务（lazy）；register_control_tools() 负责在注册前填入。

register_control_tools()：填充 _svc，将 _CONTROL_SCHEMAS 批量注册到 ToolRegistry。
"""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, Annotated

from app.config.settings import get_settings
from app.runtime.types import PlannedTask
from app.tools.types import CallContext, ToolDefinition, ToolResult
from app.tools.utils import extract_input_schema, make_tool_handler

if TYPE_CHECKING:
    from app.domain.models.task import Task
    from app.domain.services.session_service import SessionService
    from app.domain.services.task_service import TaskService


# ── @control_tool 装饰器 ──────────────────────────────────────────────────────

_CONTROL_SCHEMAS: list[ToolDefinition] = []

# schema 提取時跳過的參數名（运行时注入，不暴露给 LLM）
_SKIP: frozenset[str] = frozenset({"ctx", "task_svc", "session_svc"})

# 运行时服务引用，由 register_control_tools() 在启动时填入
_svc: dict = {}


def control_tool(fn):
    """声明控制工具 schema + 实现，生成完整 ToolDefinition 存入 _CONTROL_SCHEMAS。

    handler 闭包使用 stripped signature（去掉 task_svc/session_svc），
    调用时从 _svc 注入服务，ctx 由 ToolGateway 注入。
    """
    orig_sig = inspect.signature(fn)
    stripped = orig_sig.replace(parameters=[
        p for n, p in orig_sig.parameters.items()
        if n not in {"task_svc", "session_svc"}
    ])

    def _fn(*args, **kwargs):
        return fn(*args, **kwargs, **_svc)

    _fn.__signature__ = stripped

    tool_def = ToolDefinition(
        name=fn.__name__,
        description=(fn.__doc__ or "").strip(),
        input_schema=extract_input_schema(fn, exclude=_SKIP),
        handler=make_tool_handler(_fn),
    )
    _CONTROL_SCHEMAS.append(tool_def)
    return tool_def


# ── 模块级辅助函数 ─────────────────────────────────────────────────────────────

def _confirm_with_user(task: Task, task_result: str, outputs, *, task_svc: "TaskService", session_svc: "SessionService") -> tuple[str, str]:
    from app.storage.file.hitl_store import get_hitl_store
    from app.common.sse_bus import get_sse_bus
    from app.common.utils import now_iso

    session_id = task.session_id
    agent_id   = task.assigned_agent_id
    prompt = (
        f"任务「{task.title}」已执行，但系统无法自动判定完成状态。\n\n"
        f"执行结果：\n{task_result or '（无输出）'}\n\n"
        "请确认任务是否完成，或补充说明以便 Agent 重新规划。"
    )

    session_svc.transition(session_id, "WAITING_INPUT")
    try:
        get_sse_bus().push(session_id, {"type": "message", "role": "assistant",
                                        "content": prompt, "created_at": now_iso()})
        get_sse_bus().push(session_id, {"type": "waiting_input", "prompt": prompt,
                                        "input_type": "task_completion_confirm",
                                        "task_title": "请确认任务完成状态"})
    except Exception:
        pass

    answer = get_hitl_store().wait(session_id, agent_id, prompt, "task_completion_confirm")
    session_svc.transition(session_id, "RUNNING")

    if answer.startswith("用户已确认任务完成"):
        task_svc.finish(task.id, result=task_result, outputs=outputs, session_id=task.session_id)
        return "success", task_result
    else:
        prefix = "用户表示任务未完成，请重试。用户补充说明："
        feedback = answer[len(prefix):] if answer.startswith(prefix) else answer
        task_svc.fail(task.id, error=feedback or "用户确认任务未完成", session_id=task.session_id)
        return "failed", feedback or "用户确认任务未完成"


def _apply_reviews(reviews: list, ctx: CallContext, *, task_svc: "TaskService") -> str:
    _log = logging.getLogger(__name__)
    session_id = ctx.session_id if ctx else ""
    agent_id   = ctx.agent_id   if ctx else ""
    current_id = ctx.task.id    if ctx and ctx.task else ""

    session_tasks = task_svc.list_by_session(session_id)
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
                task_svc.reopen(matched.id, matched.session_id)
                _log.info("Task %s reopened by observer: %s", matched.id, reasoning)
            elif review_status == "skip":
                task_svc.finish(matched.id, result=reasoning or "Completed indirectly per observer.",
                                session_id=matched.session_id)
                _log.info("Task %s skipped by observer: %s", matched.id, reasoning)
        except Exception as e:
            _log.warning("task_reviews: failed to apply review for %s: %s", matched.id, e)

        applied.append(f"{title} → {review_status}")

    return f" Reviews applied: {', '.join(applied)}" if applied else ""


# ── 工具 schema + 实现 ────────────────────────────────────────────────────────

@control_tool
def request_human_input(
    prompt: Annotated[str, "The question or instruction to show the user"],
    context: Annotated[str, "Optional background context for the user"] = "",
    *,
    ctx: CallContext | None = None,
    task_svc: "TaskService" = None,
    session_svc: "SessionService" = None,
) -> ToolResult:
    """Pause execution and request input from the human user.
    Use when you need information or a decision that only the user can provide."""
    from app.storage.file.hitl_store import get_hitl_store
    from app.common.sse_bus import get_sse_bus
    from app.common.utils import now_iso

    session_id = ctx.session_id if ctx else ""
    agent_id   = ctx.agent_id   if ctx else ""

    session_svc.transition(session_id, "WAITING_INPUT")
    try:
        get_sse_bus().push(session_id, {"type": "message", "role": "assistant",
                                        "content": prompt, "created_at": now_iso()})
        get_sse_bus().push(session_id, {"type": "waiting_input", "prompt": prompt,
                                        "input_type": "user_input", "task_title": "等待用户输入"})
    except Exception:
        pass

    answer = get_hitl_store().wait(session_id, agent_id, prompt, "user_input")
    session_svc.transition(session_id, "RUNNING")
    return ToolResult(content=answer)


@control_tool
def update_task_metadata(
    title: Annotated[str, "简短的任务标题（≤20字）"],
    description: Annotated[str, "任务描述（≤80字）"],
    session_goal: Annotated[str, "对整个 session 整体目的的理解（≤60字）；首次设定或确认用户方向发生根本性转变时填写，否则留空"] = "",
    *,
    ctx: CallContext | None = None,
    task_svc: "TaskService" = None,
    session_svc: "SessionService" = None,
) -> ToolResult:
    """将生成的标题、描述和 session goal 保存。调用一次后任务即完成。"""
    task           = ctx.task if ctx else None
    target_task_id = task.settings.get("target_task_id", "") if task else ""

    session_id = ""
    if target_task_id:
        try:
            target = task_svc.get(target_task_id, ctx.session_id if ctx else None)
            session_id = target.session_id
            if title:
                target.title = title
            if description:
                target.description = description
            task_svc.save(target)
            try:
                from app.common.sse_bus import get_sse_bus
                get_sse_bus().push(session_id, {"type": "task_updated", "task": target.to_dict()})
            except Exception:
                pass
        except Exception:
            logging.getLogger(__name__).exception(
                "control_tools: failed to update metadata for task %s", target_task_id)

    if session_goal and session_id:
        try:
            session = session_svc.get(session_id)
            session.goal = session_goal
            session_svc.save(session)
            try:
                from app.common.sse_bus import get_sse_bus
                get_sse_bus().push(session_id, {"type": "session_goal_updated", "goal": session_goal})
            except Exception:
                pass
        except Exception:
            logging.getLogger(__name__).exception(
                "control_tools: failed to update session goal for session %s", session_id)

    if task is not None:
        task.actor_done    = True
        task.actor_outcome = "success"
        task.actor_result  = "metadata updated"
        task.actor_summary = ""
    return ToolResult(content="ok")


@control_tool
def replan(
    reason: Annotated[str, "The reason for replanning"],
    summary: Annotated[str, "Concise summary of this replanning action (1-3 sentences)"] = "",
    *,
    ctx: CallContext | None = None,
    task_svc: "TaskService" = None,
    session_svc: "SessionService" = None,
) -> ToolResult:
    """Trigger a replan: cancel all pending tasks and create a new plan task."""
    task = ctx.task if ctx else None

    cancelled = task_svc.cancel_pending(task.session_id if task else "")
    settings  = get_settings()
    task_svc.create(
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
        task_svc.finish(task.id, result=reason, session_id=task.session_id)
        task.status = "FINISHED"
    return ToolResult(content=f"Cancelled {cancelled} tasks. New plan task created.")


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
    *,
    ctx: CallContext | None = None,
    task_svc: "TaskService" = None,
    session_svc: "SessionService" = None,
) -> ToolResult:
    """Submit your assessment of the current task's execution result, and optionally review sibling tasks in one call."""
    task = ctx.task if ctx else None
    if task_outcome not in ("success", "failed", "active", "needs_user_input"):
        task_outcome = "failed"
    actor_result = f"{task_result}\n\nNext Step Hint: {next_step_hint}" if next_step_hint else task_result

    if task is not None:
        task.actor_outcome = task_outcome
        task.actor_result  = actor_result
        task.proceed_to_review = True
        outputs = {"progress_text": task.progress_text} if getattr(task, "progress_text", None) else None

        if task_outcome == "success":
            task_svc.finish(task.id, result=task_result, outputs=outputs, session_id=task.session_id)
            task.status = "FINISHED"
        elif task_outcome == "failed":
            task_svc.fail(task.id, error=task_result, session_id=task.session_id)
            task.status = "FAILED"
        elif task_outcome == "active":
            task_svc.transition(task.id, "PENDING", task.session_id)
            task.status = "PENDING"
        else:  # needs_user_input
            task_outcome, task_result = _confirm_with_user(
                task, task_result, outputs, task_svc=task_svc, session_svc=session_svc,
            )
            task.actor_outcome = task_outcome
            task.actor_result  = task_result
            task.status = "FINISHED" if task_outcome == "success" else "FAILED"

    review_msg = ""
    if task_reviews and ctx:
        review_msg = _apply_reviews(task_reviews, ctx, task_svc=task_svc)

    return ToolResult(content=f"Assessment recorded: outcome={task_outcome}. {task_result}{review_msg}")


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
    *,
    ctx: CallContext | None = None,
    task_svc: "TaskService" = None,
    session_svc: "SessionService" = None,
) -> ToolResult:
    """Submit the decomposed task plan. Call exactly once per turn. Each task: title (str, short imperative), description (str, WHAT not HOW), use_subagent (bool), inherit_memory (bool, default true), skill_name (str or null), user_prompt (str or empty)."""
    task = ctx.task if ctx else None
    task_ids: list[str] = []
    titles:   list[str] = []
    prev_id:  str | None = None

    for spec in tasks:
        inputs: dict = {}
        skill_name = spec.get("skill_name") or None if isinstance(spec, dict) else getattr(spec, "skill_name", None)
        if skill_name:
            inputs["skill_name"] = skill_name
        use_subagent = spec.get("use_subagent", False) if isinstance(spec, dict) else getattr(spec, "use_subagent", False)
        if bool(use_subagent):
            inputs["use_subagent"] = True
            default_subagent_template = get_settings().default_agent_template_name
            subagent_template = spec.get("subagent_template") if isinstance(spec, dict) else getattr(spec, "subagent_template", None)
            inputs["subagent_template"] = subagent_template or default_subagent_template
            inherit_memory = spec.get("inherit_memory", True) if isinstance(spec, dict) else getattr(spec, "inherit_memory", True)
            inputs["inherit_memory"] = bool(inherit_memory)
        title_val       = spec.get("title", "")        if isinstance(spec, dict) else spec.title
        description_val = spec.get("description", "")  if isinstance(spec, dict) else spec.description
        user_prompt_val = spec.get("user_prompt", "")  if isinstance(spec, dict) else getattr(spec, "user_prompt", "")
        t = task_svc.create(
            session_id=task.session_id if task else "",
            creator_agent_id=task.assigned_agent_id if task else "",
            user_prompt=user_prompt_val,
            title=title_val,
            description=description_val,
            inputs=inputs,
            parent_task_id=task.id if task else None,
            dag_deps=[prev_id] if prev_id else [],
        )
        task_ids.append(t.id)
        titles.append(title_val)
        prev_id = t.id

    result_text = (
        f"Planned {len(task_ids)} tasks: {', '.join(titles)}"
        if task_ids else "No further tasks needed — goal already achieved."
    )

    if task is not None and task_ids:
        task_svc.transition(task.id, "SUSPENDED", task.session_id)
        task.status = "SUSPENDED"
    task.actor_done = True
    return ToolResult(content=result_text)


@control_tool
def submit_task(
    title: Annotated[str, "Short imperative title for the task (≤20 chars)"],
    description: Annotated[str, "WHAT to achieve — not HOW, no tool names or arguments (≤80 chars)"],
    skill_name: Annotated[str, "Skill to assign to the task, or empty string if none"] = "",
    use_subagent: Annotated[bool, "True if the task should run in an independent sub-agent"] = False,
    subagent_template: Annotated[str, "Template name for the sub-agent (e.g. 'planner'); empty uses system default"] = "",
    inherit_memory: Annotated[bool, "True (default) for sub-agents that need session history"] = True,
    task_prompt: Annotated[str, "Detailed prompt for this task — extract and include as much relevant context from the user's original request as possible; leave empty only if no additional detail is needed"] = "",
    *,
    ctx: CallContext | None = None,
    task_svc: "TaskService" = None,
    session_svc: "SessionService" = None,
) -> ToolResult:
    """Create a single new task in the current session. The current task continues running.
    Use when you need to delegate work to another task/agent without replacing the current plan."""
    task = ctx.task if ctx else None
    if skill_name and not use_subagent:
        use_subagent = True
    inputs: dict = {}
    if skill_name:
        inputs["skill_name"] = skill_name
    if bool(use_subagent):
        inputs["use_subagent"] = True
        inputs["inherit_memory"] = bool(inherit_memory)
        if subagent_template:
            inputs["subagent_template"] = subagent_template
    t = task_svc.create(
        session_id=task.session_id if task else "",
        creator_agent_id=task.assigned_agent_id if task else "",
        user_prompt=task_prompt,
        title=title,
        description=description,
        inputs=inputs,
        parent_task_id=task.id if task else None,
    )
    if task is not None:
        task_svc.transition(task.id, "SUSPENDED", task.session_id)
        task.status = "SUSPENDED"
        task.actor_done = True
    return ToolResult(content=f"Task created: id={t.id}, title={t.title!r}")


def get_control_tools(
    task_svc: "TaskService",
    session_svc: "SessionService",
) -> list[ToolDefinition]:
    """填充服务引用，返回所有控制工具定义。由 ToolRegistry.register_control_tools() 调用。"""
    _svc.update(task_svc=task_svc, session_svc=session_svc)
    return list(_CONTROL_SCHEMAS)
