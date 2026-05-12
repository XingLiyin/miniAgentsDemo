"""TaskQueue：per-session 有状态任务栈。

结构：
- _ready:   list[str]  栈（LIFO）— deps 已满足、可立即执行的 task_id
- _blocked: set[str]   集合      — dag_deps 未满足、等待中的 task_id

流程：
  push     → 检查 dag_deps，满足入栈，不满足入 _blocked
  pop      → 弹出栈顶（LIFO），由 TM 负责激活
  notify_completed → 将 _blocked 中 deps 现已满足的 task 移入栈
  is_empty → _ready 和 _blocked 均空，session 可结束
  has_work → _ready 或 _blocked 非空，还有任务

不做：task 状态转换、session 状态管理
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from app.domain.services.task_service import TaskService

if TYPE_CHECKING:
    from app.domain.models.task import Task

logger = logging.getLogger(__name__)

_TERMINAL = {"FINISHED", "FAILED", "CANCELED"}


class TaskQueue:
    """Per-session LIFO task stack with a blocked set for dag_deps."""

    def __init__(self, task_svc: TaskService, session_id: str) -> None:
        self._task_svc = task_svc
        self._session_id = session_id
        self._ready: list[str] = []
        self._blocked: set[str] = set()
        self._lock = threading.Lock()

    # ── Stack operations ──────────────────────────────────────────────────────

    def push(self, task_id: str) -> None:
        """Push a task onto the stack (or into blocked if deps unmet). Idempotent."""
        with self._lock:
            if task_id in self._ready or task_id in self._blocked:
                return
            if self._deps_satisfied(task_id):
                self._ready.append(task_id)
                logger.debug("TaskQueue: pushed %s to ready (session %s)", task_id, self._session_id)
            else:
                self._blocked.add(task_id)
                logger.debug("TaskQueue: pushed %s to blocked (session %s)", task_id, self._session_id)

    def pop(self) -> "Task | None":
        """Pop the top task from the ready stack. Returns None if stack is empty."""
        with self._lock:
            while self._ready:
                task_id = self._ready.pop()
                try:
                    task = self._task_svc.get(task_id)
                except Exception:
                    logger.warning("TaskQueue.pop: cannot load task %s, skipping", task_id)
                    continue
                if task.status != "PENDING":
                    logger.debug("TaskQueue.pop: task %s is %s, skipping", task_id, task.status)
                    continue
                return task
            return None

    def notify_completed(self, completed_task_id: str) -> None:
        """A task finished — promote any newly unblocked tasks from blocked to ready."""
        with self._lock:
            if not self._blocked:
                return
            terminal_ids = self._terminal_ids()
            promoted = {
                tid for tid in self._blocked
                if self._deps_satisfied_with(tid, terminal_ids)
            }
            for tid in promoted:
                self._blocked.discard(tid)
                self._ready.append(tid)
                logger.debug("TaskQueue: promoted %s from blocked to ready (session %s)", tid, self._session_id)

    def remove(self, task_id: str) -> None:
        """Remove a task from ready stack or blocked set (used on cascade failure)."""
        with self._lock:
            self._blocked.discard(task_id)
            try:
                self._ready.remove(task_id)
            except ValueError:
                pass

    def clear(self) -> None:
        """Discard all ready and blocked entries (used when failing session)."""
        with self._lock:
            self._ready.clear()
            self._blocked.clear()

    def is_empty(self) -> bool:
        """True when both ready stack and blocked set are empty."""
        with self._lock:
            return not self._ready and not self._blocked

    def has_work(self) -> bool:
        """True when any task is ready or blocked (session is not done yet)."""
        return not self.is_empty()

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "ready": list(self._ready),
                "blocked": list(self._blocked),
            }

    @classmethod
    def from_dict(cls, data: dict, task_svc: TaskService, session_id: str) -> "TaskQueue":
        q = cls(task_svc=task_svc, session_id=session_id)
        q._ready = data.get("ready", [])
        q._blocked = set(data.get("blocked", []))
        return q

    # ── Private ───────────────────────────────────────────────────────────────

    def _deps_satisfied(self, task_id: str) -> bool:
        terminal_ids = self._terminal_ids()
        return self._deps_satisfied_with(task_id, terminal_ids)

    def _deps_satisfied_with(self, task_id: str, terminal_ids: set[str]) -> bool:
        try:
            task = self._task_svc.get(task_id)
            return all(dep in terminal_ids for dep in task.dag_deps)
        except Exception:
            return False

    def _terminal_ids(self) -> set[str]:
        try:
            return {
                t.id for t in self._task_svc.list_by_session(self._session_id)
                if t.status in _TERMINAL
            }
        except Exception:
            return set()
