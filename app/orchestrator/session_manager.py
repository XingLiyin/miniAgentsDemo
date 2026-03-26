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
from app.domain.services.session_service import SessionService
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
    ) -> None:
        self._session_svc = session_svc
        self._template_svc = template_svc
        self._agent_store = agent_store
        self._bus = event_bus
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
        system_prompt = settings.agent_default_system_prompt
        tool_list: list[str] = []
        if template_id:
            try:
                tpl = self._template_svc.get(template_id)
                system_prompt = tpl.system_prompt
                tool_list = tpl.tool_list
            except AppError:
                logger.warning("Template %s not found, using defaults", template_id)

        now = now_iso()
        agent = Agent(
            id=new_agent_id(),
            session_id=session.id,
            template_id=template_id,
            name="root",
            status="IDLE",
            system_prompt=system_prompt,
            tool_list=tool_list,
            loop_guard=LoopGuard(turns_used=0, max_turns=session.root_max_turns),
            llm_name=settings.agent_default_llm_name,
            created_at=now,
            updated_at=now,
        )
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
