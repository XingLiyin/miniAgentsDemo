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
from app.llm.types import LLMMessage
from app.tools.types import CallContext
from app.domain.services.session_service import SessionService
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
    from app.llm.base import BaseChatClient
    from app.runtime.tool_gateway import ToolGateway

logger = logging.getLogger(__name__)


# ── Observer ───────────────────────────────────────────────────────────────────

class Observer:
    """ReAct 循环：一次连续的多轮 LLM 调用，完成任务评估和任务列表复核。

    降级路径：role_md 未配置或 LLM 调用失败时退回规则判断。
    """

    def __init__(
        self,
        tool_gateway: "ToolGateway",
        task_svc: TaskService,
        session_svc: SessionService | None = None,
        prompt_builder: ObserverPromptBuilder | None = None,
    ) -> None:
        self._tool_gateway = tool_gateway
        self._task_svc = task_svc
        self._session_svc = session_svc
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
        if agent.spawn_depth  == 0:
            logger.debug("Observer: spawn_depth=0, skipping observation and marking task done")
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
        llm_client    = self._resolve_llm_client(session)
        session_id    = task.session_id
        system_prompt = self._prompt_builder.build_system_prompt(ctx)
        messages      = self._prompt_builder.build_messages(session, result, ctx, task, task_list)
        tools         = [r.llm_tool for r in ctx.observer_resources if r.kind == "tool" and r.llm_tool is not None]
        _wd = (
            (task.settings.get("working_dir") if task.settings else None)
            or agent.settings.get("working_dir")
            or ""
        )
        from app.config.settings import resolve_working_dir
        toolcall_ctx  = CallContext(session_id=session_id, agent_id=task.assigned_agent_id, task=task, working_dir=resolve_working_dir(_wd))
        max_rounds    = agent.loop_guard.observer_max_tool_rounds if agent else 5

        last_llm_text       = ""
        max_context_tokens  = 0
        last_prompt_tokens  = result.context_tokens or 0
        is_daemon           = bool(task.settings.get("_daemon") if task.settings else False)

        for _round in range(max_rounds):
            from app.runtime.actor import _compute_max_tokens
            round_max_tokens = _compute_max_tokens(llm_client, last_prompt_tokens) if last_prompt_tokens else None
            _push_llm_event(session_id, f"observer_round_{_round}", system_prompt, messages, tools, task, is_daemon)
            full_text, tool_call_acc, image_acc, _usage = _stream_observer(
                llm_client, messages, system_prompt, tools, session_id, f"observer_round_{_round}", round_max_tokens, is_daemon
            )
            if _usage:
                if self._session_svc and session_id:
                    self._session_svc.add_tokens(
                        session_id,
                        input_tokens=_usage.prompt_tokens or 0,
                        output_tokens=_usage.completion_tokens or 0,
                        context_tokens=_usage.prompt_tokens or 0,
                    )
                if _usage.prompt_tokens:
                    max_context_tokens = max(max_context_tokens, _usage.prompt_tokens)
            if full_text:
                last_llm_text = full_text

            tool_calls = llm_client.parse_stream_acc(full_text, tool_call_acc, images=image_acc).tool_calls

            if not tool_calls:
                break

            messages = self._prompt_builder.append_assistant_tool_calls(
                messages, full_text, tool_calls
            )
            tool_results_text = ""
            for tool_call in tool_calls:
                tool_result = self._tool_gateway.call(
                    tool_call.name, tool_call.input, agent.observer, task.id, toolcall_ctx
                )
                messages = self._prompt_builder.append_tool_result(
                    messages, tool_call.name, tool_result, tool_call_id=tool_call.id
                )
                from app.llm.types import content_to_text
                tool_results_text += content_to_text(tool_result.content) if isinstance(tool_result.content, list) else (tool_result.content or "")

            if _usage and _usage.prompt_tokens:
                from app.common.utils import estimate_tokens
                last_prompt_tokens = _usage.prompt_tokens + estimate_tokens(tool_results_text)
                if task.status != "TO_BE_OBSERVED":
                    break

            if task.status != "TO_BE_OBSERVED":
                break

        if task.status == "TO_BE_OBSERVED":
            raise RuntimeError("Observer: no assessment submitted by LLM")

        if task.process_report and task.error:
            summary = f"{task.process_report}\n\nError encountered: {task.error}"
        else:
            summary = task.process_report or task.error or last_llm_text or ""
        return ObserverVerdict(
            summary=summary,
            context_tokens=max_context_tokens,
        )

    def _resolve_llm_client(self, session: Session) -> "BaseChatClient":
        from app.config.settings import get_settings
        from app.llm.registry import get_llm_registry
        provider = session.llm_provider or get_settings().default_llm_provider
        return get_llm_registry().get_client(provider, session.llm_model or None)

    def _rule_observe(
        self,
        result: ActorResult,
        session: Session,
        task: "Task",
    ) -> ObserverVerdict:
        """降级规则：observer LLM 不可用时使用。直接写入 task 状态，跳过任务复核。"""
        token_pct = (
            session.output_tokens_used / session.token_budget
            if session.token_budget
            else 0
        )

        process_report = self._build_rule_report(result, token_exhausted=token_pct > 0.9)

        if token_pct > 0.9:
            self._task_svc.finish(task.id, process_report=process_report, outputs=result.output or "", session_id=task.session_id)
            return ObserverVerdict(summary=process_report)

        if result.success:
            self._task_svc.finish(task.id, process_report=process_report, outputs=result.output or "", session_id=task.session_id)
        else:
            self._task_svc.fail(task.id, error=result.error or result.output or "", session_id=task.session_id)
            task = self._task_svc.get(task.id, task.session_id)
            task.process_report = process_report
            task.outputs = result.output or ""
            self._task_svc.save(task)
        return ObserverVerdict(summary=process_report)

    @staticmethod
    def _build_rule_report(result: ActorResult, *, token_exhausted: bool = False) -> str:
        """从 ActorResult 构造规则降级时的 process_report 摘要。"""
        rounds = len(result.conversation_turns)
        all_calls = result.tool_calls_made

        tools_used: list[str] = []
        seen: set[str] = set()
        for tc in all_calls:
            if tc.tool_name not in seen:
                tools_used.append(tc.tool_name)
                seen.add(tc.tool_name)

        error_tools = list({tc.tool_name for tc in all_calls if tc.is_error})

        lines: list[str] = []
        lines.append(f"Ran {rounds} conversation round(s).")
        if tools_used:
            lines.append(f"Tools used: {', '.join(tools_used)}.")
        else:
            lines.append("No tools were called.")
        if error_tools:
            lines.append(f"Tools with errors: {', '.join(error_tools)}.")

        if token_exhausted:
            lines.append("Token budget nearly exhausted; treating as complete.")
        elif result.success:
            lines.append("Task completed successfully.")
        else:
            lines.append("Task failed this turn.")

        return " ".join(lines)


