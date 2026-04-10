"""Observer：两轮独立 LLM 调用，评估本轮执行结果。

第一轮：仅暴露当前任务上下文，收集 task_outcome / task_result / summary。
第二轮：暴露 session 任务列表，收集 task_reviews（confirmed / reopen / skip）。
        仅在任务列表中存在可复核条目时触发；第一轮若调用 submit_plan / replan 则跳过。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated

from app.domain.models.session import Session
from app.llm.base import BaseChatClient
from app.llm.types import LLMMessage
from app.runtime.agent_controller import AgentController
from app.runtime.types import (
    ActorResult,
    ObserverVerdict,
    ReasoningContext,
    ReviewStatus,
    TaskOutcome,
    TaskReview,
)
from app.tools.definition import ToolResult
from app.tools.tool_decorator import tool_result

if TYPE_CHECKING:
    from app.domain.models.agent import Agent
    from app.domain.models.task import Task

logger = logging.getLogger(__name__)

# 无 role_md 时的身份兜底
_OBSERVER_ROLE_FALLBACK = (
    "You are an objective observer evaluating task execution results."
)

# ── Round 1 prompt：当前任务评估 ───────────────────────────────────────────────

_ASSESSMENT_GUIDE = (
    "## 第一轮：评估当前任务\n\n"
    "你将收到：\n"
    "- 会话目标（session goal）\n"
    "- 历史进度摘要（previous progress summary）\n"
    "- 当前任务描述（current task）\n"
    "- 本轮完整执行记录（execution transcript）\n\n"
    "## 调用规则\n\n"
    "恰好调用一次工具：`submit_task_assessment`、`submit_plan`（若可用）或 `replan`（若可用）。\n\n"
    "## task_outcome 取值说明\n\n"
    "- `success`：当前任务已完成，目标达成。\n"
    "- `failed`：当前任务无法完成，执行结果不满足要求。系统将根据重试策略决定是否重试。\n"
    "- `needs_user_input`：无法自动判断任务是否完成，需要用户介入确认。"
)

# ── Round 2 prompt：任务列表复核 ───────────────────────────────────────────────

_REVIEW_GUIDE = (
    "## 第二轮：复核任务列表\n\n"
    "你将收到：\n"
    "- 会话目标（session goal）\n"
    "- 第一轮评估结论（outcome + result）\n"
    "- 会话任务列表（FINISHED 和 PENDING 任务，含各自结果）\n\n"
    "## 调用规则\n\n"
    "恰好调用一次工具：`submit_task_reviews`。\n\n"
    "## review_status 取值说明\n\n"
    "- `confirmed`：FINISHED 任务确认已完成，无需操作。\n"
    "- `reopen`：FINISHED 任务实际未达成目标，系统将重新放入队列执行。\n"
    "- `skip`：PENDING 任务已被当前或之前的执行间接满足，系统将直接标记为完成并跳过。\n\n"
    "## 填写规则\n\n"
    "- 对每个 FINISHED 和 PENDING 任务填写 review_status 和 reasoning。\n"
    "- `task_title` 必须与任务列表中的原始标题完全一致。\n"
    "- `reasoning` 必填，简述判断依据（1-2 句）。\n"
    "- 没有足够信息判断的任务不要填。\n"
    "- 当前正在评估的任务不填入 reviews（已在第一轮单独处理）。"
)


# ── 第一轮工具：当前任务评估 ───────────────────────────────────────────────────

@tool_result
def submit_task_assessment(
    task_outcome: Annotated[
        str,
        "Outcome of the current task: "
        "'success' if completed successfully; "
        "'failed' if it could not be completed (system decides whether to retry); "
        "'needs_user_input' if completion cannot be determined without user confirmation.",
    ],
    task_result: Annotated[str, "What was accomplished, or why the task could not be completed"],
    summary: Annotated[str, "Concise summary of this turn (1-3 sentences), written to agent memory"],
) -> ToolResult:
    """Submit your assessment of the current task's execution result."""
    return ToolResult(content="")


# ── 第二轮工具：任务列表复核 ───────────────────────────────────────────────────

