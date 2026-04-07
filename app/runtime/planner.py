"""Planner：单轮 LLM 调用，将目标分解为有序任务列表。"""

from __future__ import annotations

import logging
from typing import Annotated

from app.domain.models.agent import Agent
from app.llm.base import BaseChatClient
from app.llm.types import LLMMessage
from app.runtime.types import PlannedTask, ReasoningContext, TaskPlan
from app.tools.definition import ToolResult
from app.tools.tool_decorator import tool_result

logger = logging.getLogger(__name__)

_FALLBACK_SYSTEM_PROMPT = (
    "You are a planning agent. Your job is to decompose the goal into an ordered "
    "task list by calling submit_plan exactly once.\n"
    "Each task should describe WHAT to achieve, not HOW (no tool names or arguments).\n"
    "Set skill_name only when one of the available skills directly matches the task; "
    "otherwise leave it null."
)


# ── submit_plan ────────────────────────────────────────────────────────────────

@tool_result
def submit_plan(
    tasks: Annotated[
        list[PlannedTask],
        (
            "Ordered list of tasks for this turn. Empty list means the goal is already complete. "
            "Each task: "
            "  title: short imperative title; "
            "  description: WHAT to achieve — not HOW, no tool names or arguments; "
            "  skill_name: one of the available skills, or null."
        ),
    ],
) -> ToolResult:
    """Submit the decomposed task plan. Call exactly once per turn."""
    return ToolResult(content="")


class Planner:
    """One-turn planner: receives ReasoningContext, returns TaskPlan."""

    def __init__(
        self,
        llm_client: BaseChatClient,
        system_prompt: str = "",
    ) -> None:
        self._llm_client = llm_client
        self._system_prompt = system_prompt or _FALLBACK_SYSTEM_PROMPT

    def plan(self, ctx: ReasoningContext, agent: Agent) -> TaskPlan:
        """Run one planning round and return a TaskPlan."""
        system_prompt = self._build_system_prompt(ctx, agent)
        messages = self._build_messages(ctx)

        response = self._llm_client.send_message(
            messages=messages,
            system_prompt=system_prompt,
            tools=[submit_plan.to_llm_tool()],
        )
        parsed_response = self._llm_client.parse_response(response)

        for tool_call in parsed_response.tool_calls:
            if tool_call.name == "submit_plan":
                return self._parse_task_plan(tool_call.input)

        logger.warning("Planner: submit_plan not called; returning empty plan")
        return TaskPlan(tasks=[])

    # ── 私有辅助 ──────────────────────────────────────────────────────────────

    def _build_system_prompt(self, ctx: ReasoningContext, agent: Agent) -> str:
        agent_base = agent.system_prompt 
        planner_soul = self._system_prompt  
        parts = [agent_base, planner_soul]

        if ctx.relevant_skills:
            lines = ["## Available Skills (assign to tasks where appropriate)"]
            for skill in ctx.relevant_skills:
                lines.append(f"- {skill.name}: {skill.description}")
            parts.append("\n".join(lines))

        parts.append(
            "Your job: decompose the goal into an ordered task list.\n"
            "- 'description' describes WHAT to achieve, not HOW. 'description' must clearly and explicitly describe the intended objective of the task, and must not be null.\n"
            "- 'skill_name' should be set if a skill fits the task; otherwise null. 'skill_name' must be strictly selected from the provided Available Skills list; do not invent or use any names outside of that list.\n"
            "- Do NOT specify tool names, arguments, or message content."
        )
        return "\n\n---\n\n".join(parts)

    def _build_messages(self, ctx: ReasoningContext) -> list[LLMMessage]:
        user_content = f"**目标**: {ctx.goal}"
        if ctx.summary_text:
            user_content = f"**过去任务总结**:\n{ctx.summary_text}\n\n{user_content}"
        if ctx.blackboard_snippets:
            bb_text = "\n".join(f"- {s}" for s in ctx.blackboard_snippets)
            user_content = f"**额外信息**:\n{bb_text}\n\n{user_content}"

        messages: list[LLMMessage] = [LLMMessage(role="user", content=user_content)]

        for m in ctx.recent_messages:
            messages.append(
                LLMMessage(role=m.get("role", "user"), content=m.get("content", ""))
            )

        return messages

    def _parse_task_plan(self, raw: dict) -> TaskPlan:
        tasks: list[PlannedTask] = []
        for spec in raw.get("tasks", []):
            tasks.append(PlannedTask(
                title=spec.get("title", ""),
                description=spec.get("description", ""),
                skill_name=spec.get("skill_name") or None,
            ))
        return TaskPlan(tasks=tasks)


