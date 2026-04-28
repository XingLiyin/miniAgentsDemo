"""记忆压缩代理：调用 LLM（可使用只读工具）将历史消息压缩为结构化摘要。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.llm.types import LLMMessage
from app.runtime.prompt_builder import BasePromptBuilder

_prompt_builder = BasePromptBuilder()

if TYPE_CHECKING:
    from app.llm.base import BaseChatClient
    from app.tools.definition import ToolResult
    from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 8


class MemoryCompactionAgent:
    """单次无状态调用，产出结构化 summary。

    compact() 将 messages 切分为待压缩部分和保留部分：
      - 待压缩部分交给 LLM（可调用只读工具）生成摘要
      - 保留部分 = 最后 keep_last 条消息
    返回 (kept_messages, summary_text)，调用方负责将 summary 前置写回消息列表。
    """

    def __init__(
        self,
        llm_client: "BaseChatClient",
        tool_registry: "ToolRegistry",
        soul: str,
        tool_allowlist: list[str],
        keep_last: int = 6,
    ) -> None:
        self._llm_client = llm_client
        self._tool_registry = tool_registry
        self._soul = soul
        self._tool_allowlist = set(tool_allowlist)
        self._keep_last = keep_last

    def compact(
        self,
        messages: list[dict],
        session_goal: str = "",
        working_dir: str = "",
        session_id: str = "",
        agent_id: str = "",
    ) -> tuple[list[dict], str]:
        """压缩消息列表，返回 (kept_messages, summary_text)。

        消息数不超过 keep_last 时直接返回原列表，summary_text 为空。
        LLM 调用失败时记录日志并返回原列表，调用方可继续正常运行。
        """
        if not messages:
            return messages, ""

        if len(messages) > self._keep_last:
            to_compact = messages[:-self._keep_last]
            kept = messages[-self._keep_last:]
        else:
            to_compact = messages
            kept = []

        try:
            summary = self._run(to_compact, session_goal, working_dir, session_id, agent_id)
        except Exception:
            logger.exception("MemoryCompactionAgent: LLM call failed, falling back to truncation")
            return kept, ""

        if not summary:
            return kept, ""

        return kept, summary

    def _run(
        self,
        to_compact: list[dict],
        session_goal: str,
        working_dir: str,
        session_id: str,
        agent_id: str,
    ) -> str:
        from app.tools.definition import CallContext, ToolResult

        tools = self._tool_registry.to_llm_tools(list(self._tool_allowlist))
        ctx = CallContext(session_id=session_id, agent_id=agent_id, working_dir=working_dir)

        messages: list[LLMMessage] = [
            LLMMessage(role="user", content=_build_user_message(to_compact, session_goal))
        ]
        last_text = ""

        for _ in range(_MAX_TOOL_ROUNDS):
            full_text, tool_call_acc = _stream(self._llm_client, messages, self._soul, tools)
            parsed = self._llm_client.parse_stream_acc(full_text, tool_call_acc)
            last_text = full_text

            if not parsed.tool_calls:
                break

            messages = _prompt_builder.append_assistant_tool_calls(messages, full_text, parsed.tool_calls)

            for tc in parsed.tool_calls:
                result = _call_tool(tc.name, tc.input, ctx, self._tool_registry, self._tool_allowlist)
                messages = _prompt_builder.append_tool_result(messages, tc.name, result, tool_call_id=tc.id)

        return last_text


# ── 私有工具函数 ──────────────────────────────────────────────────────────────

def _stream(
    llm_client: "BaseChatClient",
    messages: list[LLMMessage],
    system_prompt: str,
    tools: list,
) -> tuple[str, dict]:
    full_text = ""
    tool_call_acc: dict[int, dict] = {}

    for chunk in llm_client.stream_message(messages=messages, system_prompt=system_prompt, tools=tools):
        if chunk.text_delta:
            full_text += chunk.text_delta
        if chunk.tool_call_delta:
            tool_call_acc[chunk.tool_call_delta["index"]] = chunk.tool_call_delta

    return full_text, tool_call_acc


def _call_tool(
    name: str,
    arguments: dict,
    ctx: "object",
    registry: "ToolRegistry",
    allowlist: set[str],
) -> "ToolResult":
    from app.tools.definition import ToolResult

    if name not in allowlist:
        return ToolResult(content=f"[Tool '{name}' not available in compaction context]", is_error=True)
    try:
        return registry.get(name).handler(arguments, ctx)
    except Exception as e:
        return ToolResult(content=str(e), is_error=True)


def _build_user_message(messages: list[dict], session_goal: str) -> str:
    from app.llm.types import content_to_text

    lines: list[str] = []
    if session_goal:
        lines.append(f"会话目标：{session_goal}\n")
    lines.append("需要压缩的对话历史：\n")
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            content = content_to_text(content)
        lines.append(f"[{role}]: {content}")
    return "\n".join(lines)
