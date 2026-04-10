"""Session 生命周期编排器（Phase 1）。

职责：创建 Session → 创建 root Agent → 异步触发 AgentLoop。
Guard 检查也在此处（token_budget）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.common.errors import AppError
from app.common.utils import new_agent_id, now_iso
from app.config.settings import get_settings
from app.domain.events.event_bus import EventBus
from app.domain.models.agent import Agent, LoopGuard
from app.domain.models.session import Session
from app.domain.services.agent_template_service import AgentTemplateService
from app.domain.services.memory_service import MemoryService
from app.domain.services.session_service import SessionService
from app.domain.services.task_service import TaskService
from app.storage.file.agent_store import AgentStore
from app.storage.file.blackboard_store import BlackboardStore
from app.storage.file.task_store import TaskStore
from app.storage.file.tool_call_store import ToolCallStore

if TYPE_CHECKING:
    from app.orchestrator.lifecycle_manager import LifecycleManager

logger = logging.getLogger(__name__)


def _apply_template_to_agent(tpl: "AgentTemplate", agent: Agent) -> None:  # type: ignore[name-defined]
    """从 AgentTemplate 独立填充 soul_md / role_md / system_prompt，互不污染。

    - soul_md → 驱动 Actor 阶段 system prompt（执行人格）
    - role_md → 驱动 Observer 阶段 system prompt（评判准则）
    - system_prompt → legacy fallback（soul_md 为空时由 Actor 使用）
    """
    agent.system_prompt = tpl.system_prompt
    agent.soul_md = getattr(tpl, "soul_md", "")
    agent.role_md = getattr(tpl, "role_md", "")


class SessionManager:
    """会话生命周期编排。"""

    def __init__(
        self,
        session_svc: SessionService,
        template_svc: AgentTemplateService,
        agent_store: AgentStore,
        event_bus: EventBus,
        task_svc: TaskService | None = None,
        memory_svc: MemoryService | None = None,
        task_store: TaskStore | None = None,
        tool_call_store: ToolCallStore | None = None,
        blackboard_store: BlackboardStore | None = None,
    ) -> None:
        self._session_svc = session_svc
        self._template_svc = template_svc
        self._agent_store = agent_store
        self._bus = event_bus
        self._task_svc = task_svc
        self._memory_svc = memory_svc
        self._task_store = task_store
        self._tool_call_store = tool_call_store
        self._blackboard_store = blackboard_store
        self._lifecycle_manager: "LifecycleManager | None" = None

    def set_lifecycle_manager(self, lm: "LifecycleManager") -> None:
        """注入 LifecycleManager（避免循环导入）。"""
        self._lifecycle_manager = lm

    def create_session(
        self,
        goal: str,
        template_id: str | None = None,
        token_budget: int | None = None,
        root_max_turns: int | None = None,
        llm_name: str | None = None,
    ) -> tuple[Session, str]:
        """创建 Session + root Agent，返回 (Session, root_agent_id)。
        由调用方决定是否/如何启动 AgentLoop（sync or async）。
        """
        settings = get_settings()
        session = self._session_svc.create(
            goal=goal,
            template_id=template_id,
            token_budget=token_budget or settings.default_token_budget,
            root_max_turns=root_max_turns or settings.default_root_max_turns,
        )

        # 构建 root Agent
        # TODO: skill_list 需要改成后续动态查询，当前默认空列表
        system_prompt = settings.agent_default_system_prompt
        tool_list: list[str] = []
        skill_list: list[str] = []
        soul_path: str | None = None
        tpl = None
        if template_id:
            try:
                tpl = self._template_svc.get(template_id)
            except AppError:
                logger.warning("Template %s not found, falling back to default", template_id)
        if tpl is None:
            tpl = self._template_svc.get_by_name(settings.default_agent_template_name)
            if tpl is None:
                logger.warning(
                    "Default template '%s' not found, using settings fallback",
                    settings.default_agent_template_name,
                )
        if tpl is not None:
            tool_list = tpl.tool_list
            skill_list = tpl.skill_list
            soul_path = tpl.source_dir or None

        now = now_iso()
        agent = Agent(
            id=new_agent_id(),
            session_id=session.id,
            template_id=template_id,
            name="root",
            status="IDLE",
            system_prompt=system_prompt,
            tool_list=tool_list,
            skill_list=skill_list,
            soul_path=soul_path,
            loop_guard=LoopGuard(turns_used=0, max_turns=session.root_max_turns),
            llm_name=llm_name or settings.agent_default_llm_name,
            has_spawn_permission=True,   # root agent 默认可以 spawn
            spawn_depth=0,
            created_at=now,
            updated_at=now,
        )
        if tpl is not None:
            _apply_template_to_agent(tpl, agent)
        self._agent_store.save(agent.to_dict())
        self._session_svc.set_root_agent(session.id, agent.id)

        # 初始化 LifecycleManager session 状态
        if self._lifecycle_manager is not None:
            self._lifecycle_manager.init_session(session.id)

        # 创建初始 plan task（use_subagent=True：由 plan sub-agent 执行规划）
        if self._task_svc is not None:
            self._task_svc.create_plan_task(
                session_id=session.id,
                creator_agent_id=agent.id,
                title=f"Plan: {goal[:80]}",
                description=goal,
            )

        return session, agent.id

    def schedule_loop(self, session_id: str, agent_id: str) -> None:
        """找到 session 的第一个 PENDING task，交由 LifecycleManager 启动。"""
        if self._lifecycle_manager is None:
            logger.warning("LifecycleManager not set, session %s will not run", session_id)
            return
        if self._task_svc is None:
            logger.error("task_svc not set, cannot schedule session %s", session_id)
            return
        tasks = self._task_svc.list_pending(session_id)
        if not tasks:
            logger.error("No pending task found for session %s", session_id)
            return
        self._lifecycle_manager.schedule_initial_task(session_id, agent_id, tasks[0].id)

    def continue_session(self, session_id: str, user_message: str) -> Session:
        """Append a user message and re-start the agent loop if the session has ended."""
        from app.domain.services.memory_service import MemoryService
        from app.storage.file.memory_store import MemoryStore

        session = self._session_svc.get(session_id)

        if session.status == "CANCELED":
            raise AppError("SESSION_CANCELED", f"Session {session_id} is canceled and cannot be continued")

        # Append the user message to root agent's memory
        mem_svc = MemoryService(store=MemoryStore())
        mem_svc.append_message(
            agent_id=session.root_agent_id or "user",
            role="user",
            content=user_message,
            session_id=session_id,
        )
        # Push SSE user message event
        try:
            from app.common.utils import now_iso
            from app.runtime.sse_bus import get_sse_bus
            get_sse_bus().push(session_id, {
                "type": "message",
                "role": "user",
                "content": user_message,
                "created_at": now_iso(),
            })
        except Exception:
            pass

        # Loop still active — message will be picked up automatically
        if session.status in ("QUEUED", "RUNNING"):
            return session

        # Session ended — create a fresh root agent and restart the loop
        settings = get_settings()
        tool_list: list[str] = []
        skill_list: list[str] = []
        soul_path: str | None = None
        tpl = None
        if session.template_id:
            try:
                tpl = self._template_svc.get(session.template_id)
            except AppError:
                logger.warning("Template %s not found, falling back to default", session.template_id)
        if tpl is None:
            tpl = self._template_svc.get_by_name(settings.default_agent_template_name)
            if tpl is None:
                logger.warning(
                    "Default template '%s' not found, using settings fallback",
                    settings.default_agent_template_name,
                )
        if tpl is not None:
            tool_list = tpl.tool_list
            skill_list = tpl.skill_list
            soul_path = tpl.source_dir or None

        now = now_iso()
        agent = Agent(
            id=new_agent_id(),
            session_id=session_id,
            template_id=session.template_id,
            name="root",
            status="IDLE",
            system_prompt=settings.agent_default_system_prompt,
            tool_list=tool_list,
            skill_list=skill_list,
            soul_path=soul_path,
            loop_guard=LoopGuard(turns_used=0, max_turns=session.root_max_turns),
            llm_name=settings.agent_default_llm_name,
            has_spawn_permission=True,
            spawn_depth=0,
            created_at=now,
            updated_at=now,
        )
        if tpl is not None:
            _apply_template_to_agent(tpl, agent)

        self._agent_store.save(agent.to_dict())
        self._session_svc.set_root_agent(session_id, agent.id)
        self._session_svc.transition(session_id, "QUEUED")
        # 重新初始化 LM 状态（旧 session 的状态已过期）
        if self._lifecycle_manager is not None:
            self._lifecycle_manager.init_session(session_id)
        # 创建新 plan task（use_subagent=True：由 plan sub-agent 执行规划）
        if self._task_svc is not None:
            self._task_svc.create_plan_task(
                session_id=session_id,
                creator_agent_id=agent.id,
                title=f"Plan: {user_message[:80]}",
                description=user_message,
            )
        self.schedule_loop(session_id, agent.id)

        return self._session_svc.get(session_id)

    def answer_input(self, session_id: str, content: str) -> Session:
        """Submit user answer for a WAITING_INPUT session and unblock the agent thread."""
        from app.runtime.hitl_store import get_hitl_store

        session = self._session_svc.get(session_id)
        if session.status != "WAITING_INPUT":
            raise AppError(
                "INVALID_STATE",
                f"Session {session_id} is not waiting for input (status={session.status})",
            )

        # 注入答案并唤醒阻塞的工作线程
        entry = get_hitl_store().submit(session_id, content)

        # 写入记忆（由 entry 携带的 agent_id 确定归属）
        if entry is not None and self._memory_svc is not None:
            self._memory_svc.append_message(
                agent_id=entry.agent_id,
                role="user",
                content=content,
                session_id=session_id,
            )

        # 推送用户回答气泡
        try:
            from app.common.utils import now_iso
            from app.runtime.sse_bus import get_sse_bus
            get_sse_bus().push(session_id, {
                "type": "message",
                "role": "user",
                "content": content,
                "created_at": now_iso(),
            })
        except Exception:
            pass

        # 工作线程自行将 session 转回 RUNNING，此处无需重启 loop
        return self._session_svc.get(session_id)

    def cancel_session(self, session_id: str) -> Session:
        """取消 Session。"""
        return self._session_svc.transition(session_id, "CANCELED")

    def delete_session(self, session_id: str) -> None:
        """级联删除 Session 及其所有关联数据。运行中的会话直接强制删除。"""
        session = self._session_svc.get(session_id)

        # 删除该 session 下的所有 Task 文件
        if self._task_svc is not None and self._task_store is not None:
            tasks = self._task_svc.list_by_session(session_id)
            for task in tasks:
                self._task_store.delete(task.id)

        # 删除工具调用日志（单个 JSONL 文件）
        if self._tool_call_store is not None:
            self._tool_call_store.delete(session_id)

        # 删除该 session 下所有 agent 的记忆（memory 以 agent_id 为 key）
        if self._memory_svc is not None:
            for aid in self._agent_store.list_by_session(session_id):
                self._memory_svc.delete_agent(aid)
            # root agent 可能已在上方遍历到，but delete_agent 是幂等的
            if session.root_agent_id:
                self._memory_svc.delete_agent(session.root_agent_id)

        # 删除 blackboard 目录
        if self._blackboard_store is not None:
            self._blackboard_store.delete_session(session_id)

        # 删除 agent 文件
        if session.root_agent_id:
            self._agent_store.delete(session.root_agent_id)

        # 删除事件日志（SSE 历史回放）
        try:
            from app.runtime.event_store import get_event_store
            get_event_store().delete(session_id)
        except Exception:
            pass

        # 最后删除 session 文件
        self._session_svc.delete(session_id)

    def check_token_budget(self, session: Session) -> None:
        """Guard 硬检查：超出 token_budget 立即抛出 AppError。"""
        if session.token_used >= session.token_budget:
            raise AppError(
                "TOKEN_BUDGET_EXCEEDED",
                f"Session {session.id} token budget exhausted ({session.token_used}/{session.token_budget})",
            )
