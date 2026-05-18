"""Reasoner：上下文构建 + 工具/技能检索（无 LLM 调用）。"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from app.domain.models.agent import Agent
from app.domain.models.session import Session
from app.domain.models.task import Task
from app.domain.services.blackboard_service import BlackboardService
from app.domain.services.memory_service import MemoryService
from app.domain.services.task_service import TaskService
from app.runtime.types import ContextResource, ReasoningContext
from app.tools.types import CallContext

if TYPE_CHECKING:
    from app.agent_template.loader import AgentLoader
    from app.runtime.memory_compaction import MemoryCompactionAgent
    from app.skills.registry import SkillRegistry
    from app.skills.skill import Skill
    from app.storage.file.agent_store import AgentStore
    from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_SKILL_CACHE_TTL = 60.0  # 秒，每个 agent 的 skill 列表缓存时间


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
        agent_template_loader: "AgentLoader | None" = None,
        compaction_agent: "MemoryCompactionAgent | None" = None,
        agent_store: "AgentStore | None" = None,
    ) -> None:
        self._memory_svc = memory_svc
        self._bb_svc = blackboard_svc
        self._tool_registry = tool_registry
        self._skill_registry = skill_registry
        self._task_svc = task_svc
        self._agent_template_loader = agent_template_loader
        self._compaction_agent = compaction_agent
        self._agent_store = agent_store
        # (session_id, agent_id, working_dir) → (fetched_at, skills)
        self._skill_cache: dict[tuple[str, str, str], tuple[float, list["Skill"]]] = {}

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

    def evict_session(self, session_id: str) -> None:
        """Session 结束时清理该 session 下所有 agent 的 skill 缓存。"""
        stale = [k for k in self._skill_cache if k[0] == session_id]
        for k in stale:
            del self._skill_cache[k]

    def _maybe_compact(self, session: Session, agent: Agent, memory_tokens: int) -> bool:
        """检查 token 预算，必要时触发 compaction，返回是否执行了压缩。"""
        context_limit = 0
        try:
            from app.config.settings import get_settings
            from app.llm.registry import get_llm_registry
            provider = session.llm_provider or get_settings().default_llm_provider
            _llm_client = get_llm_registry().get_client(provider, session.llm_model or None)
            context_limit = _llm_client.context_limit
        except Exception:
            pass
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
                from app.config.settings import resolve_working_dir
                working_dir = resolve_working_dir((agent_data.get("settings") or {}).get("working_dir", ""))
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
        from app.config.settings import resolve_working_dir
        wd = resolve_working_dir(
            (task.settings.get("working_dir") if task.settings else None)
            or (agent.settings or {}).get("working_dir")
            or get_settings().bash_exec_cwd
            or ""
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
        soul = agent.actor.instruction_md or ""
        role = agent.observer.instruction_md or ""
        skill_instructions = ""
        skill_name = task.settings.get("skill_name") if task.settings else None
        if skill_name and self._skill_registry:
            from app.config.settings import get_settings
            from app.tools.types import CallContext
            from app.config.settings import resolve_working_dir
            _wd = resolve_working_dir(
                (task.settings.get("working_dir") if task.settings else None)
                or agent.settings.get("working_dir")
                or get_settings().bash_exec_cwd
                or ""
            )
            ctx = CallContext(session_id=session_id, agent_id=agent.id, task=task, working_dir=_wd)
            skill_def = self._skill_registry.load_definition(skill_name, ctx)
            if skill_def is not None:
                skill_instructions = skill_def.instructions or ""
            if skill_instructions:
                skill_instructions = f"Instructions for skill '{skill_name}':\n{skill_instructions}"
                cached = task.settings.get("_skill_instructions_cache")
                if cached != skill_instructions and self._task_svc:
                    task.settings["_skill_instructions_cache"] = skill_instructions
                    try:
                        self._task_svc.save(task)
                    except Exception:
                        logger.warning("Reasoner: failed to cache skill instructions for task %s", task.id)
            elif task.settings:
                logger.warning(
                    "Reasoner: skill '%s' for task %s unavailable, no instructions available",
                    skill_name, task.id,
                )
                skill_instructions = task.settings.get("_skill_instructions_cache", "")
                if skill_instructions:
                    logger.warning(
                        "Reasoner: skill '%s' unavailable, using cached instructions for task %s",
                        skill_name, task.id,
                    )
        return soul, role, skill_instructions

    # ── 私有：共享数据获取 ──────────────────────────────────────────────────

    def _fetch_base(self, session: Session, agent: Agent, task: Task) -> tuple:
        """获取 memory、blackboard、token 估算等共享数据，返回 tuple。"""
        messages = self._memory_svc.get_all_messages(agent.id)

        bb_snippets = []
        for tracked_task_id in agent.tracking_tasks:
            for entry in self._bb_svc.pull(session.id, tracked_task_id, agent.id):
                bb_snippets.append(entry.content)

        from app.common.utils import estimate_tokens
        from app.llm.types import content_to_text

        def _item_text(v: object) -> str:
            return content_to_text(v) if isinstance(v, (str, list)) else ""

        if agent.loop_guard.context_tokens > 0:
            # 有上次精确值：只估算新增消息，避免重复计算历史
            new_messages = messages[agent.loop_guard.context_message_count:]
            new_text = " ".join(_item_text(m.get("content", "")) for m in new_messages)
            token_estimate = agent.loop_guard.context_tokens + estimate_tokens(new_text)
        else:
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
        tools = set(agent.actor.tools or [])
        if self._tool_registry and agent.actor.mcp_servers:
            for server_name in agent.actor.mcp_servers:
                tools.update(self._tool_registry.get_server_tool_names(server_name))
        return tools

    def _resolve_observe_tool_names(self, agent: Agent) -> set[str]:
        """展开 observe 阶段的有效工具名集合：显式列表 + 订阅 MCP server 的全部工具。"""
        tools = set(agent.observer.tools or [])
        if self._tool_registry and agent.observer.mcp_servers:
            for server_name in agent.observer.mcp_servers:
                tools.update(self._tool_registry.get_server_tool_names(server_name))
        return tools

    def _build_actor_resources(self, goal: str, agent: Agent, task: Task, session_id: str = "") -> list[ContextResource]:
        """按 task.type 构建资源列表：plan 加载 skills + planner tools，act 加载 tools。"""
        from app.config.settings import get_settings
        from app.tools.types import CallContext
        from app.config.settings import resolve_working_dir
        _wd = resolve_working_dir(
            (task.settings.get("working_dir") if task.settings else None)
            or agent.settings.get("working_dir")
            or get_settings().bash_exec_cwd
            or ""
        )
        ctx = CallContext(session_id=session_id, agent_id=agent.id, task=task, working_dir=_wd)
        allowed = self._resolve_act_tool_names(agent)
        skill_resources = []
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
        if not agent.has_spawn_permission or not self._agent_template_loader:
            return []
        workspace_dir = (agent.settings or {}).get("working_dir", "")
        own_meta = self._agent_template_loader.get_details_by_id(agent.template_id) if agent.template_id else None
        allowlist: set[str] | None = set(own_meta.actor_capability.subagents) if own_meta and own_meta.actor_capability.subagents else None
        return [
            ContextResource(name=m.name, description=m.description, kind="agent")
            for m in self._agent_template_loader.list_details(workspace_dir)
            if (allowlist is None or m.name in allowlist)
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
        """返回 (name, description) 元组列表。结果缓存在 Reasoner 内，按 TTL + working_dir 失效。"""
        if not self._skill_registry:
            return []
        wd = ctx.working_dir if ctx else ""
        key = (agent.session_id, agent.id, wd)
        now = time.monotonic()
        cached = self._skill_cache.get(key)
        if cached is not None and now - cached[0] < _SKILL_CACHE_TTL:
            return [(s.name, s.description) for s in cached[1]]

        skills = self._skill_registry.fetch_skills(ctx)
        allowlist = set(agent.actor.skills)
        if allowlist:
            skills = [s for s in skills if s.name in allowlist]
        self._skill_cache[key] = (now, skills)
        return [(s.name, s.description) for s in skills]
