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
from app.runtime.prompt_builder import ObserverPromptBuilder, PromptBuilderFactory
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
        prompt_builder: ObserverPromptBuilder | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._tool_gateway = tool_gateway
        self._task_svc = task_svc
        self._prompt_builder = prompt_builder or PromptBuilderFactory.for_observer()

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
        system_prompt = self._prompt_builder.build_system_prompt(ctx)
        messages      = self._prompt_builder.build_messages(session, result, ctx, task, task_list)
        tools         = [r.llm_tool for r in ctx.observer_resources if r.kind == "tool" and r.llm_tool is not None]
        toolcall_ctx  = CallContext(session_id=session_id, agent_id=task.assigned_agent_id, task=task)
        max_rounds    = agent.loop_guard.observer_max_tool_rounds if agent else 5

        last_llm_text     = ""
        reviews_submitted = False

        for _round in range(max_rounds):
            _push_llm_event(session_id, f"observer_round_{_round}", system_prompt, messages, tools)
            full_text, tool_call_acc = _stream_observer(
                llm_client, messages, system_prompt, tools, session_id, f"observer_round_{_round}"
            )
            if full_text:
                last_llm_text = full_text

            tool_calls = self._prompt_builder.build_tool_calls_from_stream(tool_call_acc)

            if not tool_calls:
                break

            messages = self._prompt_builder.append_assistant_tool_calls(
                messages, full_text, tool_calls
            )
            for tool_call in tool_calls:
                if tool_call.name == "submit_task_reviews":
                    reviews_submitted = True
                tool_result = self._tool_gateway.call(
                    tool_call.name, tool_call.input, None, task.id, toolcall_ctx
                )
                messages = self._prompt_builder.append_tool_result(
                    messages, tool_call.name, tool_result, tool_call_id=tool_call.id
                )

            if task.status != "TO_BE_OBSERVED":
                live_siblings = [
                    t for t in self._task_svc.list_by_session(session_id)
                    if t.id != task.id and t.assigned_agent_id == task.assigned_agent_id
                ]
                has_pending = any(t.status == "PENDING" for t in live_siblings)
                live_reviewable = has_pending and any(
                    t.status in ("FINISHED", "PENDING") for t in live_siblings
                )
                if not live_reviewable or reviews_submitted:
                    break

        if task.status == "TO_BE_OBSERVED":
            raise RuntimeError("Observer: no assessment submitted by LLM")

        return ObserverVerdict(summary=last_llm_text or task.actor_summary or "")

    def _resolve_llm_client(self, agent: "Agent | None") -> BaseChatClient:
        """按 agent.llm_name 动态解析 LLM 客户端，缺省用注入的默认客户端。"""
        if agent is not None and agent.llm_provider:
            try:
                from app.llm.registry import get_llm_registry
                return get_llm_registry().get_client(agent.llm_provider)
            except Exception:
                pass
        return self._llm_client

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
        from app.common.sse_bus import get_sse_bus
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
        from app.common.sse_bus import get_sse_bus
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


