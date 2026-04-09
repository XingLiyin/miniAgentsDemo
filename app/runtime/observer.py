"""Observer：独立评估本轮执行结果，判断 task 是否完成、goal 是否达成。

mark_task_complete 职责已移至此处：Observer 通过 submit_observation 工具
同时给出 task 级完成判定（task_complete）和 session 级目标判定（done）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated

from app.domain.models.session import Session
from app.llm.base import BaseChatClient
from app.llm.types import LLMMessage
from app.runtime.agent_controller import AgentController
from app.runtime.types import ActorResult, ObserverVerdict, ReasoningContext
from app.tools.definition import ToolResult
from app.tools.tool_decorator import tool_result

if TYPE_CHECKING:
    from app.domain.models.task import Task

logger = logging.getLogger(__name__)

# 无 role_md 时的身份兜底
_OBSERVER_ROLE_FALLBACK = (
    "You are an objective observer evaluating task execution results."
)

# 评估方法论：不随 agent 变化，始终追加在 role 之后
_OBSERVER_EVALUATION_GUIDE = (
    "You will be given the original session goal, a summary of past progress, "
    "the current task description, and the complete conversation transcript of this turn.\n"
    "Call submit_observation exactly once with your assessment.\n\n"
    "Set spawn_planner=True only when the task results reveal new sub-tasks that were not "
    "anticipated in the original plan and would clearly benefit from independent parallel "
    "execution by dedicated sub-agents. This triggers a fresh planning round. "
    "Do NOT set spawn_planner=True for routine sequential follow-up work.\n\n"
    "Use `replan` (if available) when the remaining tasks are fundamentally wrong and the whole "
    "plan must be rebuilt from scratch. Use `submit_observation` with `spawn_planner=True` when "
    "the plan is still valid but additional parallel work is needed."
)


# ── submit_observation ─────────────────────────────────────────────────────────

@tool_result
def replan(
    reason: Annotated[str, "Why the current plan must be discarded and rebuilt from scratch"],
    summary: Annotated[str, "Concise summary of what was accomplished before this replan (1-3 sentences)"],
) -> ToolResult:
    """Discard all remaining tasks from the current planning round and trigger a fresh plan."""
    return ToolResult(content="")


@tool_result
def submit_observation(
    task_complete: Annotated[bool, "True if the assigned task was completed successfully"],
    task_result: Annotated[str, "What was accomplished for this task, or why it could not be completed"],
    done: Annotated[bool, "True if the overall session goal is fully and completely achieved"],
    summary: Annotated[str, "Concise summary of what was accomplished this turn (1-3 sentences)"],
    reasoning: Annotated[str, "Internal reasoning: why task_complete=True/False, what is still missing"],
    needs_user_confirm: Annotated[bool, "True if you cannot determine task completion without user input"] = False,
    spawn_planner: Annotated[
        bool,
        "True if the task is complete but results reveal additional work requiring re-planning "
        "(e.g., discovered independent sub-tasks best run by dedicated sub-agents). "
        "False in most cases — only set when re-decomposition is clearly needed.",
    ] = False,
) -> ToolResult:
    """Submit your observation of this turn's execution results."""
    return ToolResult(content="")


class Observer:
    """Phase 4：独立 LLM 调用，判断 task 是否完成、goal 是否达成，生成摘要。

    成功路径：LLM 调用 submit_observation，由 AgentController 分发并解析所有字段。
    降级路径：LLM 调用失败或未调用工具，使用规则判断。
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
    ) -> ObserverVerdict:
        """评估本轮执行结果，返回 ObserverVerdict。"""
        try:
            return self._llm_observe(session, result, ctx, task)
        except Exception as e:
            logger.warning("Observer LLM call failed, falling back to rules: %s", e)
            return self._rule_observe(result, session)

    # ── 私有辅助 ──────────────────────────────────────────────────────────────

    def _llm_observe(
        self,
        session: Session,
        result: ActorResult,
        ctx: ReasoningContext,
        task: "Task",
    ) -> ObserverVerdict:
        # role 驱动 Observer 身份（评判准则），无则降级到通用描述
        role_base = ctx.role or _OBSERVER_ROLE_FALLBACK
        system_prompt = role_base + "\n\n---\n\n" + _OBSERVER_EVALUATION_GUIDE

        transcript = self._build_transcript(result)

        user_content = (
            f"Original session goal: {session.goal}\n\n"
            f"Previous progress summary: {ctx.summary_text or 'None'}\n\n"
            f"Current task: {task.title}\n"
            f"Task description: {task.description or task.title}\n\n"
            f"Execution transcript ({len(result.conversation_turns)} round(s)):\n{transcript}\n\n"
            "Evaluate: did the agent complete the current task? Is the overall session goal achieved?"
        )

        response = self._llm_client.send_message(
            messages=[LLMMessage(role="user", content=user_content)],
            system_prompt=system_prompt,
            tools=ctx.observer_tools,
        )
        parsed_response = self._llm_client.parse_response(response)

        for tool_call in parsed_response.tool_calls:
            if self._agent_controller.can_handle(tool_call.name):
                ctrl = self._agent_controller.dispatch(
                    tool_call.name, tool_call.input, None, task
                )
                d = ctrl.signal_data
                return ObserverVerdict(
                    task_success=d["task_success"],
                    task_result=d["task_result"],
                    done=d["done"],
                    summary=d["summary"],
                    reasoning=d["reasoning"],
                    needs_user_confirm=d["needs_user_confirm"],
                )

        raise RuntimeError("Observer: submit_observation or replan not called")

    def _build_transcript(self, result: ActorResult) -> str:
        """将 conversation_turns 展开为可读文本，供 Observer LLM 评估。"""
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

    def _rule_observe(
        self,
        result: ActorResult,
        session: Session,
    ) -> ObserverVerdict:
        """降级规则：Observer LLM 调用失败时使用。"""
        token_pct = (
            session.token_used / session.token_budget
            if session.token_budget
            else 0
        )

        # Token 快耗尽时强制结束
        if token_pct > 0.9:
            return ObserverVerdict(
                task_success=True,
                task_result="Token budget nearly exhausted; treating as complete.",
                done=True,
                summary="Token budget nearly exhausted; stopping.",
                reasoning="rule-based: token budget",
            )

        summary = (
            "Completed this turn's task." if result.success else "Task failed this turn."
        )
        return ObserverVerdict(
            task_success=result.success,
            task_result=result.output or result.error or "",
            done=False,
            summary=summary,
            reasoning="rule-based fallback",
        )
