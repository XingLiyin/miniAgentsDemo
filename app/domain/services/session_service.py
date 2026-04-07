"""Session 领域服务（Phase 1）。"""

from __future__ import annotations

from app.common.errors import AppError
from app.common.utils import new_session_id, now_iso
from app.domain.events.event_bus import EventBus
from app.domain.events.event_types import SESSION_CREATED, SESSION_STARTED, SESSION_SUCCEEDED, SESSION_FAILED, SESSION_CANCELED
from app.domain.models.session import Session
from app.domain.state_machine import SessionStateMachine
from app.storage.file.session_store import SessionStore


class SessionService:
    """Session CRUD + 状态机调用。"""

    def __init__(
        self,
        store: SessionStore,
        state_machine: SessionStateMachine,
        event_bus: EventBus,
    ) -> None:
        self._store = store
        self._sm = state_machine
        self._bus = event_bus

    def create(
        self,
        goal: str,
        template_id: str | None = None,
        token_budget: int = 200_000,
        root_max_turns: int = 20,
    ) -> Session:
        """创建新 Session，初始状态 QUEUED。"""
        now = now_iso()
        session = Session(
            id=new_session_id(),
            goal=goal,
            status="QUEUED",
            template_id=template_id,
            root_agent_id=None,
            token_budget=token_budget,
            root_max_turns=root_max_turns,
            created_at=now,
            updated_at=now,
        )
        self._store.save(session.to_dict())
        self._bus.publish(SESSION_CREATED, {"session_id": session.id, "goal": goal})
        return session

    def get(self, session_id: str) -> Session:
        data = self._store.get(session_id)
        if data is None:
            raise AppError("SESSION_NOT_FOUND", f"Session {session_id} not found")
        return Session.from_dict(data)

    def save(self, session: Session) -> None:
        session.updated_at = now_iso()
        self._store.save(session.to_dict())

    def transition(self, session_id: str, to_status: str) -> Session:
        """校验并执行状态转换，持久化后发布事件。"""
        session = self.get(session_id)
        self._sm.validate_session(session.status, to_status)
        session.status = to_status
        self.save(session)
        event_map = {
            "RUNNING": SESSION_STARTED,
            "SUCCEEDED": SESSION_SUCCEEDED,
            "FAILED": SESSION_FAILED,
            "CANCELED": SESSION_CANCELED,
        }
        if to_status in event_map:
            self._bus.publish(event_map[to_status], {"session_id": session_id})
        return session

    def set_root_agent(self, session_id: str, agent_id: str) -> None:
        session = self.get(session_id)
        session.root_agent_id = agent_id
        self.save(session)

    def add_tokens(self, session_id: str, tokens: int) -> Session:
        """累加 token 消耗，超出 budget 立即抛出 AppError（硬终止）。"""
        session = self.get(session_id)
        session.token_used += tokens
        self.save(session)
        if session.token_used >= session.token_budget:
            raise AppError("TOKEN_BUDGET_EXCEEDED", f"Session {session_id} token budget exhausted ({session.token_used}/{session.token_budget})")
        return session

    def list_ids(self) -> list[str]:
        return self._store.list_ids()

    def delete(self, session_id: str) -> None:
        self._store.delete(session_id)
