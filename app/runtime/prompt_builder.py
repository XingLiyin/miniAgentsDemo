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
        messages: list[LLMMessage] = []

        if task.user_prompt_in_memory:
            # Resume 路径：user message 已以包装形式存在 memory，直接使用历史
            for m in ctx.recent_messages:
                messages.append(LLMMessage(
                    role=m.get("role", "user"),
                    content=m.get("content", ""),
                    tool_calls=m.get("tool_calls"),
                    tool_call_id=m.get("tool_call_id", ""),
                    reasoning_content=m.get("reasoning_content"),
                ))
            # suspend 后 resume：末尾是 assistant，追加 blackboard 上下文
            if ctx.blackboard_snippets and messages and messages[-1].role != "user":
                bb = "## Task Background\n" + "\n".join(
                    f"- {content_to_text(s)}" for s in ctx.blackboard_snippets
                )
                messages.append(LLMMessage(role="user", content=bb))
            return messages

        # 非 resume 路径（daemon 或无 user_prompt）：构建完整 user message
        for m in ctx.recent_messages:
            messages.append(LLMMessage(
                role=m.get("role", "user"),
                content=m.get("content", ""),
                tool_calls=m.get("tool_calls"),
                tool_call_id=m.get("tool_call_id", ""),
                reasoning_content=m.get("reasoning_content"),
            ))
        parts: list[str] = []
        if ctx.blackboard_snippets:
            parts.append("## Task Background\n" + "\n".join(f"- {content_to_text(s)}" for s in ctx.blackboard_snippets))
        if task.title and task.description:
            parts.append(f"## Current Goal\n{task.title}\n{task.description}")
        user_prompt = ctx.current_task.user_prompt
        if user_prompt:
            parts.append(f"## Current Message\n{content_to_text(user_prompt)}")
        if parts:
            text_content = "\n\n".join(parts)
            if isinstance(user_prompt, list):
                image_parts = [p for p in content_from_raw(user_prompt) if isinstance(p, ImagePart)]
                msg_content: str | list = ([*image_parts, TextPart(text=text_content)] if image_parts else text_content)
            else:
                msg_content = text_content
            messages.append(LLMMessage(role="user", content=msg_content))
        return messages

    def build_initial_user_content(self, task: "Task") -> "str | list":
        """不依赖 ctx，仅用 task 信息构建初始 user message 内容（写入 memory 用）。"""
        parts: list[str] = []
        if task.title and task.description:
            parts.append(f"## Current Goal\n{task.title}\n{task.description}")
        user_prompt = task.user_prompt
        if user_prompt:
            parts.append(f"## Current Message\n{content_to_text(user_prompt)}")
        text_content = "\n\n".join(parts)
        if isinstance(user_prompt, list):
            image_parts = [p for p in content_from_raw(user_prompt) if isinstance(p, ImagePart)]
            return ([*image_parts, TextPart(text=text_content)] if image_parts else text_content)
        return text_content

    def _build_resources_section(self, ctx: "ReasoningContext") -> str:
        """将 ctx.actor_resources 按 kind 分组渲染为 system prompt 段落。"""
        skills = [r for r in ctx.actor_resources if r.kind == "skill"]
        tools  = [r for r in ctx.actor_resources if r.kind == "tool" and r.llm_tool is not None]
        agents = [r for r in ctx.actor_resources if r.kind == "agent"]
        parts: list[str] = []
        if skills and not ctx.skill_instructions:  # 已有技能资源但无 skill instructions，才渲染技能列表（否则可能重复）
            lines = [
                "## Available Skills (assign to tasks where appropriate)",
                "Delegate via: submit_task(skill_name='<name>')"
            ]
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
        """组装 observer 的多轮消息：当前 task 的前序执行轮次 + 本轮执行（真实对话轮次）+ 评估请求。

        消息结构（保持对话顺序，避免 user/eval 相邻被 sanitize 合并）：
          [memory: 当前 task 的前序轮次（user_prompt / assistant 回复 / 用户答复）]
          [本轮执行：result.conversation_turns 还原为 assistant(+tool) 轮次]
          user: 评估请求（任务/要求/任务列表；transcript 已作为真实轮次呈现，故不再内嵌）
        """
        siblings = [t for t in task_list if t.id != task.id]
        has_pending = any(t.status == "PENDING" for t in siblings)
        reviewable = (
            [t for t in siblings if t.status in ("FINISHED", "PENDING")]
            if has_pending else []
        )

        messages: list[LLMMessage] = []
        # 1) 仅「当前 task」的前序执行轮次（不含其它 task 的 memory 历史）。
        #    本轮的 assistant 摘要此时尚未写入 memory，所以这里只含真正的前序轮次。
        for m in ctx.recent_messages:
            if m.get("task_id") != task.id:
                continue
            messages.append(LLMMessage(
                role=m.get("role", "user"),
                content=m.get("content", ""),
                tool_calls=m.get("tool_calls"),
                tool_call_id=m.get("tool_call_id", ""),
                reasoning_content=m.get("reasoning_content"),
            ))
        # 2) 本轮执行过程，还原为真实对话轮次，紧跟前序轮次之后（自然把前序 user 答复与评估请求隔开）。
        messages.extend(self._result_to_messages(result))

        # 3) 评估请求作为最后一条 user 消息。
        content_parts = [
            (
                f"Current task: {task.title}\n"
                f"Task description: {task.description or task.title}"
            ),
            f"Sub-task results:\n" + "\n".join(f"- {content_to_text(s)}" for s in ctx.blackboard_snippets) if ctx.blackboard_snippets else "No sub-tasks.",
            f"User requirements: {task.user_prompt if task.user_prompt else session.user_prompt}",
            "Please assess the latest execution shown in the conversation above.",
        ]
        if reviewable:
            content_parts.append(
                f"Session task list:\n{self._build_task_list_section(reviewable)}"
            )
        messages.append(LLMMessage(role="user", content="\n\n".join(content_parts)))
        return messages

    def _result_to_messages(self, result: "ActorResult") -> list[LLMMessage]:
        """把本轮 conversation_turns 还原为真实对话轮次：assistant(text+tool_calls) + tool(result)。"""
        msgs: list[LLMMessage] = []
        for turn in result.conversation_turns:
            tool_calls = [
                {"id": tc.tool_call_id, "name": tc.tool_name, "input": tc.arguments}
                for tc in turn.tool_calls
            ]
            if turn.llm_text or tool_calls:
                msgs.append(LLMMessage(
                    role="assistant",
                    content=turn.llm_text or "",
                    tool_calls=tool_calls or None,
                ))
            for tc in turn.tool_calls:
                content = f"[ERROR] {tc.result}" if tc.is_error else tc.result
                msgs.append(LLMMessage(role="tool", content=content, tool_call_id=tc.tool_call_id))
        # 没有结构化轮次时退化为一条 assistant 文本。
        if not msgs and result.output:
            msgs.append(LLMMessage(role="assistant", content=result.output))
        return msgs

    def _build_task_list_section(self, tasks: list["Task"]) -> str:
        """渲染任务列表为可读文本。"""
        lines: list[str] = []
        for t in tasks:
            result_hint = f" | result: {t.process_report[:120]}" if t.status == "FINISHED" and t.process_report else ""
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
