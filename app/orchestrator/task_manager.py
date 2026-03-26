"""Task 入队/出队编排器（Phase 1）。"""

from __future__ import annotations

import logging

from app.common.errors import AppError
from app.domain.models.task import Task
from app.domain.services.session_service import SessionService
from app.domain.services.task_service import TaskService

logger = logging.getLogger(__name__)


class TaskManager:
    """Task 状态驱动与 failure_counter 管理。"""

    def __init__(
        self,
        task_svc: TaskService,
        session_svc: SessionService,
    ) -> None:
        self._task_svc = task_svc
        self._session_svc = session_svc

    def activate(self, task_id: str) -> Task:
        """将 Task 从 PENDING 转为 ACTIVE。"""
        return self._task_svc.transition(task_id, "ACTIVE")

    def complete(self, task_id: str, result: str | None = None, outputs: dict | None = None) -> Task:
        """完成 Task，并清零 session.failure_counter。"""
        task = self._task_svc.finish(task_id, result=result, outputs=outputs)
        self._reset_failure_counter(task.session_id)
        return task

    def fail_task(self, task_id: str, error: str) -> Task:
        """失败 Task，并累加 session.failure_counter。"""
        task = self._task_svc.fail(task_id, error)
        self._increment_failure_counter(task.session_id, task)
        return task

    def _reset_failure_counter(self, session_id: str) -> None:
        session = self._session_svc.get(session_id)
        if session.failure_counter > 0:
            session.failure_counter = 0
            self._session_svc.save(session)

    def _increment_failure_counter(self, session_id: str, task: Task) -> None:
        session = self._session_svc.get(session_id)
        session.failure_counter += 1
        self._session_svc.save(session)
        if session.failure_counter >= session.failure_threshold:
            logger.warning(
                "Session %s failure_counter=%d reached threshold=%d (task=%s). "
                "Phase 2: HITL pause would trigger here.",
                session_id, session.failure_counter, session.failure_threshold, task.id,
            )
