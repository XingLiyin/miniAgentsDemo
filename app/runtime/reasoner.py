"""Reasoner：上下文构建 + 工具/技能检索（无 LLM 调用）。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.domain.models.agent import Agent
from app.domain.models.session import Session
from app.domain.services.blackboard_service import BlackboardService
from app.domain.services.memory_service import MemoryService
from app.runtime.types import ReasoningContext, SkillMeta

if TYPE_CHECKING:
    from app.skills.registry import SkillRegistry
    from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class Reasoner:
    """Phase 1：纯检索，无 LLM 调用。

    职责：
      1. 从 MemoryService 取 recent_messages 和 summary_text
      2. 从 BlackboardService 取 blackboard_snippets
      3. 检索 relevant_tools（优先 ToolStoreClient 语义搜索，降级全列表）
      4. 检索 relevant_skills（优先 SkillStoreClient 语义搜索，降级全列表）
    """

    def __init__(
        self,
        memory_svc: MemoryService,
        blackboard_svc: BlackboardService,
        tool_registry: "ToolRegistry | None" = None,
        skill_registry: "SkillRegistry | None" = None,
    ) -> None:
        self._memory_svc = memory_svc
        self._bb_svc = blackboard_svc
        self._tool_registry = tool_registry
        self._skill_registry = skill_registry

    def reason(self, session: Session, agent: Agent) -> ReasoningContext:
        """构建本轮的 ReasoningContext。"""
        # 1. Memory
        messages = self._memory_svc.get_window(session.id)
        summary = self._memory_svc.get_summary(session.id)
        summary_text = summary.summary_text if summary else ""

        # 2. Blackboard
        bb_entries = self._bb_svc.pull(session.id, "_root", agent.id)
        bb_snippets = [entry.content for entry in bb_entries]

        # 3. Tools
        relevant_tools = self._retrieve_tools(session.goal, agent)

        # 4. Skills
        relevant_skills = self._retrieve_skills(session.goal, agent)

        # Token 估算
        from app.common.utils import estimate_tokens
        text_sample = (
            session.goal
            + summary_text
            + " ".join(m.get("content", "") for m in messages)
            + " ".join(bb_snippets)
        )
        token_estimate = estimate_tokens(text_sample)

        return ReasoningContext(
            goal=session.goal,
            recent_messages=messages,
            summary_text=summary_text,
            blackboard_snippets=bb_snippets,
            relevant_tools=relevant_tools,
            relevant_skills=relevant_skills,
            token_estimate=token_estimate,
        )

    # ── 私有辅助 ──────────────────────────────────────────────────────────────

    def _retrieve_tools(self, goal: str, agent: Agent) -> list:
        if not self._tool_registry or not agent.tool_list:
            return []

        try:
            from app.tools.tool_store_client import get_tool_store_client
            store_client = get_tool_store_client()
            if store_client.enabled:
                results = store_client.search(goal, top_k=10)
                if results:
                    # 过滤：只返回 agent.tool_list 中允许的
                    allowed = set(agent.tool_list)
                    filtered = [r for r in results if r.name in allowed]
                    if filtered:
                        return self._tool_registry.to_llm_tools(
                            [r.name for r in filtered]
                        )
        except Exception:
            logger.debug("Reasoner: tool store search failed, falling back to full list")

        return self._tool_registry.to_llm_tools(agent.tool_list)

    def _retrieve_skills(self, goal: str, agent: Agent) -> list[SkillMeta]:
        if not self._skill_registry or not agent.skill_list:
            return []

        try:
            from app.skills.skill_store_client import get_skill_store_client
            store_client = get_skill_store_client()
            if store_client.enabled:
                results = store_client.search(goal, top_k=5)
                if results:
                    allowed = set(agent.skill_list)
                    return [
                        SkillMeta(name=r.name, description=r.description)
                        for r in results
                        if r.name in allowed
                    ]
        except Exception:
            logger.debug("Reasoner: skill store search failed, falling back to full list")

        return [
            SkillMeta(name=m.name, description=m.description)
            for m in self._skill_registry.list_all()
            if m.name in agent.skill_list
        ]
