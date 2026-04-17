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
from app.runtime.types import ActorResult, ContextResource, ConversationTurn, ReasoningContext, ToolCallRecord
from app.tools.definition import CallContext

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
    ) -> None:
        self._llm_client = llm_client
        self._tool_gateway = tool_gateway
        self._task_svc = task_svc

    def act(self, task: Task, ctx: ReasoningContext, agent: Agent) -> ActorResult:
        """执行单个 task，plan 和 act 共用同一循环。"""
        if task.status == "PENDING":
            self._task_svc.transition(task.id, "ACTIVE")

        system_prompt = self._build_system_prompt(ctx)
        messages      = self._build_messages(task, ctx)
        tools         = [r.llm_tool for r in ctx.actor_resources if r.kind == "tool" and r.llm_tool is not None]

        tool_calls_made: list[ToolCallRecord] = []
        conversation_turns: list[ConversationTurn] = []
        last_text = ""
        session_id = getattr(task, "session_id", "")
        toolcall_ctx = CallContext(session_id=session_id, agent_id=agent.id, agent=agent, task=task)

        # 按 agent.llm_name 动态解析 LLM 客户端，缺省用注入的默认客户端
        llm_client = self._llm_client
        if agent.llm_name:
            try:
                from app.llm.registry import get_llm_registry
                llm_client = get_llm_registry().get_client(agent.llm_name)
            except Exception:
                pass

        try:
            from app.runtime.sse_bus import get_sse_bus
            from app.common.utils import now_iso
            _sse = get_sse_bus() if session_id else None
        except Exception:
            _sse = None

        for _round in range(agent.loop_guard.actor_max_tool_rounds):
            messages_sent = list(messages)

            # ── 推送 llm_prompt 调试事件 ──────────────────────────────────────
            if _sse and session_id:
                try:
                    _sse.push(session_id, {
                        "type": "llm_prompt",
                        "source": "actor",
                        "round_label": f"actor_round_{_round}",
                        "system_prompt": system_prompt,
                        "messages": [{"role": m.role, "content": m.content} for m in messages],
                        "tool_names": [r.name for r in ctx.actor_resources if r.kind == "tool" and r.llm_tool is not None],
                    })
                except Exception:
                    pass

            # ── 流式 LLM 调用 ────────────────────────────────────────────────
            full_text = ""
            tool_call_acc: dict[int, dict] = {}   # index → cumulative {id, name, arguments}

            for chunk in llm_client.stream_message(
                messages=messages,
                system_prompt=system_prompt,
                tools=tools,
            ):
                if chunk.text_delta:
                    full_text += chunk.text_delta
                    if _sse:
                        try:
                            _sse.push(session_id, {
                                "type": "text_delta",
                                "delta": chunk.text_delta,
                                "round": _round,
                            })
                        except Exception:
                            pass
                if chunk.tool_call_delta:
                    # adapter 已在内部累积，每次 yield 的是最新全量状态，直接覆写
                    tool_call_acc[chunk.tool_call_delta["index"]] = chunk.tool_call_delta

            # text_done：通知前端本 round 流式结束，可落地气泡
            if _sse:
                try:
                    _sse.push(session_id, {
                        "type": "text_done",
                        "text": full_text,
                        "round": _round,
                    })
                except Exception:
                    pass

            # 将累积缓冲区还原为 ToolCallBlock 列表
            tool_calls_from_stream = _build_tool_calls_from_stream(tool_call_acc)

            if not tool_calls_from_stream:
                last_text = full_text
                conversation_turns.append(ConversationTurn(
                    round=_round,
                    messages_sent=messages_sent,
                    llm_text=last_text,
                    tool_calls=[],
                ))
                break

            round_tool_calls: list[ToolCallRecord] = []
            done = False

            for tool_call in tool_calls_from_stream:
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
                    from app.tools.definition import ToolResult as TR
                    result = TR(content=str(e), is_error=True)
                if task.actor_done:
                    done = True

                record = ToolCallRecord(
                    tool_name=tool_call.name,
                    arguments=tool_call.input,
                    result=result.content or "",
                    is_error=result.is_error,
                )
                round_tool_calls.append(record)
                tool_calls_made.append(record)
                messages = self._append_tool_turn(messages, tool_call.name, tool_call.input, result)

                if _sse:
                    try:
                        _sse.push(session_id, {
                            "type": "tool_call",
                            "tool_name": record.tool_name,
                            "arguments": record.arguments,
                            "result": record.result,
                            "is_error": record.is_error,
                            "created_at": now_iso(),
                        })
                    except Exception:
                        pass

            last_text = full_text
            conversation_turns.append(ConversationTurn(
                round=_round,
                messages_sent=messages_sent,
                llm_text=last_text,
                tool_calls=round_tool_calls,
            ))

            if done:
                break

        return self._build_result(task, tool_calls_made, conversation_turns, last_text)

    # ── System prompt 构建 ─────────────────────────────────────────────────

    def _build_system_prompt(self, ctx: ReasoningContext) -> str:
        """组装 system prompt：soul → 资源列表 → skill instructions。
        无模式分支——身份与协议由 soul 模板承载，Actor 只渲染动态资源部分。
        """
        parts = [p for p in [ctx.soul, self._build_resources_section(ctx), ctx.skill_instructions] if p]
        return "\n\n---\n\n".join(parts)

    def _build_resources_section(self, ctx: ReasoningContext) -> str:
        """将 ctx.resources 按 kind 分组渲染，skill 和 tool 各自独立成段。"""
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

    # ── 消息构建 ───────────────────────────────────────────────────────────

    def _build_messages(self, task: Task, ctx: ReasoningContext) -> list[LLMMessage]:
        """
        plan 模式：goal + history（task 本身就是 session goal 的分解请求，不追加描述）
        act  模式：goal + history + task 描述（作为本轮具体指令）
        """
        messages: list[LLMMessage] = []

        # recent_messages：当前agent记忆，按时间顺序直接加入
        # 去掉最后一条刚刚输入的user消息（如果有），因为它通常是对当前 task 的补充说明，放在最后更合适
        recent_messages = ctx.recent_messages[:-1] if ctx.recent_messages and ctx.recent_messages[-1].get("role") == "user" else ctx.recent_messages
        for m in recent_messages:
            messages.append(LLMMessage(role=m.get("role", "user"), content=m.get("content", "")))

        # blackboard_snippets：当前 session 相关的零散信息碎片
        bb_content = "\n".join(f"- {s}" for s in ctx.blackboard_snippets)
        if ctx.blackboard_snippets:
            bb = "\n".join(f"- {s}" for s in ctx.blackboard_snippets)
            bb_content = f"Task Background:\n{bb}"

        # task 描述
        goal_content = ""
        if task.title and task.description:
            goal_content = f"Current goal: {task.title}\nDescription: {task.description}"
        previous_progress = ""
        if ctx.summary_text:
            previous_progress = f"Previous progress:\n{ctx.summary_text}"

        # user 消息最后追加，确保对当前 task 的补充说明在最显著的位置
        messages.append(LLMMessage(role="user", content=f"{bb_content}\n{goal_content}\n{previous_progress}\nCurrent message: {ctx.current_task.user_prompt}"))

        return messages
        

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


# ── 模块级工具 ─────────────────────────────────────────────────────────────────

def _build_tool_calls_from_stream(acc: dict[int, dict]) -> list:
    """将流式 tool_call 累积缓冲区（index → 最终全量状态）转换为 ToolCallBlock 列表。"""
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
