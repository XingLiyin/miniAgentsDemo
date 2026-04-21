"""Prompt builder: actor 和 observer 的提示词构建逻辑。

PromptBuilderFactory 是唯一创建入口：
  PromptBuilderFactory.for_actor()    → ActorPromptBuilder
  PromptBuilderFactory.for_observer() → ObserverPromptBuilder

BasePromptBuilder 持有两者共用的工具方法。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.llm.types import LLMMessage, ToolCallBlock

if TYPE_CHECKING:
    from app.domain.models.session import Session
    from app.domain.models.task import Task
    from app.runtime.types import ActorResult, ReasoningContext

logger = logging.getLogger(__name__)


# ── 基类：共享工具方法 ─────────────────────────────────────────────────────────

class BasePromptBuilder:
    """两个 builder 共用的工具方法。"""

    def append_tool_result(
        self,
        messages: list[LLMMessage],
        tool_name: str,
        tool_result: object,
    ) -> list[LLMMessage]:
        """将工具调用结果追加到消息历史。"""
        is_error = getattr(tool_result, "is_error", False)
        content  = getattr(tool_result, "content", "") or ""
        prefix   = f"Tool '{tool_name}' error" if is_error else f"Tool '{tool_name}' result"
        messages.append(LLMMessage(role="user", content=f"{prefix}:\n{content}"))
        return messages

    def sanitize_messages(self, messages: list[LLMMessage]) -> list[LLMMessage]:
        """过滤空白消息，合并连续同角色消息。"""
        filtered = [m for m in messages if m.content and m.content.strip()]
        merged: list[LLMMessage] = []
        for m in filtered:
            if merged and merged[-1].role == m.role:
                merged[-1].content = merged[-1].content + "\n\n" + m.content
            else:
                merged.append(LLMMessage(role=m.role, content=m.content))
        return merged

    def build_tool_calls_from_stream(self, acc: dict[int, dict]) -> list[ToolCallBlock]:
        """将流式 tool_call 累积缓冲区转换为 ToolCallBlock 列表。"""
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


# ── Actor prompt builder ───────────────────────────────────────────────────────

class ActorPromptBuilder(BasePromptBuilder):
    """构建 actor 的 system prompt 和 messages。"""

    def build_system_prompt(self, ctx: "ReasoningContext") -> str:
        """组装 system prompt：soul → 资源列表 → skill instructions。"""
        parts = [p for p in [ctx.soul, self._build_resources_section(ctx), ctx.skill_instructions] if p]
        return "\n\n---\n\n".join(parts)

    def build_messages(self, task: "Task", ctx: "ReasoningContext") -> list[LLMMessage]:
        """组装 actor messages：历史记忆 + 当前 task 指令。"""
        messages: list[LLMMessage] = []

        recent_messages = (
            ctx.recent_messages[:-1]
            if ctx.recent_messages and ctx.recent_messages[-1].get("role") == "user"
            else ctx.recent_messages
        )
        for m in recent_messages:
            messages.append(LLMMessage(role=m.get("role", "user"), content=m.get("content", "")))

        parts: list[str] = []
        if ctx.blackboard_snippets:
            parts.append("Task Background:\n" + "\n".join(f"- {s}" for s in ctx.blackboard_snippets))
        if task.title and task.description:
            parts.append(f"Current goal: {task.title}\nDescription: {task.description}")
        if ctx.summary_text:
            parts.append(f"Previous progress:\n{ctx.summary_text}")
        parts.append(f"Current message: {ctx.current_task.user_prompt}")

        messages.append(LLMMessage(role="user", content="\n".join(parts)))
        return messages

    def _build_resources_section(self, ctx: "ReasoningContext") -> str:
        """将 ctx.actor_resources 按 kind 分组渲染为 system prompt 段落。"""
        skills = [r for r in ctx.actor_resources if r.kind == "skill"]
        tools  = [r for r in ctx.actor_resources if r.kind == "tool" and r.llm_tool is not None]
        parts: list[str] = []
        if skills:
            lines = ["## Available Skills (assign to tasks where appropriate)"]
            for r in skills:
                lines.append(f"- {r.name}: {r.description}")
            parts.append("\n".join(lines))
        if tools:
            lines = ["## Available Tools (use them via tool calls)"]
            for r in tools:
                lines.append(f"- {r.llm_tool.to_prompt_text()}")
            parts.append("\n".join(lines))
        return "\n\n".join(parts)


# ── Observer prompt builder ────────────────────────────────────────────────────

_OBSERVER_ROLE_FALLBACK = "You are an objective observer evaluating task execution results."


class ObserverPromptBuilder(BasePromptBuilder):
    """构建 observer 的 system prompt 和 messages。"""

    def build_system_prompt(self, ctx: "ReasoningContext") -> str:
        """组装 observer system prompt：role + skill instructions + 工具列表。"""
        role  = ctx.role or _OBSERVER_ROLE_FALLBACK
        tools = [r for r in ctx.observer_resources if r.kind == "tool" and r.llm_tool is not None]
        parts = [role]
        if ctx.skill_instructions:
            parts.append(
                "## Skill Instructions for This Task\n\n"
                "The task was executed under the following skill. "
                "Use these instructions to calibrate your evaluation criteria and emphasis. "
                "You may also call skill-related tools listed below to gather more information before submitting your assessment.\n\n"
                + ctx.skill_instructions
            )
        if tools:
            lines = ["## Available Tools"]
            for r in tools:
                lines.append(f"- {r.llm_tool.to_prompt_text()}")
            parts.append("\n".join(lines))
        return "\n\n---\n\n".join(parts)

    def build_messages(
        self,
        session: "Session",
        result: "ActorResult",
        ctx: "ReasoningContext",
        task: "Task",
        task_list: list["Task"],
    ) -> list[LLMMessage]:
        """组装 observer 初始 user message，包含评估和复核所需的全部上下文。"""
        transcript = self._build_transcript(result)
        siblings = [t for t in task_list if t.id != task.id]
        has_pending = any(t.status == "PENDING" for t in siblings)
        reviewable = (
            [t for t in siblings if t.status in ("FINISHED", "PENDING")]
            if has_pending else []
        )

        content_parts = [
            f"Previous progress summary: {ctx.summary_text or 'None'}",
            (
                f"Current task: {task.title}\n"
                f"Task description: {task.description or task.title}"
            ),
            f"User requirements: {session.user_prompt}",
            f"Execution transcript ({len(result.conversation_turns)} round(s)):\n{transcript}",
        ]
        if reviewable:
            content_parts.append(
                f"Session task list:\n{self._build_task_list_section(reviewable)}"
            )

        return [LLMMessage(role="user", content="\n\n".join(content_parts))]

    def _build_transcript(self, result: "ActorResult") -> str:
        """将 conversation_turns 展开为可读文本，供 LLM 评估。"""
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

    def _build_task_list_section(self, tasks: list["Task"]) -> str:
        """渲染任务列表为可读文本。"""
        lines: list[str] = []
        for t in tasks:
            result_hint = f" | result: {t.result[:120]}" if t.status == "FINISHED" and t.result else ""
            lines.append(f"  [{t.status}] {t.title}{result_hint}")
        return "\n".join(lines)


# ── 工厂 ──────────────────────────────────────────────────────────────────────

class PromptBuilderFactory:
    """创建 prompt builder 实例的唯一入口。"""

    @staticmethod
    def for_actor() -> ActorPromptBuilder:
        return ActorPromptBuilder()

    @staticmethod
    def for_observer() -> ObserverPromptBuilder:
        return ObserverPromptBuilder()
