"""Session 生命周期编排器（Phase 1）。

职责：创建 Session → 创建 root Agent → 异步触发 AgentLoop。
Guard 检查也在此处（token_budget）。
"""

from __future__ import annotations

import asyncio
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

if TYPE_CHECKING:
    from app.runtime.agent_loop import AgentLoop

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._session_svc = session_svc
        self._template_svc = template_svc
        self._agent_store = agent_store
        self._bus = event_bus
        self._task_svc = task_svc
        self._memory_svc = memory_svc
        self._agent_loop: "AgentLoop | None" = None

    def set_agent_loop(self, loop: "AgentLoop") -> None:
        """注入 AgentLoop（避免循环导入）。"""
        self._agent_loop = loop

    def create_session(
        self,
        goal: str,
        template_id: str | None = None,
        token_budget: int | None = None,
        root_max_turns: int | None = None,
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
        if template_id:
            try:
                tpl = self._template_svc.get(template_id)
                tool_list = tpl.tool_list
                skill_list = tpl.skill_list
                soul_path = tpl.source_dir or None
            except AppError:
                logger.warning("Template %s not found, using defaults", template_id)
                tpl = None

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
            llm_name=settings.agent_default_llm_name,
            created_at=now,
            updated_at=now,
        )
        # 用四个 md 字段拼装 system_prompt（有模板时覆盖默认值）
        if template_id and tpl is not None:
            from app.runtime.agent_loop import _build_system_prompt
            agent.system_prompt = _build_system_prompt(tpl, agent)
        self._agent_store.save(agent.to_dict())
        self._session_svc.set_root_agent(session.id, agent.id)

        return session, agent.id

    def schedule_loop(self, session_id: str, agent_id: str) -> None:
        """从异步上下文调用：将 AgentLoop 作为 asyncio task 启动。"""
        if self._agent_loop is not None:
            asyncio.create_task(self._run_loop_async(session_id, agent_id))
        else:
            logger.warning("AgentLoop not set, session %s will not run automatically", session_id)

    async def _run_loop_async(self, session_id: str, agent_id: str) -> None:
        """在 asyncio task 中运行 AgentLoop。"""
        assert self._agent_loop is not None
        try:
            self._session_svc.transition(session_id, "RUNNING")
            await asyncio.get_event_loop().run_in_executor(
                None, self._agent_loop.run, session_id, agent_id
            )
        except AppError as e:
            logger.error("Session %s failed: %s %s", session_id, e.code, e.message)
            try:
                self._session_svc.transition(session_id, "FAILED")
            except Exception:
                pass
        except Exception:
            logger.exception("Session %s unexpected error", session_id)
            try:
                self._session_svc.transition(session_id, "FAILED")
            except Exception:
                pass

    def continue_session(self, session_id: str, user_message: str) -> Session:
        """Append a user message and re-start the agent loop if the session has ended."""
        from app.domain.services.memory_service import MemoryService
        from app.storage.file.memory_store import MemoryStore

        session = self._session_svc.get(session_id)

        if session.status == "CANCELED":
            raise AppError("SESSION_CANCELED", f"Session {session_id} is canceled and cannot be continued")

        # Append the user message so the agent picks it up on the next observe()
        mem_svc = MemoryService(store=MemoryStore())
        mem_svc.append_message(
            session_id=session_id,
            agent_id=session.root_agent_id or "user",
            role="user",
            content=user_message,
        )

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
                tool_list = tpl.tool_list
                skill_list = tpl.skill_list
                soul_path = tpl.source_dir or None
            except AppError:
                pass

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
            created_at=now,
            updated_at=now,
        )
        if session.template_id and tpl is not None:
            from app.runtime.agent_loop import _build_system_prompt
            agent.system_prompt = _build_system_prompt(tpl, agent)

        self._agent_store.save(agent.to_dict())
        self._session_svc.set_root_agent(session_id, agent.id)
        self._session_svc.transition(session_id, "QUEUED")
        self.schedule_loop(session_id, agent.id)

        return self._session_svc.get(session_id)

    def answer_input(self, session_id: str, task_id: str, content: str) -> Session:
        """Submit user answer for a WAITING_INPUT task and restart the loop."""
        session = self._session_svc.get(session_id)
        if session.status != "WAITING_INPUT":
            raise AppError(
                "INVALID_STATE",
                f"Session {session_id} is not waiting for input (status={session.status})",
            )

        if self._task_svc is None or self._memory_svc is None:
            raise AppError("INTERNAL_ERROR", "SessionManager missing task_svc or memory_svc")

        # Write user answer into the task and mark it finished
        self._task_svc.finish(task_id, result=content)

        # Append to memory so the loop sees it in next _observe()
        self._memory_svc.append_message(
            session_id=session_id,
            agent_id=session.root_agent_id or "user",
            role="user",
            content=content,
        )

        # Transition session → QUEUED and restart the loop
        self._session_svc.transition(session_id, "QUEUED")
        if session.root_agent_id:
            self.schedule_loop(session_id, session.root_agent_id)

        return self._session_svc.get(session_id)

    def cancel_session(self, session_id: str) -> Session:
        """取消 Session。"""
        return self._session_svc.transition(session_id, "CANCELED")

    def check_token_budget(self, session: Session) -> None:
        """Guard 硬检查：超出 token_budget 立即抛出 AppError。"""
        if session.token_used >= session.token_budget:
            raise AppError(
                "TOKEN_BUDGET_EXCEEDED",
                f"Session {session.id} token budget exhausted ({session.token_used}/{session.token_budget})",
            )
