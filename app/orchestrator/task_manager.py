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

from app.common.utils import extract_text
from app.domain.events.event_types import TASK_CREATED, TASK_EXECUTION_FAILED, TASK_EXECUTION_FINISHED
from app.domain.models.task import Task
from app.domain.services.memory_service import MemoryService
from app.domain.services.session_service import SessionService
from app.domain.services.task_service import TaskService
from app.orchestrator.task_queue import TaskQueue  # noqa: F401 – kept for external callers

if TYPE_CHECKING:
    from app.domain.events.event_bus import EventBus
    from app.orchestrator.lifecycle_manager import LifecycleManager
    from app.storage.file.agent_store import AgentStore

logger = logging.getLogger(__name__)


def _task_result_content(prefix: str, outputs: "str | list", process_report: "str | None", error: "str | None") -> "str | list":
    """Build memory content for a finished/failed task, preserving images when outputs is multimodal."""
    if isinstance(outputs, list):
        images = [p for p in outputs if p.get("type") == "image"]
        output_text = next((p.get("text", "") for p in outputs if p.get("type") == "text"), "")
    else:
        images = []
        output_text = outputs or ""
    text_parts = [prefix]
    if output_text:
        text_parts.append(f"# Output\n\n{output_text}")
    if process_report:
        text_parts.append(f"# Process Report\n\n{process_report}")
    if error:
        text_parts.append(f"Error: {error}")
    text = "\n".join(text_parts)
    if images:
        return [*images, {"type": "text", "text": text}]
    return text


