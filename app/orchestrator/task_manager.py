"""Task 入队/出队编排器。"""

from __future__ import annotations

import logging

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

    # ── 队列接口 ───────────────────────────────────────────────────────────────

    def next_task(self, session_id: str) -> Task | None:
        """返回该 session 下第一个 PENDING task（按 created_at 升序）。"""
        tasks = self._task_svc.list_pending(session_id)
        return tasks[0] if tasks else None

    def create_replan(self, session_id: str, agent_id: str) -> Task:
        """所有 atomic task 完成后，创建 re-plan task 重新评估目标。

        use_subagent=True：由 plan sub-agent 执行规划，root agent 不直接运行。
        """
        task = self._task_svc.create_plan_task(
            session_id=session_id,
            creator_agent_id=agent_id,
            title="Re-plan: evaluate next steps",
            description=(
                "All current tasks completed. Re-evaluate the goal and plan "
                "next steps if needed."
            ),
        )
        logger.info("Session %s: created re-plan task %s", session_id, task.id)
        return task

    # ── failure_counter ────────────────────────────────────────────────────────

    def record_success(self, session_id: str) -> None:
        """task 执行成功，清零 failure_counter。"""
        self._reset_failure_counter(session_id)

    def record_failure(self, session_id: str) -> None:
        """task 执行失败，累加 failure_counter 并在达到阈值时告警。"""
        session = self._session_svc.get(session_id)
        session.failure_counter += 1
        self._session_svc.save(session)
        if session.failure_counter >= session.failure_threshold:
            logger.warning(
                "Session %s failure_counter=%d reached threshold=%d.",
                session_id, session.failure_counter, session.failure_threshold,
            )

    # ── 状态操作（供外部调用）──────────────────────────────────────────────────

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
        self.record_failure(task.session_id)
        return task

    # ── 私有 ───────────────────────────────────────────────────────────────────

    def _reset_failure_counter(self, session_id: str) -> None:
        session = self._session_svc.get(session_id)
        if session.failure_counter > 0:
            session.failure_counter = 0
            self._session_svc.save(session)
