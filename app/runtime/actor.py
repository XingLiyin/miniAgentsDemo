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
    from app.orchestrator.lifecycle_manager import LifecycleManager, SpawnPlanItem
    from app.runtime.tool_gateway import ToolGateway
    from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

_MAX_CONFIRM_RETRIES = 3  # 用户最多确认"未完成"后重试次数

_FALLBACK_SYSTEM_PROMPT = (
    "You are an execution agent. Your job is to complete the assigned task using the available tools.\n"
    "- Analyze the task description and use tools as needed to accomplish it.\n"
    "- You MUST call mark_task_complete exactly once when you are done: "
    "set success=True if the task succeeded, False if it cannot be completed. "
    "Never return a plain text response without first calling this tool.\n"
    "- Call request_human_input if you need information or a decision that only the user can provide.\n"
    "- Focus on the task objective; do not plan or decompose — just execute."
)


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
        system_prompt: str = "",
        lifecycle_manager: "LifecycleManager | None" = None,
    ) -> None:
        self._llm_client = llm_client
        self._tool_gateway = tool_gateway
        self._skill_registry = skill_registry
        self._task_svc = task_svc
        self._session_svc = session_svc
        self._system_prompt = system_prompt or _FALLBACK_SYSTEM_PROMPT
        self._lifecycle_manager = lifecycle_manager

    def act(self, task: Task, ctx: ReasoningContext, agent: Agent) -> ActorResult:
        """执行单个 atomic task，返回 ActorResult。

        外层循环：用户确认未完成时，将反馈追加到 messages 后重试，直到：
          - LLM 调用 mark_task_complete(success=True/False)
          - 用户确认已完成
          - 达到最大确认重试次数，标记失败
        """
        from app.tools.builtins import mark_task_complete, request_human_input, spawn_agents
        # 1. 载入 skill
        system_prompt = self._build_system_prompt(task, agent, ctx)
        # 2. 构建初始 messages
        messages = self._build_messages(task, ctx)
        # 3. 构建 available_tools：始终包含 mark_task_complete 和 request_human_input
        extra_tools = [mark_task_complete.to_llm_tool(), request_human_input.to_llm_tool()]
        # 仅当 agent 有 spawn 权限且 LM 已注入时，提供 spawn_agents 工具
        if agent.has_spawn_permission and self._lifecycle_manager is not None:
            extra_tools.append(spawn_agents.to_llm_tool())
        tools = list(ctx.relevant_tools) + extra_tools
        # 4. 激活 task
        self._task_svc.transition(task.id, "ACTIVE")

        tool_calls_made: list[ToolCallRecord] = []
        max_rounds = agent.loop_guard.actor_max_tool_rounds

        for _confirm_attempt in range(_MAX_CONFIRM_RETRIES + 1):
            last_text = ""
            last_response = None

            # 5. 内层 tool use 循环（每次确认重试都重新跑）
            for _round in range(max_rounds):
                raw_response = self._llm_client.send_message(
                    messages=messages,
                    system_prompt=system_prompt,
                    tools=tools,
                )
                response = self._llm_client.parse_response(raw_response)
                last_response = response

                if not response.tool_calls:
                    # LLM 返回纯文本，未调用 mark_task_complete → 需要用户确认
                    last_text = response.text or ""
                    break

                for tool_call in response.tool_calls:
                    if tool_call.name == "spawn_agents":
                        result = self._handle_spawn_agents(tool_call.input, task, agent)
                        tool_calls_made.append(
                            ToolCallRecord(
                                tool_name="spawn_agents",
                                arguments=tool_call.input,
                                result=result.content or "",
                                is_error=result.is_error,
                            )
                        )
                        messages = self._append_tool_turn(
                            messages, tool_call.name, tool_call.input, result
                        )

                    elif tool_call.name == "request_human_input":
                        # 内联阻塞：等用户回答后将答案作为工具结果注入，继续执行
                        answer = self._block_for_user_input(
                            task,
                            title="等待用户输入",
                            inputs={
                                "prompt": tool_call.input.get("prompt", ""),
                                "context": tool_call.input.get("context", ""),
                                "inline": True,
                            },
                        )
                        from app.tools.definition import ToolResult as TR
                        inline_result = TR(content=answer)
                        tool_calls_made.append(
                            ToolCallRecord(
                                tool_name=tool_call.name,
                                arguments=tool_call.input,
                                result=answer,
                                is_error=False,
                            )
                        )
                        messages = self._append_tool_turn(
                            messages, tool_call.name, tool_call.input, inline_result
                        )

                    elif tool_call.name == "mark_task_complete":
                        summary = tool_call.input.get("summary", "")
                        success = bool(tool_call.input.get("success", True))
                        if success:
                            return self._finish_task(task, summary, tool_calls_made)
                        else:
                            return self._fail_task(task, summary, tool_calls_made)

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

                        tool_calls_made.append(
                            ToolCallRecord(
                                tool_name=tool_call.name,
                                arguments=tool_call.input,
                                result=result.content or "",
                                is_error=result.is_error,
                            )
                        )
                        messages = self._append_tool_turn(
                            messages, tool_call.name, tool_call.input, result
                        )
            else:
                # 所有轮次耗尽，LLM 从未调用 mark_task_complete
                last_text = (last_response.text if last_response else "") or ""

            # 6. 阻塞等待用户确认（不退出 act 流程）
            confirmed, feedback = self._ask_user_for_task_confirmation(task, last_text, agent)

            if confirmed:
                return self._finish_task(task, last_text, tool_calls_made)

            if _confirm_attempt >= _MAX_CONFIRM_RETRIES:
                return self._fail_task(task, "用户多次确认任务未完成，已放弃", tool_calls_made)

            # 将用户反馈追加到 messages，下一轮重试
            retry_msg = "用户确认任务尚未完成，请重新执行。"
            if feedback:
                retry_msg += f"用户补充说明：{feedback}"
            messages.append(LLMMessage(role="user", content=retry_msg))

        # 理论上不会到达此处
        return self._fail_task(task, "超出最大确认重试次数", tool_calls_made)

    def _build_system_prompt(self, task: Task, agent: Agent, ctx: ReasoningContext) -> str:
        """Build system prompt: agent base + actor execution guidance + optional skill."""
        parts = [p for p in [agent.system_prompt, self._system_prompt] if p]

        skill_name = task.inputs.get("skill_name")
        if skill_name and self._skill_registry:
            skill_def = self._skill_registry.load_definition(skill_name)
            if skill_def is None:
                logger.warning("Actor: skill '%s' not found, proceeding without it", skill_name)
            else:
                parts.append(skill_def.instructions)

        if ctx.relevant_tools:
            lines = ["## Available Tools (for awareness only — use them via tool calls)"]
            for t in ctx.relevant_tools:
                lines.append(f"- {t.name}: {t.description or ''}")
            parts.append("\n".join(lines))

        parts.append(
            "## Required Tool Protocol\n"
            "- **mark_task_complete(summary, success)**: You MUST call this exactly once when you finish — whether the task succeeded or failed.\n"
            "  - `summary` (str): brief description of what was accomplished or why it failed.\n"
            "  - `success` (bool): `True` if the task is done, `False` if it cannot be completed.\n"
            "  - Never return a plain text response without calling this tool first.\n"
            "- **request_human_input(prompt, context='')**: Call this when you need information or a decision only the user can provide.\n"
            "  - `prompt` (str): the question or instruction to show the user.\n"
            "  - `context` (str, optional): background information to help the user answer.\n"
            "  - Execution pauses until the user replies; the answer arrives as a tool result — continue from there."
        )

        return "\n\n---\n\n".join(parts)

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

    def _block_for_user_input(
        self,
        task: Task,
        title: str,
        inputs: dict,
    ) -> str:
        """创建 user_input task，阻塞等待用户回答后恢复 session，返回用户输入内容。

        inputs 必须包含 "inline": True，使 answer_input 不重启 loop。
        """
        import time

        hitl_task = self._task_svc.create(
            session_id=task.session_id,
            agent_id=task.agent_id,
            task_type="user_input",
            title=title,
            description=inputs.get("prompt", ""),
            inputs=inputs,
        )
        self._task_svc.transition(hitl_task.id, "ACTIVE")
        self._session_svc.transition(task.session_id, "WAITING_INPUT")

        # 阻塞轮询，直到用户提交答案（最多 1 小时）
        answer = ""
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            refreshed = self._task_svc.get(hitl_task.id)
            if refreshed.status == "FINISHED":
                answer = refreshed.result or ""
                break
            time.sleep(1)

        # 恢复 session 为 RUNNING，act 继续执行
        self._session_svc.transition(task.session_id, "RUNNING")
        return answer

    def _ask_user_for_task_confirmation(
        self,
        task: Task,
        llm_output: str,
        agent: Agent,
    ) -> tuple[bool, str | None]:
        """触发任务完成确认 HITL，阻塞等待，返回 (confirmed, feedback)。"""
        prompt = (
            f"任务「{task.title}」已执行完毕，但 Agent 未明确标记完成状态。\n\n"
            f"执行结果：\n{llm_output or '（无输出）'}"
        )
        answer = self._block_for_user_input(
            task,
            title="请确认任务完成状态",
            inputs={
                "prompt": prompt,
                "type": "task_completion_confirm",
                "task_title": task.title,
                "task_output": llm_output,
                "original_task_id": task.id,
                "inline": True,
            },
        )

        confirmed = answer.startswith("用户已确认任务完成")
        feedback: str | None = None
        if not confirmed:
            prefix = "用户表示任务未完成，请重试。用户补充说明："
            if prefix in answer:
                feedback = answer[answer.index(prefix) + len(prefix):]
        return confirmed, feedback

    def _handle_spawn_agents(
        self,
        tool_input: dict,
        task: "Task",
        agent: Agent,
    ) -> "ToolResult":
        """处理 spawn_agents 工具调用：委托给 LifecycleManager，阻塞直到子任务完成。"""
        from app.tools.definition import ToolResult as TR

        if self._lifecycle_manager is None:
            return TR(
                content="spawn_agents not available: LifecycleManager not configured",
                is_error=True,
                error_code="SPAWN_NOT_AVAILABLE",
            )

        plan_raw = tool_input.get("plan", [])
        resume_hint = tool_input.get("resume_hint", "")

        from app.orchestrator.lifecycle_manager import SpawnPlanItem
        try:
            plan = [
                SpawnPlanItem(
                    title=str(p.get("title", "")),
                    description=str(p.get("description", "")),
                    deps=list(p.get("deps", [])),
                )
                for p in plan_raw
                if isinstance(p, dict)
            ]
        except Exception as e:
            return TR(content=f"Invalid spawn plan: {e}", is_error=True, error_code="INVALID_PLAN")

        spawn_result = self._lifecycle_manager.handle_spawn_requested(
            session_id=task.session_id,
            requesting_agent_id=agent.id,
            requesting_task_id=task.id,
            plan=plan,
            resume_hint=resume_hint,
        )
        return TR(
            content=spawn_result.to_content(),
            is_error=not spawn_result.approved,
            error_code="SPAWN_REJECTED" if not spawn_result.approved else None,
        )

    def _fail_task(
        self,
        task: Task,
        error: str,
        tool_calls_made: list[ToolCallRecord],
    ) -> ActorResult:
        """LLM 调用 mark_task_complete(success=False) 时，标记任务失败并返回。"""
        self._task_svc.fail(task.id, error=error)
        return ActorResult(
            task_id=task.id,
            success=False,
            output="",
            tool_calls_made=tool_calls_made,
            error=error,
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
