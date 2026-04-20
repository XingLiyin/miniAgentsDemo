"""Observer：ReAct 循环，评估本轮执行结果。

LLM 在同一个循环中完成两步检查：
  1. 调用 submit_task_assessment 评估当前任务。
  2. 若任务列表中存在可复核条目，调用 submit_task_reviews 完成 reopen / skip 操作。

行为规则由 agent.role_md 承载；代码只负责循环驱动和上下文组装。
降级路径：role_md 为空或 LLM 调用失败时，退回规则判断。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.domain.models.session import Session
from app.llm.base import BaseChatClient
from app.llm.types import LLMMessage
from app.tools.definition import CallContext
from app.domain.services.task_service import TaskService
from app.runtime.types import (
    ActorResult,
    ObserverVerdict,
    ReasoningContext,
)

if TYPE_CHECKING:
    from app.domain.models.agent import Agent
    from app.domain.models.task import Task
    from app.runtime.tool_gateway import ToolGateway

logger = logging.getLogger(__name__)

# 无 role_md 时的身份兜底
_OBSERVER_ROLE_FALLBACK = (
    "You are an objective observer evaluating task execution results."
)


# ── Observer ───────────────────────────────────────────────────────────────────

class Observer:
    """ReAct 循环：一次连续的多轮 LLM 调用，完成任务评估和任务列表复核。

    降级路径：role_md 未配置或 LLM 调用失败时退回规则判断。
    """

    def __init__(
        self,
        llm_client: BaseChatClient,
        tool_gateway: "ToolGateway",
        task_svc: TaskService,
    ) -> None:
        self._llm_client = llm_client
        self._tool_gateway = tool_gateway
        self._task_svc = task_svc

    def observe(
        self,
        session: Session,
        result: ActorResult,
        ctx: ReasoningContext,
        task: "Task",
        task_list: list["Task"] | None = None,
        agent: "Agent | None" = None,
    ) -> ObserverVerdict:
        """评估本轮执行结果，返回 ObserverVerdict。

        若 agent 未配置 role_md（ctx.role 为空），跳过 LLM 直接走规则降级。
        """
        if not ctx.role:
            logger.debug("Observer: no role_md configured, using rule-based fallback")
            return self._rule_observe(result, session, task)
        try:
            return self._llm_observe(session, result, ctx, task, task_list or [], agent)
        except Exception as e:
            logger.warning("Observer LLM call failed, falling back to rules: %s", e)
            return self._rule_observe(result, session, task)

    # ── 私有：ReAct 循环主逻辑 ──────────────────────────────────────────────────

    def _llm_observe(
        self,
        session: Session,
        result: ActorResult,
        ctx: ReasoningContext,
        task: "Task",
        task_list: list["Task"],
        agent: "Agent | None" = None,
    ) -> ObserverVerdict:
        llm_client    = self._resolve_llm_client(agent)
        session_id    = task.session_id
        system_prompt = self._build_observer_system_prompt(ctx)
        messages      = self._build_observer_messages(session, result, ctx, task, task_list)
        tools         = [r.llm_tool for r in ctx.observer_resources if r.kind == "tool" and r.llm_tool is not None]
        toolcall_ctx  = CallContext(session_id=session_id, agent_id=task.assigned_agent_id, task=task)
        max_rounds    = agent.loop_guard.observer_max_tool_rounds if agent else 5

        siblings = [t for t in task_list if t.id != task.id]
        has_pending_siblings = any(t.status == "PENDING" for t in siblings)
        reviewable_count = (
            sum(1 for t in siblings if t.status in ("FINISHED", "PENDING"))
            if has_pending_siblings else 0
        )
        last_llm_text     = ""
        reviews_submitted = False

        for _round in range(max_rounds):
            _push_llm_event(session_id, f"observer_round_{_round}", system_prompt, messages, tools)
            full_text, tool_call_acc = _stream_observer(
                llm_client, messages, system_prompt, tools, session_id, f"observer_round_{_round}"
            )
            if full_text:
                last_llm_text = full_text

            tool_calls = _build_obs_tool_calls(tool_call_acc)

            if not tool_calls:
                break

            for tool_call in tool_calls:
                if tool_call.name == "submit_task_reviews":
                    reviews_submitted = True
                tool_result = self._tool_gateway.call(
                    tool_call.name, tool_call.input, None, task.id, toolcall_ctx
                )
                messages = _append_observer_tool_turn(messages, tool_call.name, tool_result)

            if task.status != "TO_BE_OBSERVED" and (not reviewable_count or reviews_submitted):
                break

        if task.status == "TO_BE_OBSERVED":
            raise RuntimeError("Observer: no assessment submitted by LLM")

        return ObserverVerdict(summary=last_llm_text or task.actor_summary or "")

    def _resolve_llm_client(self, agent: "Agent | None") -> BaseChatClient:
        """按 agent.llm_name 动态解析 LLM 客户端，缺省用注入的默认客户端。"""
        if agent is not None and agent.llm_name:
            try:
                from app.llm.registry import get_llm_registry
                return get_llm_registry().get_client(agent.llm_name)
            except Exception:
                pass
        return self._llm_client

    # ── 上下文构建 ─────────────────────────────────────────────────────────────

    def _build_observer_system_prompt(self, ctx: ReasoningContext) -> str:
        """组装 observer system prompt：role + skill instructions（如有）+ 工具列表。"""
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

    def _build_observer_messages(
        self,
        session: Session,
        result: ActorResult,
        ctx: ReasoningContext,
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
            f"User prompt that triggered this execution: {session.user_prompt}",
            f"Execution transcript ({len(result.conversation_turns)} round(s)):\n{transcript}",
        ]
        if reviewable:
            content_parts.append(
                f"Session task list:\n{self._build_task_list_section(reviewable)}"
            )

        return [LLMMessage(role="user", content="\n\n".join(content_parts))]

    # ── 私有辅助 ──────────────────────────────────────────────────────────────

    def _build_task_list_section(self, tasks: list["Task"]) -> str:
        """渲染任务列表为可读文本。"""
        lines: list[str] = []
        for t in tasks:
            result_hint = f" | result: {t.result[:120]}" if t.status == "FINISHED" and t.result else ""
            lines.append(f"  [{t.status}] {t.title}{result_hint}")
        return "\n".join(lines)

    def _build_transcript(self, result: ActorResult) -> str:
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

    def _rule_observe(
        self,
        result: ActorResult,
        session: Session,
        task: "Task",
    ) -> ObserverVerdict:
        """降级规则：observer LLM 不可用时使用。直接写入 task 状态，跳过任务复核。"""
        token_pct = (
            session.token_used / session.token_budget
            if session.token_budget
            else 0
        )

        if token_pct > 0.9:
            summary = "Token budget nearly exhausted; stopping."
            self._task_svc.finish(task.id, result="Token budget nearly exhausted; treating as complete.")
            return ObserverVerdict(summary=summary)

        if result.success:
            summary = "Completed this turn's task."
            self._task_svc.finish(task.id, result=result.output or "")
        else:
            summary = "Task failed this turn."
            self._task_svc.fail(task.id, error=result.output or result.error or "")
        return ObserverVerdict(summary=summary)


# ── 模块级工具 ─────────────────────────────────────────────────────────────────

def _append_observer_tool_turn(
    messages: list[LLMMessage],
    tool_name: str,
    tool_result: object,
) -> list[LLMMessage]:
    """将工具调用结果追加到消息历史，供下一轮 LLM 决策。"""
    is_error = getattr(tool_result, "is_error", False)
    content  = getattr(tool_result, "content", "") or ""
    prefix   = f"Tool '{tool_name}' error" if is_error else f"Tool '{tool_name}' result"
    messages.append(LLMMessage(role="user", content=f"{prefix}:\n{content}"))
    return messages


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
