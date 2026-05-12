"""Session 领域服务（Phase 1）。"""

from __future__ import annotations

from app.common.errors import AppError
from app.common.utils import new_session_id, now_iso
from app.domain.events.event_bus import EventBus
from app.domain.events.event_types import SESSION_CREATED, SESSION_STARTED, SESSION_SUCCEEDED, SESSION_FAILED, SESSION_CANCELED
from app.domain.models.session import Session
from app.domain.services.task_service import TaskService
from app.domain.state_machine import SessionStateMachine
from app.orchestrator.task_queue import TaskQueue
from app.storage.file.session_store import SessionStore


class SessionService:
    """Session CRUD + 状态机调用。"""

    def __init__(
        self,
        store: SessionStore,
        state_machine: SessionStateMachine,
        event_bus: EventBus,
        task_svc: TaskService,
    ) -> None:
        self._store = store
        self._sm = state_machine
        self._bus = event_bus
        self._task_svc = task_svc

    def create(
        self,
        user_prompt: str,
        template_id: str | None = None,
        token_budget: int = 200_000,
        llm_provider: str = "",
        llm_model: str = "",
        working_dir: str = "",
    ) -> Session:
        """创建新 Session，初始状态 QUEUED。"""
        now = now_iso()
        session = Session(
            id=new_session_id(),
            user_prompt=user_prompt,
            goal=user_prompt,
            status="QUEUED",
            template_id=template_id,
            root_agent_id=None,
            token_budget=token_budget,
            llm_provider=llm_provider,
            llm_model=llm_model,
            working_dir=working_dir,
            created_at=now,
            updated_at=now,
        )
        session.task_queue = TaskQueue(task_svc=self._task_svc, session_id=session.id)
        self._store.save(session.to_dict())
        self._bus.publish(SESSION_CREATED, {"session_id": session.id, "user_prompt": user_prompt})
        return session

    def get(self, session_id: str) -> Session:
        data = self._store.get(session_id)
        if data is None:
            raise AppError("SESSION_NOT_FOUND", f"Session {session_id} not found")
        session = Session.from_dict(data)
        raw_queue = data.get("task_queue")
        if raw_queue is not None:
            session.task_queue = TaskQueue.from_dict(raw_queue, self._task_svc, session_id)
        else:
            session.task_queue = TaskQueue(task_svc=self._task_svc, session_id=session_id)
        return session

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
        # Push SSE event
        try:
            from app.common.sse_bus import get_sse_bus
            sse_event: dict = {
                "type": "session_update",
                "session_id": session_id,
                "status": to_status,
                "input_tokens_used": session.input_tokens_used,
                "output_tokens_used": session.output_tokens_used,
            }
            get_sse_bus().push(session_id, sse_event)
            if to_status in ("SUCCEEDED", "FAILED", "CANCELED"):
                get_sse_bus().push(session_id, {"type": "done", "final_status": to_status})
        except Exception:
            pass
        return session

    def set_root_agent(self, session_id: str, agent_id: str) -> None:
        session = self.get(session_id)
        session.root_agent_id = agent_id
        self.save(session)

    def add_tokens(self, session_id: str, input_tokens: int = 0, output_tokens: int = 0, context_tokens: int = 0) -> Session:
        """累加 token 消耗，输出超出 budget 立即抛出 AppError（硬终止）。"""
        session = self.get(session_id)
        session.input_tokens_used += input_tokens
        session.output_tokens_used += output_tokens
        self.save(session)
        try:
            from app.common.sse_bus import get_sse_bus
            get_sse_bus().push(session_id, {
                "type": "token_update",
                "session_id": session_id,
                "input_tokens_used": session.input_tokens_used,
                "output_tokens_used": session.output_tokens_used,
                "context_tokens": context_tokens,
            })
        except Exception:
            pass
        if session.token_budget > 0 and session.output_tokens_used >= session.token_budget:
            raise AppError(
                "TOKEN_BUDGET_EXCEEDED",
                f"Session {session_id} output token budget exhausted "
                f"({session.output_tokens_used}/{session.token_budget})",
            )
        return session

    def list_ids(self) -> list[str]:
        return self._store.list_ids()

    def is_working_dir_in_use(self, working_dir: str, exclude_session_id: str) -> bool:
        """检查除 exclude_session_id 外是否还有其他 session 使用同一 working_dir。"""
        for sid in self._store.list_ids():
            if sid == exclude_session_id:
                continue
            data = self._store.get(sid)
            if data and data.get("working_dir") == working_dir:
                return True
        return False

    def delete(self, session_id: str) -> None:
        self._store.delete(session_id)
