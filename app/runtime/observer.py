"""Observer：独立评估本轮执行结果，判断目标是否达成。"""

from __future__ import annotations

import logging
from typing import Annotated

from app.domain.models.session import Session
from app.llm.base import BaseChatClient
from app.llm.types import LLMMessage
from app.runtime.types import ActorResult, ObserverVerdict, ReasoningContext
from app.tools.definition import ToolResult
from app.tools.tool_decorator import tool_result

logger = logging.getLogger(__name__)

_OBSERVER_SYSTEM_PROMPT = (
    "You are an objective observer evaluating whether a goal has been achieved.\n"
    "You will be given the original goal, a summary of past progress, and this turn's "
    "execution results.\n"
    "Call submit_observation exactly once with your assessment."
)


# ── submit_observation ─────────────────────────────────────────────────────────

@tool_result
def submit_observation(
    done: Annotated[bool, "True if the original session goal is fully and completely achieved"],
    summary: Annotated[str, "Concise summary of what was accomplished this turn (1-3 sentences)"],
    reasoning: Annotated[str, "Internal reasoning: why done=True/False, what is still missing"],
) -> ToolResult:
    """Submit your observation of this turn's execution results."""
    return ToolResult(content="")


class Observer:
    """Phase 4：独立 LLM 调用，判断目标是否达成，生成摘要。

    成功路径：LLM 调用 submit_observation，解析 done / summary / reasoning。
    降级路径：LLM 调用失败或未调用工具，使用规则判断。
    """

    def __init__(self, llm_client: BaseChatClient) -> None:
        self._llm_client = llm_client

    def observe(
        self,
        session: Session,
        results: list[ActorResult],
        ctx: ReasoningContext,
    ) -> ObserverVerdict:
        """评估本轮执行结果，返回 ObserverVerdict。"""
        try:
            return self._llm_observe(session, results, ctx)
        except Exception as e:
            logger.warning("Observer LLM call failed, falling back to rules: %s", e)
            return self._rule_observe(results, session)

    # ── 私有辅助 ──────────────────────────────────────────────────────────────

    def _llm_observe(
        self,
        session: Session,
        results: list[ActorResult],
        ctx: ReasoningContext,
    ) -> ObserverVerdict:
        successes = [r.output for r in results if r.success and r.output]
        errors = [r.error for r in results if not r.success and r.error]

        result_lines = (
            [f"- Task result: {o}" for o in successes]
            + [f"- Task error: {e}" for e in errors]
        )
        results_text = "\n".join(result_lines) if result_lines else "No results this turn."

        user_content = (
            f"Original goal: {session.goal}\n\n"
            f"Previous summary: {ctx.summary_text or 'None'}\n\n"
            f"This turn's results:\n{results_text}\n\n"
            "Is the original goal fully achieved?"
        )

        response = self._llm_client.send_message(
            messages=[LLMMessage(role="user", content=user_content)],
            system_prompt=_OBSERVER_SYSTEM_PROMPT,
            tools=[submit_observation.to_llm_tool()],
        )
        parsed_response = self._llm_client.parse_response(response)

        for tool_call in parsed_response.tool_calls:
            if tool_call.name == "submit_observation":
                inp = tool_call.input
                return ObserverVerdict(
                    done=bool(inp.get("done", False)),
                    summary=inp.get("summary", ""),
                    reasoning=inp.get("reasoning", ""),
                )

        raise RuntimeError("Observer: submit_observation not called")

    def _rule_observe(
        self,
        results: list[ActorResult],
        session: Session,
    ) -> ObserverVerdict:
        """降级规则：有结果但无法判断，返回 done=False 继续。"""
        all_success = all(r.success for r in results) if results else False
        token_pct = (
            session.token_used / session.token_budget
            if session.token_budget
            else 0
        )

        # Token 快耗尽时强制结束
        if token_pct > 0.9:
            return ObserverVerdict(
                done=True,
                summary="Token budget nearly exhausted; stopping.",
                reasoning="rule-based: token budget",
            )

        summary = (
            "Completed this turn's tasks." if all_success else "Some tasks failed this turn."
        )
        return ObserverVerdict(done=False, summary=summary, reasoning="rule-based fallback")
