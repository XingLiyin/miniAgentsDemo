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

from app.domain.models.agent import Agent
from app.domain.models.task import Task
from app.domain.services.task_service import TaskService
from app.llm.base import BaseChatClient, LLMMessage
from app.runtime.agent_controller import AgentController, ControlSignal
from app.runtime.types import ActorResult, ContextResource, ConversationTurn, ReasoningContext, ToolCallRecord

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
        agent_controller: AgentController,
    ) -> None:
        self._llm_client = llm_client
        self._tool_gateway = tool_gateway
        self._task_svc = task_svc
        self._agent_controller = agent_controller

    def act(self, task: Task, ctx: ReasoningContext, agent: Agent) -> ActorResult:
        """执行单个 task，plan 和 act 共用同一循环。"""
        if task.status == "PENDING":
            self._task_svc.transition(task.id, "ACTIVE")

        system_prompt = self._build_system_prompt(ctx)
        messages      = self._build_messages(task, ctx)
        tools         = [r.llm_tool for r in ctx.resources if r.kind == "tool" and r.llm_tool is not None]

        tool_calls_made: list[ToolCallRecord] = []
        conversation_turns: list[ConversationTurn] = []
        terminal_signal_data: dict = {}
        last_text = ""

        for _round in range(agent.loop_guard.actor_max_tool_rounds):
            messages_sent = list(messages)
            raw_response = self._llm_client.send_message(
                messages=messages,
                system_prompt=system_prompt,
                tools=tools,
            )
            response = self._llm_client.parse_response(raw_response)

            if not response.tool_calls:
                last_text = response.text or ""
                conversation_turns.append(ConversationTurn(
                    round=_round,
                    messages_sent=messages_sent,
                    llm_text=last_text,
                    tool_calls=[],
                ))
                break

            round_tool_calls: list[ToolCallRecord] = []
            done = False

            for tool_call in response.tool_calls:
                if self._agent_controller.can_handle(tool_call.name):
                    ctrl = self._agent_controller.dispatch(
                        tool_call.name, tool_call.input, agent, task
                    )
                    result = ctrl.tool_result
                    if ctrl.signal == ControlSignal.TASK_COMPLETE:
                        terminal_signal_data = ctrl.signal_data
                        done = True
                else:
                    try:
                        result = self._tool_gateway.call(
                            tool_name=tool_call.name,
                            arguments=tool_call.input,
                            agent=agent,
                            task_id=task.id,
                        )
                    except Exception as e:
                        logger.warning("Actor: tool '%s' raised %s", tool_call.name, e)
                        from app.tools.definition import ToolResult as TR
                        result = TR(content=str(e), is_error=True)

                record = ToolCallRecord(
                    tool_name=tool_call.name,
                    arguments=tool_call.input,
                    result=result.content or "",
                    is_error=result.is_error,
                )
                round_tool_calls.append(record)
                tool_calls_made.append(record)
                messages = self._append_tool_turn(messages, tool_call.name, tool_call.input, result)

            last_text = response.text or ""
            conversation_turns.append(ConversationTurn(
                round=_round,
                messages_sent=messages_sent,
                llm_text=last_text,
                tool_calls=round_tool_calls,
            ))

            if done:
                break

        return self._build_result(task, tool_calls_made, conversation_turns,
                                  last_text, terminal_signal_data)

    # ── System prompt 构建 ─────────────────────────────────────────────────

    def _build_system_prompt(self, ctx: ReasoningContext) -> str:
        """组装 system prompt：soul → 资源列表 → skill instructions。
        无模式分支——身份与协议由 soul 模板承载，Actor 只渲染动态资源部分。
        """
        parts = [p for p in [ctx.soul, self._build_resources_section(ctx), ctx.skill_instructions] if p]
        return "\n\n---\n\n".join(parts)

    def _build_resources_section(self, ctx: ReasoningContext) -> str:
        """将 ctx.resources 按 kind 分组渲染，skill 和 tool 各自独立成段。"""
        skills = [r for r in ctx.resources if r.kind == "skill"]
        tools  = [r for r in ctx.resources if r.kind == "tool" and r.llm_tool is not None]
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

    # ── 消息构建 ───────────────────────────────────────────────────────────

    def _build_messages(self, task: Task, ctx: ReasoningContext) -> list[LLMMessage]:
        """
        plan 模式：goal + history（task 本身就是 session goal 的分解请求，不追加描述）
        act  模式：goal + history + task 描述（作为本轮具体指令）
        """
        messages: list[LLMMessage] = []

        goal_content = ctx.goal
        if ctx.summary_text:
            goal_content = f"Previous progress:\n{ctx.summary_text}\n\nCurrent goal: {ctx.goal}"
        messages.append(LLMMessage(role="user", content=goal_content))

        if ctx.blackboard_snippets:
            bb = "\n".join(f"- {s}" for s in ctx.blackboard_snippets)
            messages.append(LLMMessage(role="user", content=f"Context:\n{bb}"))

        for m in ctx.recent_messages:
            messages.append(LLMMessage(role=m.get("role", "user"), content=m.get("content", "")))

        return messages

    # ── ActorResult 构建 ───────────────────────────────────────────────────

    def _build_result(
        self,
        task: Task,
        tool_calls_made: list[ToolCallRecord],
        conversation_turns: list[ConversationTurn],
        last_text: str,
        terminal_signal_data: dict,
    ) -> ActorResult:
        """plan 和 act 统一走文本路径；task 创建由 Observer 阶段负责。"""
        skill_used = task.inputs.get("skill_name")
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

    # ── 工具结果追加 ───────────────────────────────────────────────────────

    def _append_tool_turn(
        self,
        messages: list[LLMMessage],
        tool_name: str,
        tool_input: dict,
        tool_result: object,
    ) -> list[LLMMessage]:
        content  = getattr(tool_result, "content", "") or ""
        is_error = getattr(tool_result, "is_error", False)
        prefix   = f"Tool '{tool_name}' error" if is_error else f"Tool '{tool_name}' result"
        messages.append(LLMMessage(role="user", content=f"{prefix}:\n{content}"))
        return messages
