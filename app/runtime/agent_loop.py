"""Agent Loop 主逻辑（Phase 1）。

六阶段：Observe → Plan → CreateTask → Execute → UpdateMemory → [循环/结束]
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.common.errors import AppError
from app.domain.models.agent import Agent
from app.domain.models.session import Session
from app.domain.models.task import Task
from app.domain.services.blackboard_service import BlackboardService
from app.domain.services.memory_service import MemoryService, PromptContext
from app.domain.services.session_service import SessionService
from app.domain.services.task_service import TaskService
from app.llm.llm_base import LLMClient, LLMMessage
from app.runtime.task_executor import TaskExecutor
from app.skills.registry import SkillRegistry
from app.tools.registry import ToolRegistry
from app.runtime.skill_router import SkillRouter
from app.storage.file.agent_store import AgentStore

logger = logging.getLogger(__name__)

# 系统 Prompt：指导 LLM 输出结构化 Plan（JSON）
_PLAN_SYSTEM_PROMPT_BASE = """You are an AI agent. Based on the context provided, output a JSON plan.

The plan must be a JSON object with this structure:
{
  "done": false,
  "summary": "brief summary of what was accomplished",
  "tasks": [
    {
      "type": "reasoning",
      "title": "short task title",
      "description": "detailed description",
      "inputs": {}
    }
  ]
}

If the goal is achieved, set "done": true and "tasks": [].
Only output valid JSON, no extra text.
"""

_PLAN_SYSTEM_PROMPT_TOOLS_SUFFIX = """
You also have access to tools. To call a tool, use a task with type "tool-call":
{
  "type": "tool-call",
  "title": "short title",
  "description": "why you are calling this tool",
  "inputs": {
    "tool_name": "<tool name>",
    "arguments": { ... }
  }
}

Available tools are listed in the tools field of this request.
"""

_PLAN_SYSTEM_PROMPT_SKILLS_SUFFIX = """
{skill_metadata_block}

