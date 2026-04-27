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


class _SessionState:
    __slots__ = ("ready", "blocked", "lock")

    def __init__(self) -> None:
        self.ready: list[str] = []      # stack, append/pop from end
        self.blocked: set[str] = set()
        self.lock = threading.Lock()


class TaskQueue:
    """Per-session LIFO task stack with a blocked set for dag_deps."""

    def __init__(self, task_svc: TaskService) -> None:
        self._task_svc = task_svc
        self._sessions: dict[str, _SessionState] = {}

    # ── Session lifecycle ─────────────────────────────────────────────────────

    def init_session(self, session_id: str) -> None:
        self._sessions[session_id] = _SessionState()

    def cleanup_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    # ── Stack operations ──────────────────────────────────────────────────────

    def push(self, session_id: str, task_id: str) -> None:
        """Push a task onto the stack (or into blocked if deps unmet). Idempotent."""
        st = self._sessions.get(session_id)
        if st is None:
            logger.warning("TaskQueue.push: no state for session %s", session_id)
            return
        with st.lock:
            if task_id in st.ready or task_id in st.blocked:
                return
            if self._deps_satisfied(session_id, task_id):
                st.ready.append(task_id)
                logger.debug("TaskQueue: pushed %s to ready (session %s)", task_id, session_id)
            else:
                st.blocked.add(task_id)
                logger.debug("TaskQueue: pushed %s to blocked (session %s)", task_id, session_id)

    def pop(self, session_id: str) -> "Task | None":
        """Pop the top task from the ready stack. Returns None if stack is empty."""
        st = self._sessions.get(session_id)
        if st is None:
            return None
        with st.lock:
            while st.ready:
                task_id = st.ready.pop()
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

    def notify_completed(self, session_id: str, completed_task_id: str) -> None:
        """A task finished — promote any newly unblocked tasks from blocked to ready."""
        st = self._sessions.get(session_id)
        if st is None:
            return
        with st.lock:
            if not st.blocked:
                return
            terminal_ids = self._terminal_ids(session_id)
            promoted = {
                tid for tid in st.blocked
                if self._deps_satisfied_with(session_id, tid, terminal_ids)
            }
            for tid in promoted:
                st.blocked.discard(tid)
                st.ready.append(tid)
                logger.debug("TaskQueue: promoted %s from blocked to ready (session %s)", tid, session_id)

    def is_empty(self, session_id: str) -> bool:
        """True when both ready stack and blocked set are empty."""
        st = self._sessions.get(session_id)
        if st is None:
            return True
        with st.lock:
            return not st.ready and not st.blocked

    def has_work(self, session_id: str) -> bool:
        """True when any task is ready or blocked (session is not done yet)."""
        return not self.is_empty(session_id)

    # ── Private ───────────────────────────────────────────────────────────────

    def _deps_satisfied(self, session_id: str, task_id: str) -> bool:
        terminal_ids = self._terminal_ids(session_id)
        return self._deps_satisfied_with(session_id, task_id, terminal_ids)

    def _deps_satisfied_with(
        self, session_id: str, task_id: str, terminal_ids: set[str]
    ) -> bool:
        try:
            task = self._task_svc.get(task_id)
            return all(dep in terminal_ids for dep in task.dag_deps)
        except Exception:
            return False

    def _terminal_ids(self, session_id: str) -> set[str]:
        try:
            return {
                t.id for t in self._task_svc.list_by_session(session_id)
                if t.status in _TERMINAL
            }
        except Exception:
            return set()
