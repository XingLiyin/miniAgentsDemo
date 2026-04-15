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
        soul = agent.soul_md or ""
        role = agent.role_md or ""
        skill_instructions = ""
        skill_name = task.settings.get("skill_name") if task.settings else None
        if skill_name and self._skill_registry:
            skill_def = self._skill_registry.load_definition(skill_name)
            if skill_def is not None:
                skill_instructions = skill_def.instructions or ""
        return soul, role, skill_instructions

    # ── 私有：共享数据获取 ──────────────────────────────────────────────────

    def _fetch_base(self, session: Session, agent: Agent) -> tuple:
        """获取 memory、blackboard、token 估算等共享数据，返回 tuple。

        
        """
        messages = self._memory_svc.get_window(agent.id)
        summary = self._memory_svc.get_summary(agent.id)
        summary_text = summary.summary_text if summary else ""

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

    def _resolve_act_tool_names(self, agent: Agent) -> set[str]:
        """展开 act 阶段的有效工具名集合：显式列表 + 订阅 MCP server 的全部工具。"""
        tools = set(agent.act_tool_list or [])
        if self._tool_registry and agent.mcp_act_servers:
            for server_name in agent.mcp_act_servers:
                tools.update(self._tool_registry.get_server_tool_names(server_name))
        return tools

    def _resolve_observe_tool_names(self, agent: Agent) -> set[str]:
        """展开 observe 阶段的有效工具名集合：显式列表 + 订阅 MCP server 的全部工具。"""
        tools = set(agent.observe_tool_list or [])
        if self._tool_registry and agent.mcp_observe_servers:
            for server_name in agent.mcp_observe_servers:
                tools.update(self._tool_registry.get_server_tool_names(server_name))
        return tools

    def _build_resources(self, goal: str, agent: Agent, task: Task) -> list[ContextResource]:
        """按 task.type 构建资源列表：plan 加载 skills + planner tools，act 加载 tools。

        从 AgentController scope 获取的控制工具，经有效 act 工具集过滤后才暴露给 LLM。
        """
        allowed = self._resolve_act_tool_names(agent)
        skill_resources = [
            ContextResource(name=name, description=desc, kind="skill")
            for name, desc in self._retrieve_skills(goal, agent)
        ]
        ctrl_tools = [
            t for t in self._agent_controller.get_llm_schemas(scope="actor")
            if t.name in allowed
        ]
        tool_resources = [
            ContextResource(name=t.name, description=t.description, kind="tool", llm_tool=t)
            for t in self._retrieve_tools(goal, agent, allowed) + ctrl_tools
        ]
        return skill_resources + tool_resources

    def _build_observer_tools(self, agent: Agent, task: Task) -> list:
        """组装 Observer 阶段可用工具。

        所有从 scope 获取的工具均经有效 observe 工具集过滤。
        submit_task_reviews 由 Observer 在第二轮内部注入，不经此处。
        """
        allowed = self._resolve_observe_tool_names(agent)
        candidates = self._agent_controller.get_llm_schemas(scope="observer")
        return [t for t in candidates if t.name in allowed]

    def _retrieve_tools(self, goal: str, agent: Agent, allowed: set[str] | None = None) -> list:
        if not self._tool_registry:
            return []
        if allowed is None:
            allowed = self._resolve_act_tool_names(agent)
        if not allowed:
            return []

        try:
            from app.tools.tool_store_client import get_tool_store_client
            store_client = get_tool_store_client()
            if store_client.enabled:
                results = store_client.search(goal, top_k=10)
                if results:
                    filtered = [r for r in results if r.name in allowed]
                    if filtered:
                        return self._tool_registry.to_llm_tools(
                            [r.name for r in filtered]
                        )
        except Exception:
            logger.debug("Reasoner: tool store search failed, falling back to full list")

        return self._tool_registry.to_llm_tools(list(allowed))

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