@tool_result
def submit_task_reviews(
    reviews: Annotated[
        list,
        "Review entries for FINISHED and PENDING tasks in the session. "
        "Each entry: "
        "  task_title (str, exact match from task list); "
        "  current_status ('FINISHED' or 'PENDING'); "
        "  review_status ('confirmed' | 'reopen' | 'skip'); "
        "  reasoning (str, brief explanation, required). "
        "Omit tasks you have no information about.",
    ],
) -> ToolResult:
    """Submit your review of all FINISHED and PENDING tasks in the session."""
    return ToolResult(content="")


# ── Observer ───────────────────────────────────────────────────────────────────

class Observer:
    """两轮独立 LLM 调用：第一轮评估当前任务，第二轮复核任务列表。

    降级路径：任意一轮 LLM 调用失败时退回规则判断（跳过复核）。
    """

    def __init__(
        self,
        llm_client: BaseChatClient,
        agent_controller: AgentController,
    ) -> None:
        self._llm_client = llm_client
        self._agent_controller = agent_controller

    def observe(
        self,
        session: Session,
        result: ActorResult,
        ctx: ReasoningContext,
        task: "Task",
        task_list: list["Task"] | None = None,
        agent: "Agent | None" = None,
    ) -> ObserverVerdict:
        """评估本轮执行结果，返回 ObserverVerdict。"""
        try:
            return self._llm_observe(session, result, ctx, task, task_list or [], agent)
        except Exception as e:
            logger.warning("Observer LLM call failed, falling back to rules: %s", e)
            return self._rule_observe(result, session)

    # ── 私有：两轮调用主逻辑 ────────────────────────────────────────────────────

    def _llm_observe(
        self,
        session: Session,
        result: ActorResult,
        ctx: ReasoningContext,
        task: "Task",
        task_list: list["Task"],
        agent: "Agent | None" = None,
    ) -> ObserverVerdict:
        role_base = ctx.role or _OBSERVER_ROLE_FALLBACK
        session_id = getattr(task, "session_id", "")

        # 按 agent.llm_name 动态解析 LLM 客户端
        llm_client = self._llm_client
        if agent is not None and agent.llm_name:
            try:
                from app.llm.registry import get_llm_registry
                llm_client = get_llm_registry().get_client(agent.llm_name)
            except Exception:
                pass

        # 第一轮
        complete_verdict, assessment = self._round1_assess(
            session, result, ctx, task, role_base, llm_client, session_id
        )
        if complete_verdict is not None:
            # submit_plan 或 replan 已调用，verdict 完整，跳过第二轮
            return complete_verdict

        # 第二轮（仅当有可复核任务时）
        reviewable = [
            t for t in task_list
            if t.status in ("FINISHED", "PENDING") and t.id != task.id
        ]
        reviews: list[TaskReview] = []
        if reviewable:
            try:
                reviews = self._round2_review(session, task, reviewable, assessment, role_base, llm_client, session_id)
            except Exception as e:
                logger.warning("Observer round 2 failed, skipping reviews: %s", e)

        return ObserverVerdict(
            task_outcome=assessment["task_outcome"],
            task_result=assessment["task_result"],
            summary=assessment["summary"],
            task_reviews=reviews,
        )

    # ── 第一轮 ────────────────────────────────────────────────────────────────

    def _round1_assess(
        self,
        session: Session,
        result: ActorResult,
        ctx: ReasoningContext,
        task: "Task",
        role_base: str,
        llm_client: "BaseChatClient | None" = None,
        session_id: str = "",
    ) -> tuple[ObserverVerdict | None, dict | None]:
        """第一轮 LLM 调用：评估当前任务。

        返回：
        - (None, assessment_dict)：proceed_to_review=True，继续第二轮（submit_task_assessment）
        - (complete_verdict, None)：proceed_to_review=False，跳过第二轮（submit_plan）
        """
        system_prompt = role_base + "\n\n---\n\n" + _ASSESSMENT_GUIDE
        transcript = self._build_transcript(result)
        user_content = (
            f"Session goal: {session.goal}\n\n"
            f"Previous progress summary: {ctx.summary_text or 'None'}\n\n"
            f"Current task: {task.title}\n"
            f"Task description: {task.description or task.title}\n\n"
            f"Execution transcript ({len(result.conversation_turns)} round(s)):\n{transcript}\n\n"
            "Assess: was the current task completed successfully?"
        )
        messages = [LLMMessage(role="user", content=user_content)]
        tools = ctx.observer_tools

        _client = llm_client or self._llm_client
        _push_llm_event(session_id, "observer_round1", system_prompt, messages, tools)

        full_text, tool_call_acc = _stream_observer(
            _client, messages, system_prompt, tools, session_id, "observer_round1"
        )
        tool_calls = _build_obs_tool_calls(tool_call_acc)

        for tool_call in tool_calls:
            if self._agent_controller.can_handle(tool_call.name):
                ctrl = self._agent_controller.dispatch(
                    tool_call.name, tool_call.input, None, task
                )
                d = ctrl.signal_data
                if d.get("proceed_to_review"):
                    return None, d
                return ObserverVerdict(
                    task_outcome=d["task_outcome"],
                    task_result=d["task_result"],
                    summary=d["summary"],
                    task_reviews=[],
                ), None

        raise RuntimeError("Observer round 1: no valid tool called")

    # ── 第二轮 ────────────────────────────────────────────────────────────────

    def _round2_review(
        self,
        session: Session,
        current_task: "Task",
        reviewable: list["Task"],
        assessment: dict,
        role_base: str,
        llm_client: "BaseChatClient | None" = None,
        session_id: str = "",
    ) -> list[TaskReview]:
        """第二轮 LLM 调用：复核任务列表，返回 TaskReview 列表。"""
        system_prompt = role_base + "\n\n---\n\n" + _REVIEW_GUIDE
        task_list_text = self._build_task_list_section(reviewable)
        user_content = (
            f"Session goal: {session.goal}\n\n"
            f"Round 1 assessment — current task '{current_task.title}':\n"
            f"  outcome: {assessment['task_outcome']}\n"
            f"  result: {assessment['task_result']}\n\n"
            f"Session task list (excluding current task):\n{task_list_text}\n\n"
            "Review each task: is it confirmed done, should it be reopened, or can it be skipped?"
        )
        messages = [LLMMessage(role="user", content=user_content)]
        tools = [submit_task_reviews.to_llm_tool()]

        _client = llm_client or self._llm_client
        _push_llm_event(session_id, "observer_round2", system_prompt, messages, tools)

        _, tool_call_acc = _stream_observer(
            _client, messages, system_prompt, tools, session_id, "observer_round2"
        )
        tool_calls = _build_obs_tool_calls(tool_call_acc)

        for tool_call in tool_calls:
            if tool_call.name == "submit_task_reviews":
                return self._parse_task_reviews(
                    tool_call.input.get("reviews") or [], reviewable
                )

        logger.warning("Observer round 2: submit_task_reviews not called, returning empty reviews")
        return []

    # ── 私有辅助 ──────────────────────────────────────────────────────────────

    def _parse_task_reviews(
        self, raw: list, task_list: list["Task"]
    ) -> list[TaskReview]:
        """将 LLM 返回的 reviews 解析为 TaskReview 对象。

        LLM 填写 task_title，此处解析为 task_id。
        格式错误或无法匹配的条目静默忽略。
        """
        valid_review = {"confirmed", "reopen", "skip"}
        title_to_task = {t.title: t for t in task_list}
        reviews: list[TaskReview] = []

        for item in raw:
            if not isinstance(item, dict):
                continue
            title         = item.get("task_title", "")
            review_status = item.get("review_status", "")
            reasoning     = str(item.get("reasoning", ""))

            if not title or review_status not in valid_review:
                continue

            matched = title_to_task.get(title)
            if matched is None:
                logger.warning("Observer task_reviews: no task with title %r, skipping", title)
                continue

            # current_status 以实际任务状态为准，LLM 填的值仅用于 debug 日志
            llm_status = item.get("current_status", "")
            if llm_status and llm_status != matched.status:
                logger.warning(
                    "Observer task_reviews: LLM reported status %r for %r but actual is %r, using actual",
                    llm_status, title, matched.status,
                )

            reviews.append(TaskReview(
                task_id=matched.id,
                current_status=matched.status,
                review_status=review_status,
                reasoning=reasoning,
            ))

        return reviews

    def _build_task_list_section(self, tasks: list["Task"]) -> str:
        """渲染任务列表为可读文本（供第二轮 prompt 使用）。"""
        lines: list[str] = []
        for t in tasks:
            result_hint = f" | result: {t.result[:120]}" if t.status == "FINISHED" and t.result else ""
            lines.append(f"  [{t.status}] {t.title}{result_hint}")
        return "\n".join(lines)

    def _build_transcript(self, result: ActorResult) -> str:
        """将 conversation_turns 展开为可读文本，供第一轮 LLM 评估。"""
        if not result.conversation_turns:
            if result.output:
                return f"[No tool calls] Agent response: {result.output}"
            return "[No conversation recorded]"

        lines: list[str] = []
        for turn in result.conversation_turns:
            lines.append(f"--- Round {turn.round + 1} ---")
            if turn.tool_calls:
                for tc in turn.tool_calls:
                    status = "ERROR" if tc.is_error else "OK"
                    lines.append(f"  Tool call: {tc.tool_name}({tc.arguments})")
                    lines.append(f"  Result [{status}]: {tc.result[:500]}")
            if turn.llm_text:
                lines.append(f"  Agent reply: {turn.llm_text}")
        return "\n".join(lines)

    # ── 私有辅助（实例方法结束） ─────────────────────────────────────────────────

    def _rule_observe(
        self,
        result: ActorResult,
        session: Session,
    ) -> ObserverVerdict:
        """降级规则：任意一轮 LLM 调用失败时使用，跳过任务复核。"""
        token_pct = (
            session.token_used / session.token_budget
            if session.token_budget
            else 0
        )

        if token_pct > 0.9:
            return ObserverVerdict(
                task_outcome="success",
                task_result="Token budget nearly exhausted; treating as complete.",
                summary="Token budget nearly exhausted; stopping.",
            )

        outcome: TaskOutcome = "success" if result.success else "failed"
        summary = (
            "Completed this turn's task." if result.success else "Task failed this turn."
        )
        return ObserverVerdict(
            task_outcome=outcome,
            task_result=result.output or result.error or "",
            summary=summary,
        )