To use a skill, create a task with type "skill":
{{
  "type": "skill",
  "title": "short title",
  "description": "what you are doing with this skill",
  "inputs": {{
    "skill_name": "<name>",
    "context": "relevant context for this skill"
  }}
}}
"""


def _build_plan_prompt(has_tools: bool, skill_metadata_block: str = "") -> str:
    prompt = _PLAN_SYSTEM_PROMPT_BASE
    if has_tools:
        prompt += _PLAN_SYSTEM_PROMPT_TOOLS_SUFFIX
    if skill_metadata_block:
        prompt += _PLAN_SYSTEM_PROMPT_SKILLS_SUFFIX.format(
            skill_metadata_block=skill_metadata_block
        )
    return prompt


class AgentLoop:
    """驱动 Agent Loop 直到完成或触发 Guard 终止。"""

    def __init__(
        self,
        session_svc: SessionService,
        task_svc: TaskService,
        memory_svc: MemoryService,
        blackboard_svc: BlackboardService,
        task_executor: TaskExecutor,
        agent_store: AgentStore,
        llm_client: LLMClient,
        tool_registry: ToolRegistry | None = None,
        skill_registry: SkillRegistry | None = None,
        skill_router: SkillRouter | None = None,
    ) -> None:
        self._session_svc = session_svc
        self._task_svc = task_svc
        self._memory_svc = memory_svc
        self._bb_svc = blackboard_svc
        self._executor = task_executor
        self._agent_store = agent_store
        self._llm_client = llm_client
        self._tool_registry = tool_registry
        self._skill_registry = skill_registry
        self._skill_router = skill_router

    def run(self, session_id: str, agent_id: str) -> None:
        """同步驱动 Agent Loop。由 asyncio executor 在线程中调用。"""
        agent = self._load_agent(agent_id)

        # 标记 Agent 为 RUNNING
        agent.status = "RUNNING"
        self._agent_store.save(agent.to_dict())

        try:
            while True:
                # ── Guard 检查 ──────────────────────────────────────────
                session = self._session_svc.get(session_id)
                self._check_guard(session, agent)

                # ── 六阶段 ──────────────────────────────────────────────
                context = self._observe(session, agent)
                plan = self._plan(context, agent, session)

                if plan.get("done", False):
                    logger.info("Session %s: agent %s reports done", session_id, agent_id)
                    self._session_svc.transition(session_id, "SUCCEEDED")
                    break

                tasks = self._create_tasks(session_id, agent_id, plan, context)
                self._execute(tasks, agent)
                self._update_memory(session_id, agent_id, tasks, plan)

        except AppError as e:
            logger.error("AgentLoop terminated: session=%s code=%s msg=%s", session_id, e.code, e.message)
            try:
                self._session_svc.transition(session_id, "FAILED")
            except Exception:
                pass
            raise

        finally:
            # 无论成功/失败都更新 agent 状态
            agent = self._load_agent(agent_id)
            if agent.status == "RUNNING":
                agent.status = "FINISHED"
                self._agent_store.save(agent.to_dict())

    # ── 1. Observe ────────────────────────────────────────────────────────

    def _observe(self, session: Session, agent: Agent) -> PromptContext:
        """拼装上下文：system_prompt + goal + Blackboard 增量 + 消息窗口 + 摘要。"""
        bb_entries = self._bb_svc.pull(session.id, "_root", agent.id)
        bb_snippets = [e.content for e in bb_entries]

        return self._memory_svc.build_prompt_context(
            session_id=session.id,
            agent_id=agent.id,
            system_prompt=agent.system_prompt,
            goal=session.goal,
            task_description=session.goal,
            blackboard_snippets=bb_snippets,
            token_budget=session.token_budget,
        )

    # ── 2. Plan ───────────────────────────────────────────────────────────

    def _plan(self, context: PromptContext, agent: Agent, session: Session) -> dict[str, Any]:
        """调用 LLM 获取结构化 Plan，更新 token_used 和 turns_used。"""
        messages = self._build_llm_messages(context)

        # 获取 agent 可用工具列表
        llm_tools = []
        if self._tool_registry and agent.tool_list:
            llm_tools = self._tool_registry.to_llm_tools(agent.tool_list)

        # 获取 skill 元数据块（Level 1），注入 plan system prompt
        skill_metadata_block = ""
        if self._skill_registry and agent.skill_list:
            registry_block = self._skill_registry.get_metadata_block()
            if registry_block:
                skill_metadata_block = registry_block

        response = self._llm_client.send_message(
            messages=messages,
            system_prompt=_build_plan_prompt(
                has_tools=bool(llm_tools),
                skill_metadata_block=skill_metadata_block,
            ),
            tools=llm_tools if llm_tools else None,
        )

        # 累加 token
        if response.usage and response.usage.total_tokens:
            try:
                self._session_svc.add_tokens(session.id, response.usage.total_tokens)
            except AppError:
                raise  # TOKEN_BUDGET_EXCEEDED — 让外层捕获

        # 更新 turns_used
        agent.loop_guard.turns_used += 1
        self._agent_store.save(agent.to_dict())

        # 解析 JSON Plan
        try:
            plan = json.loads(response.text.strip())
        except json.JSONDecodeError:
            logger.warning("LLM returned non-JSON plan, treating as single reasoning task")
            plan = {
                "done": False,
                "summary": response.text[:200],
                "tasks": [{"type": "reasoning", "title": "Continue", "description": response.text, "inputs": {}}],
            }

        return plan

    # ── 3. CreateTask ─────────────────────────────────────────────────────

    def _create_tasks(
        self,
        session_id: str,
        agent_id: str,
        plan: dict[str, Any],
        context: PromptContext,
    ) -> list[Task]:
        """按 Plan.tasks 创建 Task 列表。"""
        tasks = []
        for task_spec in plan.get("tasks", []):
            task_type = task_spec.get("type", "reasoning")
            inputs = task_spec.get("inputs", {})

            # reasoning task：将当前上下文消息注入 inputs
            if task_type == "reasoning":
                inputs["system_prompt"] = context.system_prompt
                inputs["messages"] = [
                    {"role": "user", "content": context.goal},
                    *context.recent_messages,
                    {"role": "user", "content": task_spec.get("description", task_spec.get("title", ""))},
                ]

            # skill task：加载 Level 2 instructions，注入 inputs
            elif task_type == "skill":
                skill_name = inputs.get("skill_name", "")
                if skill_name and self._skill_router:
                    skill_def = self._skill_router.load_for_task(skill_name)
                    if skill_def:
                        inputs["skill_instructions"] = self._skill_router.build_skill_prompt(
                            skill_def, inputs
                        )
                        inputs["skill_name"] = skill_name
                # 同时注入基础上下文消息
                inputs.setdefault("messages", [
                    {"role": "user", "content": context.goal},
                    *context.recent_messages,
                    {"role": "user", "content": task_spec.get("description", task_spec.get("title", ""))},
                ])

            task = self._task_svc.create(
                session_id=session_id,
                agent_id=agent_id,
                task_type=task_type,
                title=task_spec.get("title", "Untitled"),
                description=task_spec.get("description", ""),
                inputs=inputs,
            )
            tasks.append(task)
        return tasks

    # ── 4. Execute ────────────────────────────────────────────────────────

    def _execute(self, tasks: list[Task], agent: Agent) -> None:
        """按序执行 Task 列表。"""
        for task in tasks:
            self._executor.execute(task, agent)

    # ── 5. UpdateMemory ───────────────────────────────────────────────────

    def _update_memory(
        self,
        session_id: str,
        agent_id: str,
        tasks: list[Task],
        plan: dict[str, Any],
    ) -> None:
        """写消息；检查摘要阈值；发布产出到 Blackboard。"""
        # 写入本轮产出到消息流
        summary_text = plan.get("summary", "")
        if summary_text:
            self._memory_svc.append_message(
                session_id=session_id,
                agent_id=agent_id,
                role="assistant",
                content=summary_text,
            )

        for task in tasks:
            # 重新读取最新 task 状态
            try:
                latest = self._task_svc.get(task.id)
            except AppError:
                continue
            if latest.result:
                self._memory_svc.append_message(
                    session_id=session_id,
                    agent_id=agent_id,
                    role="assistant",
                    content=latest.result,
                    task_id=task.id,
                )

        # 检查是否触发摘要（简单处理：直接用 plan.summary 作为摘要）
        if self._memory_svc.should_summarize(session_id):
            self._do_summarize(session_id, agent_id, summary_text)

        # 发布到 Blackboard _root
        if summary_text:
            self._bb_svc.publish(
                session_id=session_id,
                topic="_root",
                publisher_id=agent_id,
                content=summary_text,
            )

    def _do_summarize(self, session_id: str, agent_id: str, latest_summary: str) -> None:
        """生成并保存摘要（Phase 1：直接使用 LLM plan.summary）。"""
        from app.common.utils import now_iso
        from app.domain.models.memory import MemorySummary
        count = len(self._memory_svc.get_window(session_id, 10000))
        summary = MemorySummary(
            session_id=session_id,
            agent_id=agent_id,
            summary_text=latest_summary,
            covered_up_to=count,
            created_at=now_iso(),
        )
        self._memory_svc.save_summary(session_id, summary)

    # ── Guard ─────────────────────────────────────────────────────────────

    def _check_guard(self, session: Session, agent: Agent) -> None:
        """Guard 检查：token_budget（硬）+ turns_used（软）。"""
        # 硬检查：token budget
        if session.token_used >= session.token_budget:
            raise AppError(
                "TOKEN_BUDGET_EXCEEDED",
                f"Session {session.id} token budget exhausted ({session.token_used}/{session.token_budget})",
            )
        # 软检查：max_turns
        guard = agent.loop_guard
        if guard.turns_used >= guard.max_turns:
            logger.warning(
                "Session %s: agent %s reached max_turns=%d. Forcing done.",
                session.id, agent.id, guard.max_turns,
            )
            raise AppError(
                "MAX_TURNS_EXCEEDED",
                f"Agent {agent.id} reached max_turns={guard.max_turns}",
            )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _load_agent(self, agent_id: str) -> Agent:
        data = self._agent_store.get(agent_id)
        if data is None:
            raise AppError("AGENT_NOT_FOUND", f"Agent {agent_id} not found")
        return Agent.from_dict(data)

    def _build_llm_messages(self, context: PromptContext) -> list[LLMMessage]:
        """将 PromptContext 转为 LLMMessage 列表。"""
        messages: list[LLMMessage] = []

        # 目标
        user_content = f"Goal: {context.goal}\n\nTask: {context.task_description}"
        if context.summary_text:
            user_content = f"Previous summary:\n{context.summary_text}\n\n{user_content}"
        if context.blackboard_snippets:
            bb_text = "\n".join(f"- {s}" for s in context.blackboard_snippets)
            user_content = f"Shared context (blackboard):\n{bb_text}\n\n{user_content}"

        messages.append(LLMMessage(role="user", content=user_content))

        # 历史消息窗口
        for m in context.recent_messages:
            messages.append(LLMMessage(role=m.get("role", "user"), content=m.get("content", "")))

        return messages