class TaskManager:
    """Task scheduling decisions and session lifecycle."""

    def __init__(
        self,
        task_svc: TaskService,
        session_svc: SessionService,
        lifecycle_manager: "LifecycleManager | None" = None,
        event_bus: "EventBus | None" = None,
        max_task_retries: int = 3,
        memory_svc: MemoryService | None = None,
        agent_store: "AgentStore | None" = None,
    ) -> None:
        self._task_svc = task_svc
        self._session_svc = session_svc
        self._lm = lifecycle_manager
        self._max_task_retries = max_task_retries
        self._memory_svc = memory_svc
        self._agent_store = agent_store
        self._session_locks: dict[str, threading.Lock] = {}
        self._pending: dict[str, list[str]] = {}
        self._pending_lock = threading.Lock()

        if event_bus is not None:
            event_bus.subscribe(TASK_CREATED, self.on_task_created)
            event_bus.subscribe(TASK_EXECUTION_FINISHED, self.on_task_finished)
            event_bus.subscribe(TASK_EXECUTION_FAILED, self.on_task_failed)

    # ── Session lifecycle ─────────────────────────────────────────────────────

    def init_session(self, session_id: str) -> None:
        pass

    def cleanup_session(self, session_id: str) -> None:
        self._session_locks.pop(session_id, None)
        with self._pending_lock:
            self._pending.pop(session_id, None)

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

        with self._get_lock(session_id):
            next_task = self._q_pop(session_id)
        if not next_task:
            logger.error("TM: no ready task for session %s", session_id)
            return

        self._dispatch_next(session_id, agent_id, next_task)

    def spawn_metadata_filler(
        self,
        session_id: str,
        creator_agent_id: str,
        user_prompt: str | list,
        target_task_id: str,
    ) -> None:
        """创建 metadata_filler daemon task 并旁路启动，用于异步补全 task 标题/描述和 session goal。"""
        if self._task_svc is None:
            return
        try:
            _session = self._session_svc.get(session_id)
            _existing_goal = _session.goal if _session.goal != _session.user_prompt else ""
        except Exception:
            _existing_goal = ""

        if _existing_goal:
            description = (
                f"根据用户指令（{extract_text(user_prompt)}）和完整对话上下文，生成任务标题和描述。"
                f"当前 session goal 为「{_existing_goal}」。"
            )
        else:
            description = (
                f"根据用户指令（{extract_text(user_prompt)}）和完整对话上下文，"
                f"生成任务标题和描述，并判断 session goal。"
            )

        meta_task = self._task_svc.create(
            session_id=session_id,
            creator_agent_id=creator_agent_id,
            user_prompt=user_prompt,
            title="Update task meta data details",
            description=description,
            inputs={
                "subagent_template": "metadata_filler",
                "target_task_id": target_task_id,
                "_daemon": True,
                "inherit_memory": True,
            },
        )
        self.spawn_daemon_task(session_id, creator_agent_id, meta_task.id)

    def spawn_daemon_task(self, session_id: str, parent_agent_id: str, task_id: str) -> None:
        """Pre-activate a daemon task (keeps it out of the queue) and spawn its agent."""
        if self._lm is None:
            logger.error("TM: LifecycleManager not set, cannot spawn daemon task %s", task_id)
            return
        try:
            self._task_svc.transition(task_id, "ACTIVE", session_id)
        except Exception:
            logger.warning("TM: could not pre-activate daemon task %s", task_id)
        try:
            task = self._task_svc.get(task_id, session_id)
        except Exception:
            logger.exception("TM: cannot load daemon task %s", task_id)
            return
        template_name = str(task.settings.get("subagent_template", ""))
        self._lm.spawn_daemon_agent(
            session_id=session_id,
            parent_agent_id=parent_agent_id,
            task_id=task_id,
            template_name=template_name,
            inherit_memory=bool(task.settings.get("inherit_memory", False)),
        )

    # ── Event handlers ────────────────────────────────────────────────────────

    def on_task_created(self, event_type: str, payload: dict) -> None:
        """Buffer newly created task; the actual (reversed) push happens at the next _q_pop.

        Same-batch tasks created within one agent turn are pushed together in reverse
        order so they pop in submission order (FIFO within a batch, LIFO across batches).
        """
        task_id = payload.get("task_id", "")
        session_id = payload.get("session_id", "")
        if task_id and session_id:
            with self._pending_lock:
                self._pending.setdefault(session_id, []).append(task_id)

    def on_task_finished(self, event_type: str, payload: dict) -> None:
        session_id = payload.get("session_id", "")
        agent_id = payload.get("agent_id", "")
        finished_task_id = payload.get("task_id", "")

        self.record_success(session_id)

        next_task: Task | None = None
        _session_done = False

        with self._get_lock(session_id):
            try:
                session = self._session_svc.get(session_id)
                if session.status not in ("RUNNING", "QUEUED"):
                    self._lm_recycle_finished(session_id, agent_id)
                    return
                if finished_task_id and finished_task_id in session.active_tasks:
                    session.active_tasks.remove(finished_task_id)
                    self._session_svc.save(session)
            except Exception:
                self._lm_recycle_finished(session_id, agent_id)
                return

            # active outcome: observer set task to PENDING but did not re-queue it;
            # push it back so the session doesn't terminate prematurely.
            if finished_task_id:
                try:
                    _ft = self._task_svc.get(finished_task_id, session_id)
                    if _ft.status == "PENDING":
                        self._q_push(session_id, finished_task_id)
                        logger.debug(
                            "TM: re-queued active task %s for session %s", finished_task_id, session_id
                        )
                    elif _ft.status == "FINISHED":
                        self._notify_trackers(session_id, _ft)
                except Exception:
                    logger.exception("TM: failed to check active task %s", finished_task_id)

            self._q_notify(session_id, finished_task_id)
            self._try_resume_parent(session_id, finished_task_id)

            next_task = self._q_pop(session_id)
            if next_task is None:
                if self._q_is_empty(session_id):
                    running = self._lm.running_agent_count(session_id) if self._lm else 0
                    if running > 0:
                        logger.debug(
                            "TM: queue empty but %d agent(s) still running for session %s, deferring SUCCEEDED",
                            running, session_id,
                        )
                    else:
                        # Reconcile against the store before declaring success: the queue
                        # is in-memory and can lose a task (e.g. pop() dropping it on a
                        # transient load failure). Re-queue any orphaned PENDING task so we
                        # never SUCCEED while real work remains.
                        orphans = self._orphan_pending_ids(session_id)
                        if orphans:
                            for tid in orphans:
                                self._q_push(session_id, tid)
                            logger.warning(
                                "TM: recovered %d orphan PENDING task(s) for session %s, "
                                "deferring SUCCEEDED: %s", len(orphans), session_id, orphans,
                            )
                            next_task = self._q_pop(session_id)
                        else:
                            try:
                                self._session_svc.transition(session_id, "SUCCEEDED")
                            except Exception:
                                logger.exception("TM: failed to transition session %s to SUCCEEDED", session_id)
                            _session_done = True
                else:
                    # _blocked has tasks waiting on deps; they will be promoted
                    # when further active tasks complete
                    logger.debug("TM: tasks blocked on deps for session %s", session_id)

        if _session_done:
            self.cleanup_session(session_id)
        self._dispatch_next(session_id, agent_id, next_task)

    def on_task_failed(self, event_type: str, payload: dict) -> None:
        session_id = payload.get("session_id", "")
        agent_id = payload.get("agent_id", "")
        failed_task_id = payload.get("task_id", "")

        self.record_failure(session_id)

        next_task: Task | None = None
        _session_done = False

        with self._get_lock(session_id):
            if failed_task_id:
                try:
                    session = self._session_svc.get(session_id)
                    if failed_task_id in session.active_tasks:
                        session.active_tasks.remove(failed_task_id)
                        self._session_svc.save(session)
                except Exception:
                    logger.exception("TM: failed to remove active_tasks for task %s", failed_task_id)
            if failed_task_id:
                try:
                    current_task = self._task_svc.get(failed_task_id, session_id)
                    if current_task.status != "FAILED":
                        self._task_svc.fail(failed_task_id, process_report=payload.get("process_report", ""), error=payload.get("error", ""), session_id=session_id)
                    else:
                        logger.debug("TM: task %s already FAILED (set by observer), skipping transition", failed_task_id)
                except Exception:
                    logger.exception("TM: failed to mark task %s as FAILED", failed_task_id)
                self._cascade_fail(session_id, failed_task_id)

            try:
                failed_task = self._task_svc.get(failed_task_id, session_id) if failed_task_id else None
            except Exception:
                failed_task = None

            can_retry = failed_task is not None and failed_task.retry_count < self._max_task_retries

            if can_retry:
                logger.info(
                    "TM: retrying task %s (attempt %d/%d) for session %s",
                    failed_task_id,
                    failed_task.retry_count + 1,
                    self._max_task_retries,
                    session_id,
                )
                try:
                    self._task_svc.retry(failed_task_id, session_id)
                    self._q_push(session_id, failed_task_id)
                except Exception:
                    logger.exception("TM: failed to retry task %s", failed_task_id)
                    self._cancel_remaining_and_fail(session_id)
                    _session_done = True
            elif failed_task is not None:
                logger.warning(
                    "TM: task %s exhausted retries (%d/%d), cancelling remaining tasks and failing session %s",
                    failed_task_id,
                    failed_task.retry_count,
                    self._max_task_retries,
                    session_id,
                )
                self._notify_trackers(session_id, failed_task)
                self._cancel_remaining_and_fail(session_id)
                _session_done = True

            if not _session_done:
                self._q_notify(session_id, failed_task_id)
                next_task = self._q_pop(session_id)
                if next_task is None and self._q_is_empty(session_id):
                    self._fail_session(session_id)
                    _session_done = True

        if _session_done:
            self.cleanup_session(session_id)
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
        return self._q_pop(session_id)

    def activate(self, task_id: str, session_id: str | None = None) -> Task:
        return self._task_svc.transition(task_id, "ACTIVE", session_id)

    def complete(self, task_id: str, process_report: str | None = None, outputs: str | None = None, session_id: str | None = None) -> Task:
        task = self._task_svc.finish(task_id, process_report=process_report, outputs=outputs, session_id=session_id)
        self._reset_failure_counter(task.session_id)
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
            self._task_svc.transition(next_task.id, "ACTIVE", next_task.session_id)
        except Exception:
            logger.exception("TM: failed to activate task %s", next_task.id)

        template_name = str(next_task.settings.get("subagent_template", ""))
        agent_id = self._lm.prepare_executor(
            session_id=session_id,
            finished_agent_id=finished_agent_id,
            task_id=next_task.id,
            use_subagent=bool(next_task.settings.get("use_subagent")),
            template_name=template_name,
            inherit_memory=bool(next_task.settings.get("inherit_memory", True)),
            task_assigned_agent_id=next_task.assigned_agent_id or "",
            task_creator_agent_id=next_task.creator_agent_id or "",
        )
        if agent_id:
            try:
                task = self._task_svc.get(next_task.id, session_id)
                task.assigned_agent_id = agent_id
                self._task_svc.save(task)
                self._sync_sibling_tracking(session_id, task, agent_id)
            except Exception:
                logger.exception("TM: failed to update assigned_agent_id for task %s", next_task.id)
            try:
                session = self._session_svc.get(session_id)
                if next_task.id not in session.active_tasks:
                    session.active_tasks.append(next_task.id)
                    self._session_svc.save(session)
            except Exception:
                logger.exception("TM: failed to append active_tasks for task %s", next_task.id)
            self._lm.run_agent(session_id, agent_id, next_task.id)

    def _notify_trackers(self, session_id: str, task: Task) -> None:
        """向所有不在 RUNNING 状态的 tracker agent 写入 task 结果，并从其 tracking_tasks 中移除该 task。"""
        if not task.trackers or not self._memory_svc or not self._agent_store:
            return
        outcome = "completed" if task.status == "FINISHED" else "failed"
        content = _task_result_content(
            f"Tracked task「{task.title}」{outcome}.",
            task.outputs, task.process_report, task.error,
        )

        notified: list[str] = []
        for agent_id in list(task.trackers):
            try:
                agent_data = self._agent_store.get(session_id, agent_id)
                if agent_data is None or agent_data.get("status") == "RUNNING":
                    continue
                # The executor already recorded this result via
                # _write_execution_memory; writing it again here would duplicate it.
                # Still clean up the tracking bookkeeping below.
                if agent_id != task.assigned_agent_id:
                    self._memory_svc.append_message(
                        agent_id=agent_id,
                        role="assistant",
                        content=content,
                        session_id=session_id,
                        task_id=task.id,
                    )
                tracking: list[str] = agent_data.get("tracking_tasks", [])
                if task.id in tracking:
                    tracking.remove(task.id)
                    agent_data["tracking_tasks"] = tracking
                    self._agent_store.save(agent_data)
                notified.append(agent_id)
            except Exception:
                logger.warning("TM: failed to notify tracker %s for task %s", agent_id, task.id)
        if notified:
            try:
                for aid in notified:
                    if aid in task.trackers:
                        task.trackers.remove(aid)
                self._task_svc.save(task)
            except Exception:
                logger.warning("TM: failed to update trackers for task %s", task.id)

    def _sync_sibling_tracking(self, session_id: str, task: Task, agent_id: str) -> None:
        """将兄弟 task 的 id 写入新分配 agent 的 tracking_tasks，并将 agent_id 同步写入各兄弟 task 的 trackers。
        已终结的兄弟直接写 memory，不加入 tracking_tasks。"""
        if not self._agent_store or not task.parent_task_id:
            return
        _TERMINAL = {"FINISHED", "FAILED", "CANCELED"}
        try:
            data = self._agent_store.get(session_id, agent_id)
            if data is None:
                return
            tracking: list[str] = data.get("tracking_tasks", [])
            newly_tracked: list[str] = []
            for sib in self._task_svc.list_children(task.parent_task_id, session_id):
                if sib.id == task.id or sib.id in tracking:
                    continue
                if sib.status in _TERMINAL:
                    self._write_sib_result_to_memory(session_id, sib, agent_id, task.id)
                else:
                    tracking.append(sib.id)
                    newly_tracked.append(sib.id)
            if newly_tracked:
                data["tracking_tasks"] = tracking
                self._agent_store.save(data)
                for sib_id in newly_tracked:
                    try:
                        sib_task = self._task_svc.get(sib_id, session_id)
                        if agent_id not in sib_task.trackers:
                            sib_task.trackers.append(agent_id)
                            self._task_svc.save(sib_task)
                    except Exception:
                        pass
        except Exception:
            logger.warning("TM: failed to sync sibling tracking for agent %s task %s", agent_id, task.id)

    def _write_sib_result_to_memory(self, session_id: str, sib: Task, agent_id: str, task_id: str) -> None:
        """将已终结兄弟 task 的结果直接写入 agent memory。"""
        if not self._memory_svc:
            return
        # The agent already holds the result if it executed the sibling itself.
        if sib.assigned_agent_id == agent_id:
            return
        try:
            outcome = "completed" if sib.status == "FINISHED" else "failed"
            content = _task_result_content(
                f"Sibling task「{sib.title}」{outcome}.",
                sib.outputs, sib.process_report, sib.error,
            )
            self._memory_svc.append_message(
                agent_id=agent_id,
                role="assistant",
                content=content,
                session_id=session_id,
                task_id=task_id,
            )
        except Exception:
            logger.warning("TM: failed to write sib result to memory for agent %s sib %s", agent_id, sib.id)

    def _lm_recycle_finished(self, session_id: str, finished_agent_id: str) -> None:
        if self._lm is not None:
            self._lm.release(session_id, finished_agent_id)

    def _flush_tracking_tasks_to_memory(self, session_id: str, parent: Task) -> None:
        """将 parent agent 的 tracking_tasks 中已终结的 task 结果写入 memory，并双向清理关联。"""
        if not self._agent_store or not self._memory_svc or not parent.assigned_agent_id:
            return
        agent_data = self._agent_store.get(session_id, parent.assigned_agent_id)
        if not agent_data:
            return
        tracking: list[str] = list(agent_data.get("tracking_tasks", []))
        if not tracking:
            return
        flushed: list[str] = []
        for task_id in tracking:
            try:
                t = self._task_svc.get(task_id, session_id)
                if t.status not in ("FINISHED", "FAILED", "CANCELED"):
                    continue
                # Skip tasks the parent agent executed itself — it already holds the
                # result via _write_execution_memory; re-flushing would duplicate it.
                if t.assigned_agent_id != parent.assigned_agent_id:
                    outcome = "completed" if t.status == "FINISHED" else "failed"
                    content = _task_result_content(
                        f"Tracked task「{t.title}」{outcome}.",
                        t.outputs, t.process_report, t.error,
                    )
                    self._memory_svc.append_message(
                        agent_id=parent.assigned_agent_id,
                        role="assistant",
                        content=content,
                        session_id=session_id,
                        task_id=parent.id,
                    )
                flushed.append(task_id)
            except Exception:
                logger.exception("TM: failed to flush tracking task %s to parent %s memory", task_id, parent.id)
        if not flushed:
            return
        new_tracking = [tid for tid in agent_data.get("tracking_tasks", []) if tid not in flushed]
        agent_data["tracking_tasks"] = new_tracking
        self._agent_store.save(agent_data)
        for task_id in flushed:
            try:
                t = self._task_svc.get(task_id, session_id)
                if parent.assigned_agent_id in t.trackers:
                    t.trackers.remove(parent.assigned_agent_id)
                    self._task_svc.save(t)
            except Exception:
                pass

    def _try_resume_parent(self, session_id: str, finished_task_id: str) -> None:
        """Conclude a SUSPENDED parent once all its children are terminal.

        Any child FAILED → fail parent (cascade).
        All children succeeded → resume parent and push to queue.
        """
        if not finished_task_id:
            return
        try:
            finished_task = self._task_svc.get(finished_task_id, session_id)
            if not finished_task.parent_task_id:
                return
            parent = self._task_svc.get(finished_task.parent_task_id, session_id)
            if parent.status != "SUSPENDED":
                return
            children = self._task_svc.list_children(finished_task.parent_task_id, session_id)
            _terminal = {"FINISHED", "FAILED", "CANCELED"}
            if not children or not all(t.status in _terminal for t in children):
                return
            if any(t.status == "FAILED" for t in children):
                self._task_svc.fail(parent.id, error="cascade: child task failed", session_id=session_id)
                self._q_remove(session_id, parent.id)
                self._cascade_fail(session_id, parent.id)
            else:
                self._flush_tracking_tasks_to_memory(session_id, parent)
                self._task_svc.resume(parent.id, session_id)
                self._q_push(session_id, parent.id)
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
                self._task_svc.fail(task.id, error=f"cascade: dependency {failed_task_id} failed", session_id=task.session_id)
                self._q_remove(session_id, task.id)
                self._cascade_fail(session_id, task.id)
            except Exception:
                logger.exception("TM: _cascade_fail: failed to fail task %s", task.id)

    def _cancel_remaining_and_fail(self, session_id: str) -> None:
        try:
            self._task_svc.cancel_pending(session_id)
        except Exception:
            logger.exception("TM: failed to cancel pending tasks for session %s", session_id)
        self._q_clear(session_id)
        self._fail_session(session_id)

    def _fail_session(self, session_id: str) -> None:
        try:
            self._session_svc.transition(session_id, "FAILED")
        except Exception:
            logger.exception("TM: failed to transition session %s to FAILED", session_id)

    # ── Queue helpers (get + op + save) ──────────────────────────────────────

    def _q_push(self, session_id: str, task_id: str) -> None:
        session = self._session_svc.get(session_id)
        session.task_queue.push(task_id)
        self._session_svc.save(session)

    def _drain_pending(self, session_id: str) -> list[str]:
        with self._pending_lock:
            return self._pending.pop(session_id, [])

    def _q_pop(self, session_id: str) -> "Task | None":
        session = self._session_svc.get(session_id)
        # Flush the turn's buffered tasks in reverse so the first-submitted lands on
        # top of the stack (batch-internal FIFO), above any pre-existing tasks (LIFO).
        for task_id in reversed(self._drain_pending(session_id)):
            session.task_queue.push(task_id)
        task = session.task_queue.pop()
        self._session_svc.save(session)
        return task

    def _q_notify(self, session_id: str, completed_id: str) -> None:
        session = self._session_svc.get(session_id)
        session.task_queue.notify_completed(completed_id)
        self._session_svc.save(session)

    def _q_remove(self, session_id: str, task_id: str) -> None:
        session = self._session_svc.get(session_id)
        session.task_queue.remove(task_id)
        self._session_svc.save(session)

    def _q_clear(self, session_id: str) -> None:
        self._drain_pending(session_id)
        session = self._session_svc.get(session_id)
        session.task_queue.clear()
        self._session_svc.save(session)

    def _q_is_empty(self, session_id: str) -> bool:
        # Buffered-but-not-yet-flushed tasks count as work; never report empty while they exist.
        with self._pending_lock:
            if self._pending.get(session_id):
                return False
        return self._session_svc.get(session_id).task_queue.is_empty()

    def _orphan_pending_ids(self, session_id: str) -> list[str]:
        """PENDING tasks in the store that the queue no longer tracks.

        The TaskQueue is in-memory; a task can fall out of it (e.g. pop() drops an
        entry when its load fails transiently) while still PENDING in the store.
        Returns such ids so the session is reconciled instead of succeeding with
        real work left behind.
        """
        try:
            q = self._session_svc.get(session_id).task_queue.to_dict()
            known = set(q.get("ready", [])) | set(q.get("blocked", []))
        except Exception:
            known = set()
        with self._pending_lock:
            known |= set(self._pending.get(session_id, []))
        try:
            return [
                t.id for t in self._task_svc.list_by_session(session_id)
                if t.status == "PENDING" and t.id not in known
            ]
        except Exception:
            return []

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
