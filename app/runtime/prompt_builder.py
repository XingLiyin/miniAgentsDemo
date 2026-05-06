"""Prompt builder: actor 和 observer 的提示词构建逻辑。

PromptBuilderFactory 是唯一创建入口：
  PromptBuilderFactory.for_actor()    → ActorPromptBuilder
  PromptBuilderFactory.for_observer() → ObserverPromptBuilder

BasePromptBuilder 持有两者共用的工具方法。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.llm.types import ImagePart, LLMMessage, TextPart, ToolCallBlock, content_from_raw, content_to_text

if TYPE_CHECKING:
    from app.domain.models.session import Session
    from app.domain.models.task import Task
    from app.runtime.types import ActorResult, ReasoningContext

logger = logging.getLogger(__name__)


# ── 基类：共享工具方法 ─────────────────────────────────────────────────────────

class BasePromptBuilder:
    """两个 builder 共用的工具方法。"""

    def append_assistant_tool_calls(
        self,
        messages: list[LLMMessage],
        full_text: str,
        tool_calls: list[ToolCallBlock],
        reasoning_content: str | None = None,
    ) -> list[LLMMessage]:
        """在工具结果之前插入 assistant 工具调用消息（provider 协议要求配对）。"""
        messages.append(LLMMessage(
            role="assistant",
            content=full_text,
            tool_calls=[
                {"id": tc.id, "name": tc.name, "input": tc.input}
                for tc in tool_calls
            ],
            reasoning_content=reasoning_content,
        ))
        return messages

    def append_tool_result(
        self,
        messages: list[LLMMessage],
        tool_name: str,
        tool_result: object,
        tool_call_id: str = "",
    ) -> list[LLMMessage]:
        """将工具调用结果追加到消息历史。"""
        is_error = getattr(tool_result, "is_error", False)
        content: str | list = getattr(tool_result, "content", "") or ""
        if is_error:
            text = content_to_text(content) if isinstance(content, list) else content
            content = f"[ERROR] {text}"
        messages.append(LLMMessage(role="tool", content=content, tool_call_id=tool_call_id))
        return messages

    def sanitize_messages(self, messages: list[LLMMessage]) -> list[LLMMessage]:
        """过滤空白消息，合并连续同角色消息（tool/assistant 不合并）。"""
        filtered = [
            m for m in messages
            if (m.content and content_to_text(m.content).strip()) or m.tool_calls or m.role == "tool"
        ]
        merged: list[LLMMessage] = []
        for m in filtered:
            if (merged and merged[-1].role == m.role
                    and m.role not in ("tool", "assistant")):
                prev_parts = content_from_raw(merged[-1].content) if isinstance(merged[-1].content, list) else None
                curr_parts = content_from_raw(m.content) if isinstance(m.content, list) else None
                prev_images = [p for p in prev_parts if isinstance(p, ImagePart)] if prev_parts else []
                curr_images = [p for p in curr_parts if isinstance(p, ImagePart)] if curr_parts else []
                all_images = prev_images + curr_images
                merged_text = content_to_text(merged[-1].content) + "\n\n" + content_to_text(m.content)
                merged[-1].content = ([*all_images, TextPart(text=merged_text)] if all_images else merged_text)
            else:
                merged.append(LLMMessage(
                    role=m.role,
                    content=m.content,
                    tool_call_id=m.tool_call_id,
                    tool_calls=m.tool_calls,
                    reasoning_content=m.reasoning_content,
                ))
        return merged



# ── Actor prompt builder ───────────────────────────────────────────────────────

class ActorPromptBuilder(BasePromptBuilder):
    """构建 actor 的 system prompt 和 messages。"""

    def build_system_prompt(self, ctx: "ReasoningContext") -> str:
        """组装 system prompt：soul → 项目背景 → 资源列表 → skill instructions。"""
        parts = [p for p in [
            ctx.soul,
            f"## Project Background\n\n{ctx.project_background}" if ctx.project_background else "",
            self._build_resources_section(ctx),
            ctx.skill_instructions,
        ] if p]
        return "\n\n---\n\n".join(parts)

    def build_messages(self, task: "Task", ctx: "ReasoningContext") -> list[LLMMessage]:
        """组装 actor messages：历史记忆 + 当前 task 指令。

        Resume 场景（memory 里已有 task_id == task.id 的记录）：直接还原 memory，
        并追加一条"子任务已完成，请继续"的 user 消息，不重复注入 user_prompt。
        """
        messages: list[LLMMessage] = []

        # 检测 resume：memory 里存在本 task 的挂起记录
        is_resume = any(m.get("task_id") == task.id for m in ctx.recent_messages)

        if is_resume:
            for m in ctx.recent_messages:
                messages.append(LLMMessage(
                    role=m.get("role", "user"),
                    content=content_from_raw(m.get("content", "")),
                    tool_call_id=m.get("tool_call_id"),
                    tool_calls=m.get("tool_calls"),
                ))
            return messages

        recent_messages = (
            ctx.recent_messages[:-1]
            if ctx.recent_messages and ctx.recent_messages[-1].get("role") == "user"
            else ctx.recent_messages
        )
        for m in recent_messages:
            messages.append(LLMMessage(
                role=m.get("role", "user"),
                content=content_from_raw(m.get("content", "")),
                tool_call_id=m.get("tool_call_id"),
                tool_calls=m.get("tool_calls"),
            ))

        parts: list[str] = []
        if ctx.blackboard_snippets:
            parts.append("Task Background:\n" + "\n".join(f"- {content_to_text(s)}" for s in ctx.blackboard_snippets))
        if task.title and task.description:
            parts.append(f"Current goal: {task.title}\nDescription: {task.description}")

        user_prompt = ctx.current_task.user_prompt
        parts.append(f"Current message: {content_to_text(user_prompt)}")
        text_content = "\n".join(parts)

        if isinstance(user_prompt, list):
            image_parts = [p for p in content_from_raw(user_prompt) if isinstance(p, ImagePart)]
            msg_content = ([*image_parts, TextPart(text=text_content)] if image_parts else text_content)
        else:
            msg_content = text_content

        messages.append(LLMMessage(role="user", content=msg_content))
        return messages

    def _build_resources_section(self, ctx: "ReasoningContext") -> str:
        """将 ctx.actor_resources 按 kind 分组渲染为 system prompt 段落。"""
        skills = [r for r in ctx.actor_resources if r.kind == "skill"]
        tools  = [r for r in ctx.actor_resources if r.kind == "tool" and r.llm_tool is not None]
        agents = [r for r in ctx.actor_resources if r.kind == "agent"]
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
        if agents:
            lines = [
                "## Available Sub-Agents",
                "Delegate via: submit_task(use_subagent=True, subagent_template='<name>')",
            ]
            for r in agents:
                lines.append(f"- {r.name}: {r.description}")
            parts.append("\n".join(lines))
        return "\n\n".join(parts)


# ── Observer prompt builder ────────────────────────────────────────────────────

_OBSERVER_ROLE_FALLBACK = "You are an objective observer evaluating task execution results."


class ObserverPromptBuilder(BasePromptBuilder):
    """构建 observer 的 system prompt 和 messages。"""

    def build_system_prompt(self, ctx: "ReasoningContext") -> str:
        """组装 observer system prompt：role + 工具列表。"""
        role  = ctx.role or _OBSERVER_ROLE_FALLBACK
        tools = [r for r in ctx.observer_resources if r.kind == "tool" and r.llm_tool is not None]
        parts = [role]
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
            (
                f"Current task: {task.title}\n"
                f"Task description: {task.description or task.title}"
            ),
            f"Sub-task results:\n" + "\n".join(f"- {content_to_text(s)}" for s in ctx.blackboard_snippets) if ctx.blackboard_snippets else "No sub-tasks.",
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
