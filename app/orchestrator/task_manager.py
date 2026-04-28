"""Task Manager：任务调度与 session 终结决策。

职责：
- 订阅 TASK_CREATED / TASK_EXECUTION_FINISHED / TASK_EXECUTION_FAILED
- 显式管理 TaskQueue：task 创建、父任务恢复、失败重试 三个 push 点
- 决策：从队列取下一个就绪 task、判断 session 是否结束、重试
- 读取 task 属性（use_subagent / subagent_template / inherit_memory），决定执行方式
- 调用 LifecycleManager 操作执行 agent 侧后果
- 管理 session.failure_counter

不做：agent 注册/回收的具体操作、线程管理
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from app.domain.events.event_types import TASK_CREATED, TASK_EXECUTION_FAILED, TASK_EXECUTION_FINISHED
from app.domain.models.task import Task
from app.domain.services.session_service import SessionService
from app.domain.services.task_service import TaskService
from app.orchestrator.task_queue import TaskQueue

if TYPE_CHECKING:
    from app.domain.events.event_bus import EventBus
    from app.orchestrator.lifecycle_manager import LifecycleManager

logger = logging.getLogger(__name__)


class TaskManager:
    """Task scheduling decisions and session lifecycle."""

    def __init__(
        self,
        task_svc: TaskService,
        session_svc: SessionService,
        task_queue: TaskQueue,
        lifecycle_manager: "LifecycleManager | None" = None,
        event_bus: "EventBus | None" = None,
        max_task_retries: int = 3,
    ) -> None:
        self._task_svc = task_svc
        self._session_svc = session_svc
        self._task_queue = task_queue
        self._lm = lifecycle_manager
        self._max_task_retries = max_task_retries
        self._session_locks: dict[str, threading.Lock] = {}

        if event_bus is not None:
            event_bus.subscribe(TASK_CREATED, self.on_task_created)
            event_bus.subscribe(TASK_EXECUTION_FINISHED, self.on_task_finished)
            event_bus.subscribe(TASK_EXECUTION_FAILED, self.on_task_failed)

    # ── Session lifecycle ─────────────────────────────────────────────────────

    def init_session(self, session_id: str) -> None:
        """Initialize per-session queue. Must be called before any task is created."""
        self._task_queue.init_session(session_id)

    def cleanup_session(self, session_id: str) -> None:
        self._task_queue.cleanup_session(session_id)
        self._session_locks.pop(session_id, None)

    # ── Session start ─────────────────────────────────────────────────────────

    def start_session(self, session_id: str, agent_id: str) -> None:
        """Entry point called by SessionManager when a session is ready to run."""
        if self._lm is None:
            logger.error("TM: LifecycleManager not set, cannot start session %s", session_id)
            return

        try:
            session = self._session_svc.get(session_id)
            if session.status == "QUEUED":
                self._session_svc.transition(session_id, "RUNNING")
        except Exception:
            logger.exception("TM: failed to activate session %s", session_id)
            return

        next_task = self._task_queue.pop(session_id)
        if not next_task:
            logger.error("TM: no ready task for session %s", session_id)
            return

        self._dispatch_next(session_id, agent_id, next_task)

    def spawn_daemon_task(self, session_id: str, parent_agent_id: str, task_id: str) -> None:
        """Pre-activate a daemon task (keeps it out of the queue) and spawn its agent."""
        if self._lm is None:
            logger.error("TM: LifecycleManager not set, cannot spawn daemon task %s", task_id)
            return
        try:
            self._task_svc.transition(task_id, "ACTIVE")
        except Exception:
            logger.warning("TM: could not pre-activate daemon task %s", task_id)
        try:
            task = self._task_svc.get(task_id)
        except Exception:
            logger.exception("TM: cannot load daemon task %s", task_id)
            return
        self._lm.spawn_daemon_agent(
            session_id=session_id,
            parent_agent_id=parent_agent_id,
            task_id=task_id,
            template_name=str(task.settings.get("subagent_template", "")),
            inherit_memory=bool(task.settings.get("inherit_memory", False)),
        )

    # ── Event handlers ────────────────────────────────────────────────────────

    def on_task_created(self, event_type: str, payload: dict) -> None:
        """Push newly created task onto the stack."""
        task_id = payload.get("task_id", "")
        session_id = payload.get("session_id", "")
        if task_id and session_id:
            self._task_queue.push(session_id, task_id)

    def on_task_finished(self, event_type: str, payload: dict) -> None:
        session_id = payload.get("session_id", "")
        agent_id = payload.get("agent_id", "")
        finished_task_id = payload.get("task_id", "")

        self.record_success(session_id)

        next_task: Task | None = None

        with self._get_lock(session_id):
            try:
                session = self._session_svc.get(session_id)
                if session.status not in ("RUNNING", "QUEUED"):
                    self._lm_recycle_finished(session_id, agent_id)
                    return
            except Exception:
                self._lm_recycle_finished(session_id, agent_id)
                return

            self._task_queue.notify_completed(session_id, finished_task_id)
            self._try_resume_parent(session_id, finished_task_id)

            next_task = self._task_queue.pop(session_id)
            if next_task is None:
                if self._task_queue.is_empty(session_id):
                    try:
                        self._session_svc.transition(session_id, "SUCCEEDED")
                    except Exception:
                        logger.exception("TM: failed to transition session %s to SUCCEEDED", session_id)
                else:
                    # _blocked has tasks waiting on deps; they will be promoted
                    # when further active tasks complete
                    logger.debug("TM: tasks blocked on deps for session %s", session_id)

        self._dispatch_next(session_id, agent_id, next_task)

    def on_task_failed(self, event_type: str, payload: dict) -> None:
        session_id = payload.get("session_id", "")
        agent_id = payload.get("agent_id", "")
        failed_task_id = payload.get("task_id", "")

        self.record_failure(session_id)

        next_task: Task | None = None

        with self._get_lock(session_id):
            if failed_task_id:
                try:
                    self._task_svc.fail(failed_task_id, error=payload.get("error", ""))
                except Exception:
                    logger.exception("TM: failed to mark task %s as FAILED", failed_task_id)
                self._cascade_fail(session_id, failed_task_id)

            try:
                failed_task = self._task_svc.get(failed_task_id) if failed_task_id else None
            except Exception:
                failed_task = None

            if failed_task and failed_task.retry_count < self._max_task_retries:
                logger.info(
                    "TM: retrying task %s (attempt %d/%d) for session %s",
                    failed_task_id,
                    failed_task.retry_count + 1,
                    self._max_task_retries,
                    session_id,
                )
                try:
                    self._task_svc.retry(failed_task_id)
                    self._task_queue.push(session_id, failed_task_id)
                except Exception:
                    logger.exception("TM: failed to retry task %s", failed_task_id)
                    self._fail_session(session_id)

            self._task_queue.notify_completed(session_id, failed_task_id)
            next_task = self._task_queue.pop(session_id)
            if next_task is None and self._task_queue.is_empty(session_id):
                self._fail_session(session_id)

        self._dispatch_next(session_id, agent_id, next_task)

    # ── failure_counter ───────────────────────────────────────────────────────

    def record_success(self, session_id: str) -> None:
        self._reset_failure_counter(session_id)

    def record_failure(self, session_id: str) -> None:
        try:
            session = self._session_svc.get(session_id)
            session.failure_counter += 1
            self._session_svc.save(session)
            if session.failure_counter >= session.failure_threshold:
                logger.warning(
                    "Session %s failure_counter=%d reached threshold=%d.",
                    session_id,
                    session.failure_counter,
                    session.failure_threshold,
                )
        except Exception:
            pass

    # ── Queue interface ───────────────────────────────────────────────────────

    def next_task(self, session_id: str) -> Task | None:
        return self._task_queue.pop(session_id)

    def activate(self, task_id: str) -> Task:
        return self._task_svc.transition(task_id, "ACTIVE")

    def complete(self, task_id: str, result: str | None = None, outputs: dict | None = None) -> Task:
        task = self._task_svc.finish(task_id, result=result, outputs=outputs)
        self._reset_failure_counter(task.session_id)
        return task

    def fail_task(self, task_id: str, error: str) -> Task:
        task = self._task_svc.fail(task_id, error)
        self.record_failure(task.session_id)
        return task

    # ── Private helpers ───────────────────────────────────────────────────────

    def _dispatch_next(
        self, session_id: str, finished_agent_id: str, next_task: Task | None
    ) -> None:
        """Activate task, ask LM for an assembled agent, start execution."""
        if self._lm is None:
            return

        if next_task is None:
            self._lm.release(session_id, finished_agent_id)
            return

        try:
            self._task_svc.transition(next_task.id, "ACTIVE")
        except Exception:
            logger.exception("TM: failed to activate task %s", next_task.id)

        agent_id = self._lm.prepare_executor(
            session_id=session_id,
            finished_agent_id=finished_agent_id,
            task_id=next_task.id,
            use_subagent=bool(next_task.settings.get("use_subagent")),
            template_name=str(next_task.settings.get("subagent_template", "")),
            inherit_memory=bool(next_task.settings.get("inherit_memory", True)),
        )
        if agent_id:
            self._lm.run_agent(session_id, agent_id, next_task.id)

    def _lm_recycle_finished(self, session_id: str, finished_agent_id: str) -> None:
        if self._lm is not None:
            self._lm.release(session_id, finished_agent_id)

    def _try_resume_parent(self, session_id: str, finished_task_id: str) -> None:
        """Conclude a SUSPENDED parent once all its children are terminal.

        Any child FAILED → fail parent (cascade).
        All children succeeded → resume parent and push to queue.
        """
        if not finished_task_id:
            return
        try:
            finished_task = self._task_svc.get(finished_task_id)
            if not finished_task.parent_task_id:
                return
            parent = self._task_svc.get(finished_task.parent_task_id)
            if parent.status != "SUSPENDED":
                return
            children = self._task_svc.list_children(finished_task.parent_task_id, session_id)
            _terminal = {"FINISHED", "FAILED", "CANCELED"}
            if not children or not all(t.status in _terminal for t in children):
                return
            if any(t.status == "FAILED" for t in children):
                self._task_svc.fail(parent.id, error="cascade: child task failed")
                self._task_queue.remove(session_id, parent.id)
                self._cascade_fail(session_id, parent.id)
            else:
                self._task_svc.resume(parent.id)
                self._task_queue.push(session_id, parent.id)
        except Exception:
            logger.exception("TM: failed to conclude parent for task %s", finished_task_id)

    def _cascade_fail(self, session_id: str, failed_task_id: str) -> None:
        """Cascade failure to all PENDING tasks whose dag_deps include failed_task_id."""
        try:
            all_tasks = self._task_svc.list_by_session(session_id)
        except Exception:
            logger.exception("TM: _cascade_fail: cannot list tasks for session %s", session_id)
            return
        for task in all_tasks:
            if task.status != "PENDING" or failed_task_id not in task.dag_deps:
                continue
            try:
                self._task_svc.fail(task.id, error=f"cascade: dependency {failed_task_id} failed")
                self._task_queue.remove(session_id, task.id)
                self._cascade_fail(session_id, task.id)
            except Exception:
                logger.exception("TM: _cascade_fail: failed to fail task %s", task.id)

    def _fail_session(self, session_id: str) -> None:
        try:
            self._session_svc.transition(session_id, "FAILED")
        except Exception:
            logger.exception("TM: failed to transition session %s to FAILED", session_id)

    def _get_lock(self, session_id: str) -> threading.Lock:
        return self._session_locks.setdefault(session_id, threading.Lock())

    def _reset_failure_counter(self, session_id: str) -> None:
        try:
            session = self._session_svc.get(session_id)
            if session.failure_counter > 0:
                session.failure_counter = 0
                self._session_svc.save(session)
        except Exception:
            pass