# ── 模块级工具 ─────────────────────────────────────────────────────────────────

def _push_llm_event(
    session_id: str,
    round_label: str,
    system_prompt: str,
    messages: list,
    tools: list,
) -> None:
    """向前端推送 llm_prompt 调试事件（含 system_prompt / messages / tool 名列表）。"""
    if not session_id:
        return
    try:
        from app.runtime.sse_bus import get_sse_bus
        get_sse_bus().push(session_id, {
            "type": "llm_prompt",
            "source": "observer",
            "round_label": round_label,
            "system_prompt": system_prompt,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "tool_names": [getattr(t, "name", str(t)) for t in tools],
        })
    except Exception:
        pass


def _stream_observer(
    client: "BaseChatClient",
    messages: list,
    system_prompt: str,
    tools: list,
    session_id: str,
    round_label: str,
) -> tuple[str, dict]:
    """流式调用 LLM，推送 observer_text_delta / observer_text_done，返回 (full_text, tool_call_acc)。"""
    full_text = ""
    tool_call_acc: dict[int, dict] = {}

    try:
        from app.runtime.sse_bus import get_sse_bus
        _sse = get_sse_bus() if session_id else None
    except Exception:
        _sse = None

    for chunk in client.stream_message(messages=messages, system_prompt=system_prompt, tools=tools):
        if chunk.text_delta:
            full_text += chunk.text_delta
            if _sse:
                try:
                    _sse.push(session_id, {
                        "type": "observer_text_delta",
                        "delta": chunk.text_delta,
                        "round_label": round_label,
                    })
                except Exception:
                    pass
        if chunk.tool_call_delta:
            tool_call_acc[chunk.tool_call_delta["index"]] = chunk.tool_call_delta

    if _sse:
        try:
            _sse.push(session_id, {
                "type": "observer_text_done",
                "text": full_text,
                "round_label": round_label,
            })
        except Exception:
            pass

    return full_text, tool_call_acc


def _build_obs_tool_calls(acc: dict[int, dict]) -> list:
    """将流式 tool_call 累积缓冲区转换为 ToolCallBlock 列表。"""
    import json
    from app.llm.types import ToolCallBlock

    result = []
    for idx in sorted(acc.keys()):
        buf = acc[idx]
        args_raw = buf.get("arguments") or ""
        try:
            args = json.loads(args_raw) if args_raw else {}
        except json.JSONDecodeError:
            args = {"_raw": args_raw}
        result.append(ToolCallBlock(
            type="tool_call",
            id=buf.get("id", ""),
            name=buf.get("name", ""),
            input=args,
        ))
    return result
