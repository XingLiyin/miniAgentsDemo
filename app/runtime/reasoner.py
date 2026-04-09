"""Reasoner：上下文构建 + 工具/技能检索（无 LLM 调用）。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.domain.models.agent import Agent
from app.domain.models.session import Session
from app.domain.models.task import Task
from app.domain.services.blackboard_service import BlackboardService
from app.domain.services.memory_service import MemoryService
from app.runtime.agent_controller import AgentController
from app.runtime.types import ContextResource, ReasoningContext

if TYPE_CHECKING:
    from app.skills.registry import SkillRegistry
    from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class Reasoner:
    """上下文构建，无 LLM 调用。

    根据 task.type 判断模式，差异化加载 tools / skills 并构建
    Actor 从 ctx.resources 渲染资源列表，不感知 plan/act 模式。

    plan 模式：加载 skills，不加载 tools；构建规划指导 prompt。
    act  模式：加载 tools，不加载 skills；构建执行指导 + tool awareness prompt。
    """

    def __init__(
        self,
        memory_svc: MemoryService,
        blackboard_svc: BlackboardService,
        agent_controller: AgentController,
        tool_registry: "ToolRegistry | None" = None,
        skill_registry: "SkillRegistry | None" = None,
    ) -> None:
        self._memory_svc = memory_svc
        self._bb_svc = blackboard_svc
        self._agent_controller = agent_controller
        self._tool_registry = tool_registry
        self._skill_registry = skill_registry

    def reason(
        self, session: Session, agent: Agent, task: Task
    ) -> ReasoningContext:
        """构建本轮 ReasoningContext。"""
        messages, summary_text, bb_snippets, token_estimate = self._fetch_base(session, agent)
        soul, role, skill_instructions = self._extract_agent_identity(agent, task)

        return ReasoningContext(
            goal=session.goal,
            recent_messages=messages,
            summary_text=summary_text,
            blackboard_snippets=bb_snippets,
            soul=soul,
            role=role,
            skill_instructions=skill_instructions,
            resources=self._build_resources(session.goal, agent, task),
            observer_tools=self._build_observer_tools(agent, task),
            current_task=task,
            token_estimate=token_estimate,
        )

    def _extract_agent_identity(self, agent: Agent, task: Task) -> tuple[str, str, str]:
        """提取 soul、role、skill_instructions（plan/act 均适用）。"""
        soul = agent.soul_md or agent.system_prompt or ""
        role = agent.role_md or ""
        skill_instructions = ""
        skill_name = task.inputs.get("skill_name") if task.inputs else None
        if skill_name and self._skill_registry:
            skill_def = self._skill_registry.load_definition(skill_name)
            if skill_def is not None:
                skill_instructions = skill_def.instructions or ""
        return soul, role, skill_instructions

    # ── 私有：共享数据获取 ──────────────────────────────────────────────────

    def _fetch_base(self, session: Session, agent: Agent) -> tuple:
        """获取 memory、blackboard、token 估算等共享数据，返回 tuple。

        若 agent.inherit_memory=False，跳过 session 记忆加载（完全隔离执行）。
        """
        if agent.inherit_memory:
            messages = self._memory_svc.get_window(agent.id)
            summary = self._memory_svc.get_summary(agent.id)
            summary_text = summary.summary_text if summary else ""
        else:
            messages = []
            summary_text = ""

        bb_entries = self._bb_svc.pull(session.id, "_root", agent.id)
        bb_snippets = [entry.content for entry in bb_entries]

        from app.common.utils import estimate_tokens
        text_sample = (
            session.goal
            + summary_text
            + " ".join(m.get("content", "") for m in messages)
            + " ".join(bb_snippets)
        )
        token_estimate = estimate_tokens(text_sample)

        return messages, summary_text, bb_snippets, token_estimate

    # ── 私有辅助 ──────────────────────────────────────────────────────────────

    def _build_resources(self, goal: str, agent: Agent, task: Task) -> list[ContextResource]:
        """按 task.type 构建资源列表：plan 加载 skills + planner tools，act 加载 tools。"""
        if task.type == "plan":
            skill_resources = [
                ContextResource(name=name, description=desc, kind="skill")
                for name, desc in self._retrieve_skills(goal, agent)
            ]
            tool_resources = [
                ContextResource(name=t.name, description=t.description, kind="tool", llm_tool=t)
                for t in self._agent_controller.get_llm_schemas(scope="planner")
            ]
            return skill_resources + tool_resources
        else:
            return [
                ContextResource(name=t.name, description=t.description, kind="tool", llm_tool=t)
                for t in self._retrieve_tools(goal, agent)
                        + self._agent_controller.get_llm_schemas(scope="actor")
            ]

    def _build_observer_tools(self, agent: Agent, task: Task) -> list:
        """组装 Observer 阶段可用工具。

        plan task：submit_plan（必须）+ submit_observation（兜底）+ observer_opt 过滤
        act  task：submit_observation（必须）+ observer_opt 过滤
        """
        opt = [t for t in self._agent_controller.get_llm_schemas(scope="observer_opt")
               if t.name in (agent.tool_list or [])]
        if task.type == "plan":
            base = (self._agent_controller.get_llm_schemas(scope="observer_plan")
                    + self._agent_controller.get_llm_schemas(scope="observer"))
        else:
            base = self._agent_controller.get_llm_schemas(scope="observer")
        return base + opt

    def _retrieve_tools(self, goal: str, agent: Agent) -> list:
        if not self._tool_registry or not agent.tool_list:
            return []

        try:
            from app.tools.tool_store_client import get_tool_store_client
            store_client = get_tool_store_client()
            if store_client.enabled:
                results = store_client.search(goal, top_k=10)
                if results:
                    allowed = set(agent.tool_list)
                    filtered = [r for r in results if r.name in allowed]
                    if filtered:
                        return self._tool_registry.to_llm_tools(
                            [r.name for r in filtered]
                        )
        except Exception:
            logger.debug("Reasoner: tool store search failed, falling back to full list")

        return self._tool_registry.to_llm_tools(agent.tool_list)

    def _retrieve_skills(self, goal: str, agent: Agent) -> list[tuple[str, str]]:
        """返回 (name, description) 元组列表。"""
        if not self._skill_registry or not agent.skill_list:
            return []

        try:
            from app.skills.skill_store_client import get_skill_store_client
            store_client = get_skill_store_client()
            if store_client.enabled:
                results = store_client.search(goal, top_k=5)
                if results:
                    allowed = set(agent.skill_list)
                    return [(r.name, r.description) for r in results if r.name in allowed]
        except Exception:
            logger.debug("Reasoner: skill store search failed, falling back to full list")

        return [
            (m.name, m.description)
            for m in self._skill_registry.list_all()
            if m.name in agent.skill_list
        ]
