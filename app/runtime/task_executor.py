"""Task 执行器：按 Task.type 分发执行（Phase 1）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.common.errors import AppError
from app.domain.models.agent import Agent
from app.domain.models.task import Task
from app.llm.llm_base import LLMClient, LLMMessage, LLMRequest
from app.orchestrator.task_manager import TaskManager
from app.runtime.tool_gateway import ToolGateway, ToolResult
from app.runtime.skill_router import SkillRouter

logger = logging.getLogger(__name__)


@dataclass
class TaskResult:
    """任务执行结果。"""
    task_id: str
    success: bool
    result: str | None = None
    outputs: dict[str, Any] | None = None
    error: str | None = None


class TaskExecutor:
    """按 task.type 分发执行：reasoning | tool-call | skill。"""

    def __init__(
        self,
        task_manager: TaskManager,
        tool_gateway: ToolGateway,
        llm_client: LLMClient,
        skill_router: SkillRouter | None = None,
    ) -> None:
        self._task_mgr = task_manager
        self._tool_gateway = tool_gateway
        self._llm_client = llm_client
        self._skill_router = skill_router

    def execute(self, task: Task, agent: Agent) -> TaskResult:
        """执行 Task，返回 TaskResult。成功/失败均写入存储。"""
        self._task_mgr.activate(task.id)

        try:
            match task.type:
                case "reasoning":
                    result = self._run_reasoning(task, agent)
                case "tool-call":
                    result = self._run_tool_call(task, agent)
                case "skill":
                    result = self._run_skill(task, agent)
                case _:
                    raise AppError("UNSUPPORTED_TASK_TYPE", f"Unknown task type: {task.type}")

            self._task_mgr.complete(task.id, result=result.result, outputs=result.outputs)
            return result

        except AppError as e:
            logger.warning("Task %s failed: %s %s", task.id, e.code, e.message)
            self._task_mgr.fail_task(task.id, error=f"{e.code}: {e.message}")
            return TaskResult(task_id=task.id, success=False, error=f"{e.code}: {e.message}")

        except Exception as e:
            logger.exception("Task %s unexpected error", task.id)
            self._task_mgr.fail_task(task.id, error=str(e))
            return TaskResult(task_id=task.id, success=False, error=str(e))

    def _run_reasoning(self, task: Task, agent: Agent) -> TaskResult:
        """构建 LLMRequest → LLMClient.send_message() → 写 task.result。"""
        messages: list[LLMMessage] = []

        # 从 task.inputs 拿上下文消息（由 AgentLoop 组装）
        context_messages = task.inputs.get("messages", [])
        for m in context_messages:
            messages.append(LLMMessage(role=m["role"], content=m["content"]))

        if not messages:
            messages.append(LLMMessage(role="user", content=task.description or task.title))

        response = self._llm_client.send_message(
            messages=messages,
            system_prompt=task.inputs.get("system_prompt", agent.system_prompt),
        )

        return TaskResult(
            task_id=task.id,
            success=True,
            result=response.text,
            outputs={"text": response.text, "usage": response.usage.__dict__ if response.usage else {}},
        )

    def _run_skill(self, task: Task, agent: Agent) -> TaskResult:
        """执行 skill task：以 skill instructions 作为 system prompt 调用 LLM。"""
        skill_instructions = task.inputs.get("skill_instructions", "")
        system_prompt = skill_instructions or agent.system_prompt

        messages: list[LLMMessage] = []
        for m in task.inputs.get("messages", []):
            messages.append(LLMMessage(role=m["role"], content=m["content"]))
        if not messages:
            messages.append(LLMMessage(role="user", content=task.description or task.title))

        response = self._llm_client.send_message(
            messages=messages,
            system_prompt=system_prompt,
        )
        return TaskResult(
            task_id=task.id,
            success=True,
            result=response.text,
            outputs={"text": response.text, "usage": response.usage.__dict__ if response.usage else {}},
        )

    def _run_tool_call(self, task: Task, agent: Agent) -> TaskResult:
        """从 task.inputs 取 tool_name + arguments → ToolGateway.call()。"""
        tool_name = task.inputs.get("tool_name")
        arguments = task.inputs.get("arguments", {})

        if not tool_name:
            raise AppError("INVALID_ARGUMENT", "tool-call task missing 'tool_name' in inputs")

        tool_result: ToolResult = self._tool_gateway.call(
            tool_name=tool_name,
            arguments=arguments,
            agent=agent,
            task_id=task.id,
        )

        if tool_result.is_error:
            raise AppError(
                tool_result.error_code or "TOOL_ERROR",
                tool_result.content or "Tool execution failed",
            )

        return TaskResult(
            task_id=task.id,
            success=True,
            result=tool_result.content,
            outputs={"content": tool_result.content, **tool_result.metadata},
        )