def _push_llm_event(
    session_id: str,
    round_label: str,
    system_prompt: str,
    messages: list,
    tools: list,
    task: "Task | None" = None,
    is_daemon: bool = False,
) -> None:
    """向前端推送 llm_prompt / daemon_prompt 调试事件。"""
    if not session_id:
        return
    try:
        from app.common.sse_bus import get_sse_bus
        get_sse_bus().push(session_id, {
            "type": "daemon_prompt" if is_daemon else "llm_prompt",
            "source": "observer",
            "round_label": round_label,
            "task_id": task.id if task else "",
            "agent_id": task.assigned_agent_id if task else "",
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
    max_tokens: int | None = None,
    is_daemon: bool = False,
) -> tuple[str, dict, list]:
    """流式调用 LLM，推送 observer_text_delta / observer_text_done（daemon 时推送 daemon_message）。"""
    full_text = ""
    reasoning_text = ""
    tool_call_acc: dict[int, dict] = {}
    image_acc: list = []
    finish_reason = None
    final_usage = None

    try:
        from app.common.sse_bus import get_sse_bus
        _sse = get_sse_bus() if session_id else None
    except Exception:
        _sse = None

    for chunk in client.stream_message(messages=messages, system_prompt=system_prompt, tools=tools, max_tokens=max_tokens):
        if chunk.text_delta:
            full_text += chunk.text_delta
            if _sse and not is_daemon:
                try:
                    _sse.push(session_id, {
                        "type": "observer_text_delta",
                        "delta": chunk.text_delta,
                        "round_label": round_label,
                    })
                except Exception:
                    pass
        if chunk.reasoning_delta:
            reasoning_text += chunk.reasoning_delta
            if _sse and not is_daemon:
                try:
                    _sse.push(session_id, {
                        "type": "observer_reasoning_delta",
                        "delta": chunk.reasoning_delta,
                        "round_label": round_label,
                    })
                except Exception:
                    pass
        if chunk.tool_call_delta:
            tool_call_acc[chunk.tool_call_delta["index"]] = chunk.tool_call_delta
        if chunk.image:
            image_acc.append(chunk.image)
        if chunk.is_done:
            finish_reason = chunk.finish_reason
            final_usage = chunk.usage
            if chunk.error:
                from app.common.errors import AppError
                raise AppError("LLM_API_ERROR", str(chunk.error))

    if _sse:
        try:
            if reasoning_text and not is_daemon:
                _sse.push(session_id, {
                    "type": "observer_reasoning_done",
                    "text": reasoning_text,
                    "round_label": round_label,
                })
            _sse.push(session_id, {
                "type": "daemon_message" if is_daemon else "observer_text_done",
                "text": full_text,
                "round_label": round_label,
                "context_tokens": final_usage.prompt_tokens if final_usage else None,
            })
        except Exception:
            pass

    logger.info(
        "Observer LLM stream finished: round_label=%s finish_reason=%s usage=%s text_len=%s reasoning_len=%s tool_calls=%s images=%s",
        round_label,
        finish_reason,
        final_usage,
        len(full_text),
        len(reasoning_text),
        len(tool_call_acc),
        len(image_acc),
    )

    return full_text, tool_call_acc, image_acc, final_usage


