"""Actor：统一单循环执行 plan 和 atomic 两种 task。

工具集来自 ctx.resources（kind="tool"），由 Reasoner 按 task.type 差异化加载：
  - plan task：submit_plan
  - atomic task：外部工具 + request_human_input

循环由 ControlSignal.TASK_COMPLETE（plan 路径）和"无工具调用"（act 路径）统一终止。
身份与协议文本由 soul 模板承载，Actor 不感知 plan/act 模式。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.config.settings import get_settings
from app.domain.models.agent import Agent
from app.domain.models.task import Task
from app.domain.services.task_service import TaskService
from app.llm.base import BaseChatClient, LLMMessage
from app.runtime.prompt_builder import ActorPromptBuilder, PromptBuilderFactory
from app.runtime.types import ActorResult, ContextResource, ConversationTurn, ReasoningContext, ToolCallRecord
from app.tools.definition import CallContext, ToolResult

if TYPE_CHECKING:
    from app.runtime.tool_gateway import ToolGateway

logger = logging.getLogger(__name__)



class Actor:
    """统一 LLM 执行入口：plan 和 act 共用同一个 tool-use 循环。"""

    def __init__(
        self,
        llm_client: BaseChatClient,
        tool_gateway: "ToolGateway",
        task_svc: TaskService,
        prompt_builder: ActorPromptBuilder | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._tool_gateway = tool_gateway
        self._task_svc = task_svc
        self._prompt_builder = prompt_builder or PromptBuilderFactory.for_actor()

    def act(self, task: Task, ctx: ReasoningContext, agent: Agent) -> ActorResult:
        """执行单个 task，plan 和 act 共用同一循环。"""
        if task.status == "PENDING":
            self._task_svc.transition(task.id, "ACTIVE")

        system_prompt = self._prompt_builder.build_system_prompt(ctx)
        messages = self._prompt_builder.build_messages(task, ctx)
        tools = [r.llm_tool for r in ctx.actor_resources if r.kind == "tool" and r.llm_tool is not None]

        session_id = getattr(task, "session_id", "")
        _wd = (
            (task.settings.get("working_dir") if task.settings else None)
            or getattr(agent, "settings", {}).get("working_dir")
            or get_settings().bash_exec_cwd
        )
        toolcall_ctx = CallContext(session_id=session_id, agent_id=agent.id, agent=agent, task=task, working_dir=_wd or "")
        llm_client = self._resolve_llm_client(agent)
        _sse = self._get_sse(session_id)

        tool_calls_made: list[ToolCallRecord] = []
        conversation_turns: list[ConversationTurn] = []
        last_text = ""

        for _round in range(agent.loop_guard.actor_max_tool_rounds):
            messages = self._prompt_builder.sanitize_messages(messages)
            messages_sent = list(messages)
            self._push_prompt_event(_sse, session_id, _round, system_prompt, messages, ctx)

            full_text, tool_call_acc, image_acc = self._stream_llm(
                llm_client, messages, system_prompt, tools, _round, _sse, session_id
            )
            last_text = full_text
            parsed = llm_client.parse_stream_acc(full_text, tool_call_acc, images=image_acc)
            tool_calls_from_stream = parsed.tool_calls

            round_tool_calls: list[ToolCallRecord] = []
            done = False

            if tool_calls_from_stream:
                messages = self._prompt_builder.append_assistant_tool_calls(
                    messages, full_text, tool_calls_from_stream
                )
                round_tool_calls, messages, done = self._execute_tools(
                    tool_calls_from_stream, agent, task, toolcall_ctx, messages, _sse, session_id
                )
                tool_calls_made.extend(round_tool_calls)

            conversation_turns.append(ConversationTurn(
                round=_round,
                messages_sent=messages_sent,
                llm_text=last_text,
                tool_calls=round_tool_calls,
                images=parsed.images,
            ))

            if not tool_calls_from_stream or done:
                break

        return self._build_result(task, tool_calls_made, conversation_turns, last_text)

    # ── 私有辅助方法 ──────────────────────────────────────────────────────────

    def _resolve_llm_client(self, agent: Agent) -> BaseChatClient:
        if agent.llm_provider:
            try:
                from app.llm.registry import get_llm_registry
                return get_llm_registry().get_client(agent.llm_provider, agent.llm_model or None)
            except Exception:
                pass
        return self._llm_client

    def _get_sse(self, session_id: str):
        if not session_id:
            return None
        try:
            from app.common.sse_bus import get_sse_bus
            return get_sse_bus()
        except Exception:
            return None

    def _sse_push(self, _sse, session_id: str, event: dict) -> None:
        if _sse:
            try:
                _sse.push(session_id, event)
            except Exception:
                pass

    def _push_prompt_event(self, _sse, session_id: str, _round: int, system_prompt: str, messages, ctx: ReasoningContext) -> None:
        self._sse_push(_sse, session_id, {
            "type": "llm_prompt",
            "source": "actor",
            "round_label": f"actor_round_{_round}",
            "system_prompt": system_prompt,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "tool_names": [r.name for r in ctx.actor_resources if r.kind == "tool" and r.llm_tool is not None],
        })

    def _stream_llm(self, llm_client, messages, system_prompt, tools, _round: int, _sse, session_id: str):
        full_text = ""
        reasoning_text = ""
        tool_call_acc: dict[int, dict] = {}
        image_acc: list = []
        finish_reason = None
        final_usage = None

        for chunk in llm_client.stream_message(messages=messages, system_prompt=system_prompt, tools=tools):
            if chunk.text_delta:
                full_text += chunk.text_delta
                self._sse_push(_sse, session_id, {"type": "text_delta", "delta": chunk.text_delta, "round": _round})
            if chunk.reasoning_delta:
                reasoning_text += chunk.reasoning_delta
                self._sse_push(_sse, session_id, {
                    "type": "reasoning_delta",
                    "delta": chunk.reasoning_delta,
                    "round": _round,
                })
            if chunk.tool_call_delta:
                tool_call_acc[chunk.tool_call_delta["index"]] = chunk.tool_call_delta
            if chunk.image:
                image_acc.append(chunk.image)
                self._sse_push(_sse, session_id, {
                    "type": "image",
                    "media_type": chunk.image.media_type,
                    "source_type": chunk.image.source_type,
                    "data": chunk.image.data,
                    "round": _round,
                })
            if chunk.is_done:
                finish_reason = chunk.finish_reason
                final_usage = chunk.usage

        if reasoning_text:
            self._sse_push(_sse, session_id, {
                "type": "reasoning_done",
                "text": reasoning_text,
                "round": _round,
            })
        self._sse_push(_sse, session_id, {"type": "text_done", "text": full_text, "round": _round})
        logger.info(
            "Actor LLM stream finished: round=%s finish_reason=%s usage=%s text_len=%s reasoning_len=%s tool_calls=%s images=%s",
            _round,
            finish_reason,
            final_usage,
            len(full_text),
            len(reasoning_text),
            len(tool_call_acc),
            len(image_acc),
        )
        return full_text, tool_call_acc, image_acc

    def _execute_tools(self, tool_calls, agent: Agent, task: Task, toolcall_ctx: CallContext, messages, _sse, session_id: str):
        from app.common.utils import now_iso

        round_tool_calls: list[ToolCallRecord] = []
        done = False

        for tool_call in tool_calls:
            try:
                result = self._tool_gateway.call(
                    tool_name=tool_call.name,
                    arguments=tool_call.input,
                    agent=agent,
                    task_id=task.id,
                    ctx=toolcall_ctx,
                )
            except Exception as e:
                logger.warning("Actor: tool '%s' raised %s", tool_call.name, e)
                result = ToolResult(content=str(e), is_error=True)

            if task.actor_done:
                done = True

            from app.llm.types import content_to_text
            result_text = content_to_text(result.content) if isinstance(result.content, list) else (result.content or "")
            record = ToolCallRecord(
                tool_name=tool_call.name,
                arguments=tool_call.input,
                result=result_text,
                is_error=result.is_error,
                tool_call_id=tool_call.id,
            )
            round_tool_calls.append(record)
            messages = self._prompt_builder.append_tool_result(
                messages, tool_call.name, result, tool_call_id=tool_call.id
            )
            self._sse_push(_sse, session_id, {
                "type": "tool_call",
                "tool_name": record.tool_name,
                "arguments": record.arguments,
                "result": record.result,
                "is_error": record.is_error,
                "created_at": now_iso(),
            })

        return round_tool_calls, messages, done

    # ── ActorResult 构建 ───────────────────────────────────────────────────

    def _build_result(
        self,
        task: Task,
        tool_calls_made: list[ToolCallRecord],
        conversation_turns: list[ConversationTurn],
        last_text: str,
    ) -> ActorResult:
        """plan 和 act 统一走文本路径；task 创建由 Observer 阶段负责。"""
        skill_used = task.settings.get("skill_name")
        actor_mode = "skill" if skill_used else ("tool_use" if tool_calls_made else "text")
        return ActorResult(
            task_id=task.id,
            success=True,
            output=last_text,
            tool_calls_made=tool_calls_made,
            conversation_turns=conversation_turns,
            actor_mode=actor_mode,
            skill_used=skill_used,
        )
