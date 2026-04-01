"""Actor executes one atomic task and can iterate through tool use."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.domain.models.agent import Agent
from app.domain.models.task import Task
from app.domain.services.session_service import SessionService
from app.domain.services.task_service import TaskService
from app.llm.base import BaseChatClient
from app.llm.base import LLMMessage
from app.runtime.types import ActorResult, ReasoningContext, ToolCallRecord

if TYPE_CHECKING:
    from app.runtime.tool_gateway import ToolGateway
    from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class Actor:
    """Phase 3：执行单个 atomic task，支持多轮 tool use 循环。

    执行流程（per task）：
      1. 载入 Skill（如 task.inputs["skill_name"] 有值）
      2. 构建初始 messages（目标 + 历史 + 当前任务描述）
      3. 构建 available_tools：ctx.relevant_tools + [request_human_input]
      4. 激活 task
      5. 多轮 tool use 循环（最多 actor_max_tool_rounds 次）
         - 无 tool calls → 取文本输出，结束
         - request_human_input → HITL 暂停
         - 其他 → ToolGateway.call() → 追加结果 → 继续循环
      6. 超出最大轮次，取最后响应文本
    """
    def __init__(
        self,
        llm_client: BaseChatClient,
        tool_gateway: "ToolGateway",
        skill_registry: "SkillRegistry | None",
        task_svc: TaskService,
        session_svc: SessionService,
    ) -> None:
        self._llm_client = llm_client
        self._tool_gateway = tool_gateway
        self._skill_registry = skill_registry
        self._task_svc = task_svc
        self._session_svc = session_svc

    def act(self, task: Task, ctx: ReasoningContext, agent: Agent) -> ActorResult:
        """执行单个 atomic task，返回 ActorResult。"""
        from app.tools.builtins import request_human_input
        # 1. 载入 skill
        system_prompt = self._build_system_prompt(task, agent)
        # 2. 构建初始 messages
        messages = self._build_messages(task, ctx)
        # 3. 构建 available_tools
        tools = list(ctx.relevant_tools) + [request_human_input.to_llm_tool()]
        # 4. 激活 task
        self._task_svc.transition(task.id, "ACTIVE")
        # 5. 多轮 tool use 循环
        tool_calls_made: list[ToolCallRecord] = []
        max_rounds = agent.loop_guard.actor_max_tool_rounds
        last_response = None

        for _round in range(max_rounds):
            raw_response = self._llm_client.send_message(
                messages=messages,
                system_prompt=system_prompt,
                tools=tools if ctx.relevant_tools else None,
            )
            response = self._llm_client.parse_response(raw_response)
            last_response = response
            # 5.1. 无 tool calls → 取文本输出，结束
            if not response.tool_calls:
                return self._finish_task(task, response.text, tool_calls_made)
            # 5.2. 有 tool calls → 逐条处理
            for tool_call in response.tool_calls:
                if tool_call.name == "request_human_input":
                    return self._handle_hitl(task, tool_call.input, agent)

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

                tool_calls_made.append(
                    ToolCallRecord(
                        tool_name=tool_call.name,
                        arguments=tool_call.input,
                        result=result.content or "",
                        is_error=result.is_error,
                    )
                )
                # 5.2.1. 追加 tool 调用结果到 messages，继续下一轮
                messages = self._append_tool_turn(
                    messages, tool_call.name, tool_call.input, result
                )

        # 超出最大轮次，取最后响应文本
        last_text = (last_response.text if last_response else "") or ""
        return self._finish_task(task, last_text, tool_calls_made)

    def _build_system_prompt(self, task: Task, agent: Agent) -> str:
        """Load skill instructions if present and append them to the agent prompt."""
        skill_name = task.inputs.get("skill_name")
        if not skill_name or not self._skill_registry:
            return agent.system_prompt

        skill_def = self._skill_registry.load_definition(skill_name)
        if skill_def is None:
            logger.warning("Actor: skill '%s' not found, proceeding without it", skill_name)
            return agent.system_prompt

        return agent.system_prompt + "\n\n---\n\n" + skill_def.instructions

    def _build_messages(self, task: Task, ctx: ReasoningContext) -> list[LLMMessage]:
        """Build the initial conversation for the current task."""
        messages: list[LLMMessage] = []

        goal_content = ctx.goal
        if ctx.summary_text:
            goal_content = (
                f"Previous progress:\n{ctx.summary_text}\n\nCurrent goal: {ctx.goal}"
            )
        messages.append(LLMMessage(role="user", content=goal_content))

        if ctx.blackboard_snippets:
            bb = "\n".join(f"- {s}" for s in ctx.blackboard_snippets)
            messages.append(LLMMessage(role="user", content=f"Context:\n{bb}"))

        for m in ctx.recent_messages:
            messages.append(
                LLMMessage(role=m.get("role", "user"), content=m.get("content", ""))
            )

        task_content = task.description or task.title
        messages.append(LLMMessage(role="user", content=task_content))

        return messages

    def _append_tool_turn(
        self,
        messages: list[LLMMessage],
        tool_name: str,
        tool_input: dict,
        tool_result: object,
    ) -> list[LLMMessage]:
        """Append a tool result back into the conversation as text."""
        content = getattr(tool_result, "content", "") or ""
        is_error = getattr(tool_result, "is_error", False)
        prefix = f"Tool '{tool_name}' error" if is_error else f"Tool '{tool_name}' result"
        messages.append(LLMMessage(role="user", content=f"{prefix}:\n{content}"))
        return messages

    def _handle_hitl(self, task: Task, inputs: dict, agent: Agent) -> ActorResult:
        """Create a user_input task and pause the session."""
        prompt = inputs.get("prompt", "")
        hitl_task = self._task_svc.create(
            session_id=task.session_id,
            agent_id=task.agent_id,
            task_type="user_input",
            title="等待用户输入",
            description=prompt,
            inputs={"prompt": prompt},
        )
        self._task_svc.transition(hitl_task.id, "ACTIVE")
        self._session_svc.transition(task.session_id, "WAITING_INPUT")
        self._task_svc.finish(task.id, result="Waiting for user input")

        return ActorResult(
            task_id=task.id,
            success=True,
            output="",
            hitl_task_id=hitl_task.id,
        )

    def _finish_task(
        self,
        task: Task,
        output: str,
        tool_calls_made: list[ToolCallRecord],
    ) -> ActorResult:
        """Persist the task result and return a normalized actor result."""
        skill_used = task.inputs.get("skill_name")
        if skill_used:
            actor_mode = "skill"
        elif tool_calls_made:
            actor_mode = "tool_use"
        else:
            actor_mode = "text"

        outputs = {
            "actor_mode": actor_mode,
            "skill_used": skill_used,
            "tool_calls_made": [tc.tool_name for tc in tool_calls_made],
            "text": output,
        }
        self._task_svc.finish(task.id, result=output, outputs=outputs)

        return ActorResult(
            task_id=task.id,
            success=True,
            output=output,
            tool_calls_made=tool_calls_made,
            actor_mode=actor_mode,
            skill_used=skill_used,
        )
