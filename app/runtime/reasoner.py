"""Reasoner：上下文构建 + 工具/技能检索（无 LLM 调用）。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from app.domain.models.agent import Agent
from app.domain.models.session import Session
from app.domain.models.task import Task
from app.domain.services.blackboard_service import BlackboardService
from app.domain.services.memory_service import MemoryService
from app.domain.services.task_service import TaskService
from app.runtime.types import ContextResource, ReasoningContext
from app.tools.definition import CallContext

if TYPE_CHECKING:
    from app.agent_template.registry import AgentTemplateRegistry
    from app.llm.base import BaseChatClient
    from app.runtime.memory_compaction import MemoryCompactionAgent
    from app.skills.registry import SkillRegistry
    from app.storage.file.agent_store import AgentStore
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
        tool_registry: "ToolRegistry | None" = None,
        skill_registry: "SkillRegistry | None" = None,
        task_svc: TaskService | None = None,
        agent_template_registry: "AgentTemplateRegistry | None" = None,
        llm_client: "BaseChatClient | None" = None,
        compaction_agent: "MemoryCompactionAgent | None" = None,
        agent_store: "AgentStore | None" = None,
    ) -> None:
        self._memory_svc = memory_svc
        self._bb_svc = blackboard_svc
        self._tool_registry = tool_registry
        self._skill_registry = skill_registry
        self._task_svc = task_svc
        self._agent_template_registry = agent_template_registry
        self._llm_client = llm_client
        self._compaction_agent = compaction_agent
        self._agent_store = agent_store

    def reason(
        self, session: Session, agent: Agent, task: Task
    ) -> ReasoningContext:
        """构建本轮 ReasoningContext。"""
        messages, bb_snippets, token_estimate = self._fetch_base(session, agent, task)
        if self._maybe_compact(session, agent, token_estimate):
            messages, bb_snippets, token_estimate = self._fetch_base(session, agent, task)
        soul, role, skill_instructions = self._extract_agent_identity(agent, task, session.id)
        return ReasoningContext(
            goal=session.goal,
            recent_messages=messages,
            blackboard_snippets=bb_snippets,
            soul=soul,
            role=role,
            skill_instructions=skill_instructions,
            actor_resources=self._build_actor_resources(session.goal, agent, task, session.id),
            observer_resources=self._build_observer_resources(agent, task),
            current_task=task,
            token_estimate=token_estimate,
            project_background=self._load_background(agent, task),
        )

    def _maybe_compact(self, session: Session, agent: Agent, memory_tokens: int) -> bool:
        """检查 token 预算，必要时触发 compaction，返回是否执行了压缩。"""
        _llm_client = self._llm_client
        if agent.llm_provider and _llm_client:
            try:
                from app.llm.registry import get_llm_registry
                _llm_client = get_llm_registry().get_client(agent.llm_provider, agent.llm_model or None)
            except Exception:
                pass
        context_limit = _llm_client.context_limit if _llm_client else 0
        if not self._memory_svc.should_summarize(
            agent.id,
            context_tokens=memory_tokens,
            context_limit=context_limit,
        ):
            return False
        self._do_compact(session, agent)
        return True

    def _do_compact(self, session: Session, agent: Agent) -> None:
        from app.common.utils import now_iso
        from app.domain.models.memory import MemorySummary

        messages = self._memory_svc.get_window(agent.id, 10000)
        summary_text = ""

        if self._compaction_agent is not None:
            try:
                agent_data = (self._agent_store.get(session.id, agent.id) if self._agent_store else None) or {}
                working_dir = (agent_data.get("settings") or {}).get("working_dir", "")
                kept, compacted_summary = self._compaction_agent.compact(
                    messages=messages,
                    session_goal=session.goal,
                    working_dir=working_dir,
                    session_id=session.id,
                    agent_id=agent.id,
                )
                if compacted_summary:
                    summary_text = compacted_summary
                    kept = [{"role": "assistant", "content": f"[Context so far]:\n{compacted_summary}"}] + kept
                if len(kept) < len(messages):
                    self._memory_svc.rewrite_messages(agent.id, kept)
            except Exception:
                logger.exception("Reasoner: compaction failed for agent %s", agent.id)

        count = self._memory_svc.count_messages(agent.id)
        self._memory_svc.save_summary(agent.id, MemorySummary(
            session_id=session.id,
            agent_id=agent.id,
            summary_text=summary_text,
            covered_up_to=count,
            created_at=now_iso(),
        ))
        agent.loop_guard.context_tokens = 0
        if self._agent_store:
            self._agent_store.save(agent.to_dict())

    def _load_background(self, agent: Agent, task: Task) -> str:
        from app.config.settings import get_settings
        wd = (
            (task.settings.get("working_dir") if task.settings else None)
            or (agent.settings or {}).get("working_dir")
            or get_settings().bash_exec_cwd
        )
        if not wd:
            return ""
        bg_path = Path(wd) / "BACKGROUND.md"
        try:
            return bg_path.read_text(encoding="utf-8") if bg_path.is_file() else ""
        except Exception:
            logger.debug("Reasoner: failed to read BACKGROUND.md from %s", bg_path)
            return ""

    def _extract_agent_identity(
        self, agent: Agent, task: Task, session_id: str = ""
    ) -> tuple[str, str, str]:
        """提取 soul、role、skill_instructions（plan/act 均适用）。"""
        soul = agent.soul_md or ""
        role = agent.role_md or ""
        skill_instructions = ""
        skill_name = task.settings.get("skill_name") if task.settings else None
        if skill_name and self._skill_registry:
            from app.config.settings import get_settings
            from app.tools.definition import CallContext
            _wd = (
                (task.settings.get("working_dir") if task.settings else None)
                or agent.settings.get("working_dir")
                or get_settings().bash_exec_cwd
            )
            ctx = CallContext(session_id=session_id, agent_id=agent.id, task=task, working_dir=_wd or "")
            skill_def = self._skill_registry.load_definition(skill_name, ctx)
            if skill_def is not None:
                skill_instructions = skill_def.instructions or ""
        return soul, role, skill_instructions

    # ── 私有：共享数据获取 ──────────────────────────────────────────────────

    def _fetch_base(self, session: Session, agent: Agent, task: Task) -> tuple:
        """获取 memory、blackboard、token 估算等共享数据，返回 tuple。"""
        messages = self._memory_svc.get_all_messages(agent.id)

        # bb_entries = self._bb_svc.pull(session.id, "_root", agent.id)
        # bb_snippets = [entry.content for entry in bb_entries]
        bb_snippets = []

        if self._task_svc:
            for child in self._task_svc.list_children(task.id, session.id):
                for entry in self._bb_svc.pull(session.id, child.id, agent.id):
                    bb_snippets.append(entry.content)

        from app.common.utils import estimate_tokens
        from app.llm.types import content_to_text

        def _item_text(v: object) -> str:
            return content_to_text(v) if isinstance(v, (str, list)) else ""

        text_sample = (
            session.goal
            + " ".join(_item_text(m.get("content", "")) for m in messages)
            + " ".join(_item_text(s) for s in bb_snippets)
        )
        token_estimate = estimate_tokens(text_sample)

        return messages, bb_snippets, token_estimate

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

    def _build_actor_resources(self, goal: str, agent: Agent, task: Task, session_id: str = "") -> list[ContextResource]:
        """按 task.type 构建资源列表：plan 加载 skills + planner tools，act 加载 tools。"""
        from app.config.settings import get_settings
        from app.tools.definition import CallContext
        _wd = (
            (task.settings.get("working_dir") if task.settings else None)
            or agent.settings.get("working_dir")
            or get_settings().bash_exec_cwd
        )
        ctx = CallContext(session_id=session_id, agent_id=agent.id, task=task, working_dir=_wd or "")
        allowed = self._resolve_act_tool_names(agent)
        skill_resources = [
            ContextResource(name=name, description=desc, kind="skill")
            for name, desc in self._retrieve_skills(goal, agent, ctx)
        ]
        tool_resources = [
            ContextResource(name=t.name, description=t.description, kind="tool", llm_tool=t)
            for t in self._tool_registry.to_llm_tools(list(allowed))
        ]
        agent_resources = self._build_agent_resources(agent)
        return skill_resources + tool_resources + agent_resources

    def _build_agent_resources(self, agent: Agent) -> list[ContextResource]:
        """构建可见 sub-agent 列表；仅 has_spawn_permission=True 时生效。

        可见范围由当前 agent template 的 SOUL.md subagents 字段控制：
        空列表 = 全部可见，非空 = 仅列出的 template name 可见。
        """
        if not agent.has_spawn_permission or not self._agent_template_registry:
            return []
        workspace_dir = (agent.settings or {}).get("working_dir", "")
        own_meta = self._agent_template_registry.get_metadata_by_id(agent.template_id or "")
        allowlist: set[str] | None = set(own_meta.subagents) if own_meta and own_meta.subagents else None
        return [
            ContextResource(name=m.name, description=m.description, kind="agent")
            for m in self._agent_template_registry.list_all(workspace_dir=workspace_dir)
            if m.name != (agent.template_id or "")
            and (allowlist is None or m.name in allowlist)
        ]

    def _build_observer_resources(self, agent: Agent, task: Task) -> list[ContextResource]:
        """组装 Observer 阶段可用工具，由 observe_tool_list 统一配置。

        submit_task_reviews 由 Observer 在第二轮内部注入，不经此处。
        """
        if not self._tool_registry:
            return []
        allowed = self._resolve_observe_tool_names(agent)
        if not allowed:
            return []
        tool_resources = [
            ContextResource(name=t.name, description=t.description, kind="tool", llm_tool=t)
            for t in self._tool_registry.to_llm_tools(list(allowed))
        ]
        return tool_resources

    def _retrieve_skills(self, goal: str, agent: Agent, ctx: "CallContext | None" = None) -> list[tuple[str, str]]:
        """返回 (name, description) 元组列表。"""
        if not self._skill_registry:
            return []
        return [
            (m.name, m.description)
            for m in self._skill_registry.list_all(ctx)
        ]
