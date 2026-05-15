"""控制工具：将 Agent 内部控制逻辑暴露为 ToolDefinition。

@control_tool 装饰器：同时声明 schema 和实现，直接生成完整 ToolDefinition 存入 _CONTROL_SCHEMAS。
  schema 从 Annotated 注解提取，task_svc / session_svc / ctx 不计入 schema。
  handler 闭包在调用时从 _svc 读取服务（lazy）；register_control_tools() 负责在注册前填入。

register_control_tools()：填充 _svc，将 _CONTROL_SCHEMAS 批量注册到 ToolRegistry。
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import TYPE_CHECKING, Annotated, TypedDict

from app.config.settings import get_settings
from app.tools.types import CallContext, ToolDefinition, ToolResult
from app.tools.utils import extract_input_schema, make_tool_handler

if TYPE_CHECKING:
    from app.domain.models.task import Task
    from app.domain.services.session_service import SessionService
    from app.domain.services.task_service import TaskService


class _PlannedTaskBase(TypedDict):
    title: Annotated[str, "Short imperative title for the task"]
    description: Annotated[str, "WHAT to achieve — not HOW, no tool names or arguments"]


class PlannedTask(_PlannedTaskBase, total=False):
    task_prompt: Annotated[str, "Detailed prompt — extract and include as much relevant context from the user's original request as possible"]
    skill_name: Annotated[str | None, "One of the available skills, or null"]
    use_subagent: Annotated[bool, "True if the task should run in an independent sub-agent"]
    subagent_template: Annotated[str | None, "Template name for the sub-agent (e.g. 'default'); empty uses system default"]
    inherit_memory: Annotated[bool, "True (default) for sub-agents that need session history"]


# ── @control_tool 装饰器 ──────────────────────────────────────────────────────

_CONTROL_SCHEMAS: list[ToolDefinition] = []

# schema 提取時跳過的參數名（运行时注入，不暴露给 LLM）
_SKIP: frozenset[str] = frozenset({"ctx", "task_svc", "session_svc"})

# 运行时服务引用，由 register_control_tools() 在启动时填入
_svc: dict = {}


def _add_tracking_tasks(session_id: str, agent_ids: list[str], task_id: str) -> None:
    """将 task_id 追加到指定 agents 的 tracking_tasks，并将 agent_id 同步写入 task.trackers（均幂等）。"""
    agent_store = _svc.get("agent_store")
    task_svc = _svc.get("task_svc")
    if not agent_store:
        return
    added: list[str] = []
    for aid in agent_ids:
        data = agent_store.get(session_id, aid)
        if data is None:
            continue
        tracking: list[str] = data.get("tracking_tasks", [])
        if task_id not in tracking:
            tracking.append(task_id)
            data["tracking_tasks"] = tracking
            agent_store.save(data)
            added.append(aid)
    if added and task_svc:
        try:
            t = task_svc.get(task_id, session_id)
            changed = False
            for aid in added:
                if aid not in t.trackers:
                    t.trackers.append(aid)
                    changed = True
            if changed:
                task_svc.save(t)
        except Exception:
            pass


def _track_parent_and_siblings(
    session_id: str, parent_task_id: str | None, new_task_id: str, task_svc
) -> None:
    """将 new_task_id 追加到父 task 的 agent 以及所有兄弟 task 的 agent 的 tracking_tasks。"""
    if not parent_task_id or not task_svc:
        return
    agent_ids: set[str] = set()
    try:
        parent = task_svc.get(parent_task_id, session_id)
        if parent.assigned_agent_id:
            agent_ids.add(parent.assigned_agent_id)
    except Exception:
        pass
    try:
        for sib in task_svc.list_children(parent_task_id, session_id):
            if sib.id != new_task_id and sib.assigned_agent_id:
                agent_ids.add(sib.assigned_agent_id)
    except Exception:
        pass
    if agent_ids:
        _add_tracking_tasks(session_id, list(agent_ids), new_task_id)


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

    _accepted = set(orig_sig.parameters.keys())

    def _fn(*args, **kwargs):
        return fn(*args, **kwargs, **{k: v for k, v in _svc.items() if k in _accepted})

    _fn.__signature__ = stripped

    tool_def = ToolDefinition(
        name=fn.__name__,
        description=(fn.__doc__ or "").strip(),
        input_schema=extract_input_schema(fn, exclude=_SKIP),
        handler=make_tool_handler(_fn),
        is_control=True,
    )
    _CONTROL_SCHEMAS.append(tool_def)
    return tool_def


# ── 模块级辅助函数 ─────────────────────────────────────────────────────────────

def _confirm_with_user(task: Task, task_result: str, *, task_svc: "TaskService", session_svc: "SessionService") -> tuple[str, str]:
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
        sess = session_svc.get(session_id)
        sess.metadata["_hitl_prompt"] = prompt
        sess.metadata["_hitl_input_type"] = "task_completion_confirm"
        session_svc.save(sess)
    except Exception:
        pass
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
    try:
        sess = session_svc.get(session_id)
        sess.metadata.pop("_hitl_prompt", None)
        sess.metadata.pop("_hitl_input_type", None)
        session_svc.save(sess)
    except Exception:
        pass

    if answer.startswith("用户已确认任务完成"):
        task_svc.finish(task.id, result=task_result, session_id=task.session_id)
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
        sess = session_svc.get(session_id)
        sess.metadata["_hitl_prompt"] = prompt
        sess.metadata["_hitl_input_type"] = "user_input"
        session_svc.save(sess)
    except Exception:
        pass
    try:
        get_sse_bus().push(session_id, {"type": "message", "role": "assistant",
                                        "content": prompt, "created_at": now_iso()})
        get_sse_bus().push(session_id, {"type": "waiting_input", "prompt": prompt,
                                        "input_type": "user_input", "task_title": "等待用户输入"})
    except Exception:
        pass

    answer = get_hitl_store().wait(session_id, agent_id, prompt, "user_input")
    session_svc.transition(session_id, "RUNNING")
    try:
        sess = session_svc.get(session_id)
        sess.metadata.pop("_hitl_prompt", None)
        sess.metadata.pop("_hitl_input_type", None)
        session_svc.save(sess)
    except Exception:
        pass
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
                is_daemon = bool(target.settings.get("_daemon") if target.settings else False)
                event_type = "daemon_task_updated" if is_daemon else "task_updated"
                get_sse_bus().push(session_id, {"type": event_type, "task": target.to_dict()})
            except Exception:
                pass
        except Exception:
            logging.getLogger(__name__).exception(
                "control_tools: failed to update metadata for task %s", target_task_id)

    if session_goal and session_id:
        try:
            session_svc.set_goal(session_id, session_goal)
        except Exception:
            logging.getLogger(__name__).exception(
                "control_tools: failed to update session goal for session %s", session_id)

    if task is not None:
        task.actor_done    = True
        task.actor_outcome = "success"
        task.actor_summary = "metadata updated"
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
    replan_inputs: dict = {"use_subagent": True, "inherit_memory": True,
                           "subagent_template": settings.default_planner_template_name}
    if ctx and ctx.working_dir:
        replan_inputs["working_dir"] = ctx.working_dir
    task_svc.create(
        session_id=task.session_id if task else "",
        creator_agent_id=task.assigned_agent_id if task else "",
        user_prompt=(task.user_prompt if task else "") or "",
        title=f"Replan For: {reason}",
        description=f"{summary}\n\n请基于最新情况重新制定计划。",
        inputs=replan_inputs,
    )
    if task is not None:
        task.actor_outcome     = "success"
        task.actor_summary     = reason
        task.proceed_to_review = False
        task_svc.finish(task.id, result=reason, session_id=task.session_id)
        task.status = "FINISHED"
    return ToolResult(content=f"Cancelled {cancelled} tasks. New plan task created.")


@control_tool
def submit_task_assessment(
    task_status: Annotated[
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
    if task_status not in ("success", "failed", "active", "needs_user_input"):
        task_status = "failed"
    task_result = f"{task_result}\n\nNext Step Hint: {next_step_hint}" if next_step_hint else task_result

    if task is not None:
        task.actor_outcome = task_status
        task.actor_summary = task_result
        task.proceed_to_review = True

        if task_status == "success":
            task_svc.finish(task.id, result=task_result, session_id=task.session_id)
            task.status = "FINISHED"
        elif task_status == "failed":
            task_svc.fail(task.id, error=task_result, session_id=task.session_id)
            task.status = "FAILED"
        elif task_status == "active":
            task_svc.transition(task.id, "PENDING", task.session_id)
            task.status = "PENDING"
        else:  # needs_user_input
            task_status, task_result = _confirm_with_user(
                task, task_result, task_svc=task_svc, session_svc=session_svc,
            )
            task.actor_outcome = task_status
            task.actor_summary = task_result
            task.status = "FINISHED" if task_status == "success" else "FAILED"

    review_msg = ""
    if task_reviews and ctx:
        review_msg = _apply_reviews(task_reviews, ctx, task_svc=task_svc)

    return ToolResult(content=f"Assessment recorded: outcome={task_status}. {task_result}{review_msg}")


@control_tool
def submit_plan(
    tasks: Annotated[
        list[PlannedTask],
        (
            "Ordered list of tasks for this turn. Empty list means the goal is already complete. "
            "Each task: "
            "  title: short imperative title; "
            "  description: WHAT to achieve — not HOW, no tool names or arguments; "
            "  task_prompt: detailed prompt for this task — extract and include as much relevant context from the user's original request as possible; "

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
    """Submit the decomposed task plan. Call exactly once per turn. Each task: title (str), description (str, WHAT not HOW), task_prompt (str), skill_name (str|null), use_subagent (bool), subagent_template (str), inherit_memory (bool, default true). Pass empty list if goal is already complete."""
    task = ctx.task if ctx else None
    task_ids: list[str] = []
    titles:   list[str] = []
    prev_id:  str | None = None

    if isinstance(tasks, str):
        try:
            tasks = json.loads(tasks)
        except Exception:
            tasks = []

    session_id_val = task.session_id if task else ""
    assigned_id = task.assigned_agent_id if task else ""
    creator_id  = task.creator_agent_id  if task else ""
    tracked_agent_ids = list(dict.fromkeys(x for x in [assigned_id, creator_id] if x))

    for planned in tasks:
        if isinstance(planned, str):
            spec: PlannedTask = {"title": planned, "description": planned}
        else:
            spec = planned
        inputs: dict = {}
        if ctx and ctx.working_dir:
            inputs["working_dir"] = ctx.working_dir
        skill_name = spec.get("skill_name")
        if skill_name:
            inputs["skill_name"] = skill_name
        use_subagent = spec.get("use_subagent", False)
        if bool(use_subagent):
            inputs["use_subagent"] = True
            default_subagent_template = get_settings().default_agent_template_name
            subagent_template = spec.get("subagent_template")
            inputs["subagent_template"] = subagent_template or default_subagent_template
            inherit_memory = spec.get("inherit_memory", True)
            inputs["inherit_memory"] = bool(inherit_memory)
        title_val       = spec["title"]
        description_val = spec["description"]
        user_prompt_val = spec.get("task_prompt", "")
        t = task_svc.create(
            session_id=session_id_val,
            creator_agent_id=assigned_id,
            user_prompt=user_prompt_val,
            title=title_val,
            description=description_val,
            inputs=inputs,
            parent_task_id=task.id if task else None,
            dag_deps=[prev_id] if prev_id else [],
        )
        _add_tracking_tasks(session_id_val, tracked_agent_ids, t.id)
        _track_parent_and_siblings(session_id_val, task.id if task else None, t.id, task_svc)
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
    task_prompt: Annotated[str, "Detailed prompt for this task — extract and include as much relevant context from the user's original request as possible"],
    skill_name: Annotated[str, "Skill to assign to the task, or empty string if none"] = "",
    use_subagent: Annotated[bool, "True if the task should run in an independent sub-agent"] = False,
    subagent_template: Annotated[str, "Template name for the sub-agent (e.g. 'planner'); empty uses system default"] = "",
    inherit_memory: Annotated[bool, "True (default) for sub-agents that need session history"] = True,
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
    if ctx and ctx.working_dir:
        inputs["working_dir"] = ctx.working_dir
    if skill_name:
        inputs["skill_name"] = skill_name
    if bool(use_subagent):
        inputs["use_subagent"] = True
        inputs["inherit_memory"] = bool(inherit_memory)
        if subagent_template:
            inputs["subagent_template"] = subagent_template
    creator_id = task.assigned_agent_id if task else ""
    t = task_svc.create(
        session_id=task.session_id if task else "",
        creator_agent_id=creator_id,
        user_prompt=task_prompt,
        title=title,
        description=description,
        inputs=inputs,
        parent_task_id=task.id if task else None,
    )
    session_id_val = task.session_id if task else ""
    if creator_id:
        _add_tracking_tasks(session_id_val, [creator_id], t.id)
    _track_parent_and_siblings(session_id_val, task.id if task else None, t.id, task_svc)
    if task is not None:                                                                                        
        task_svc.transition(task.id, "SUSPENDED", task.session_id)                                              
        task.status = "SUSPENDED"                                                                               
        task.actor_done = True
    return ToolResult(content=f"Task created: id={t.id}, title={t.title!r}")


@control_tool
def get_tracked_task_output(
    title: Annotated[str, "Title of the task to fetch output for"],
    *,
    ctx: CallContext | None = None,
    task_svc: "TaskService" = None,
    session_svc: "SessionService" = None,
) -> ToolResult:
    """Fetch the output of a tracked task by title. Returns outputs of all tracked tasks whose title matches.
    Only tasks in the current agent's tracking list are searched."""
    agent_store = _svc.get("agent_store")
    session_id = ctx.session_id if ctx else ""
    agent_id   = ctx.agent_id   if ctx else ""

    tracking_task_ids: list[str] = []
    if agent_store and agent_id and session_id:
        data = agent_store.get(session_id, agent_id)
        if data:
            tracking_task_ids = data.get("tracking_tasks", [])

    matches: list[str] = []
    for task_id in tracking_task_ids:
        try:
            t = task_svc.get(task_id, session_id)
        except Exception:
            continue
        if t.title != title:
            continue
        parts = [f"Task「{t.title}」(id={t.id}, status={t.status})"]
        if t.outputs:
            parts.append(f"Output:\n{t.outputs}")
        if t.result:
            parts.append(f"Result:\n{t.result}")
        if t.error:
            parts.append(f"Error:\n{t.error}")
        matches.append("\n".join(parts))

    if not matches:
        return ToolResult(content=f"No tracked task found with title {title!r}.")
    return ToolResult(content="\n\n---\n\n".join(matches))


def get_control_tools(
    task_svc: "TaskService",
    session_svc: "SessionService",
    agent_store=None,
) -> list[ToolDefinition]:
    """填充服务引用，返回所有控制工具定义。由 ToolRegistry.register_control_tools() 调用。"""
    _svc.update(task_svc=task_svc, session_svc=session_svc, agent_store=agent_store)
    return list(_CONTROL_SCHEMAS)
