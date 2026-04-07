"""Task 领域服务（Phase 1）。"""

from __future__ import annotations

from app.common.errors import AppError
from app.common.utils import new_task_id, now_iso
from app.domain.events.event_bus import EventBus
from app.domain.events.event_types import TASK_CREATED, TASK_STARTED, TASK_FINISHED, TASK_FAILED
from app.domain.models.task import Task
from app.domain.state_machine import TaskStateMachine
from app.storage.file.task_store import TaskStore


class TaskService:
    """Task CRUD + 状态机调用。"""

    def __init__(
        self,
        store: TaskStore,
        state_machine: TaskStateMachine,
        event_bus: EventBus,
    ) -> None:
        self._store = store
        self._sm = state_machine
        self._bus = event_bus

    def create(
        self,
        session_id: str,
        agent_id: str,
        task_type: str,
        title: str,
        description: str = "",
        inputs: dict | None = None,
    ) -> Task:
        """创建新 Task，初始状态 PENDING。"""
        now = now_iso()
        task = Task(
            id=new_task_id(),
            session_id=session_id,
            agent_id=agent_id,
            type=task_type,
            title=title,
            status="PENDING",
            description=description,
            inputs=inputs or {},
            created_at=now,
            updated_at=now,
        )
        self._store.save(task.to_dict())
        self._bus.publish(TASK_CREATED, {"task_id": task.id, "session_id": session_id})
        return task

    def get(self, task_id: str) -> Task:
        data = self._store.get(task_id)
        if data is None:
            raise AppError("TASK_NOT_FOUND", f"Task {task_id} not found")
        return Task.from_dict(data)

    def save(self, task: Task) -> None:
        task.updated_at = now_iso()
        self._store.save(task.to_dict())

    def transition(self, task_id: str, to_status: str) -> Task:
        """校验并执行状态转换，持久化后发布事件。"""
        task = self.get(task_id)
        self._sm.validate_task(task.status, to_status)
        task.status = to_status
        self.save(task)
        event_map = {
            "ACTIVE": TASK_STARTED,
            "FINISHED": TASK_FINISHED,
            "FAILED": TASK_FAILED,
        }
        if to_status in event_map:
            self._bus.publish(event_map[to_status], {"task_id": task_id, "session_id": task.session_id})
        return task

    def finish(self, task_id: str, result: str | None = None, outputs: dict | None = None) -> Task:
        """完成 Task，写入 result/outputs 后转为 FINISHED。"""
        task = self.get(task_id)
        if result is not None:
            task.result = result
        if outputs is not None:
            task.outputs = outputs
        self.save(task)
        return self.transition(task_id, "FINISHED")

    def fail(self, task_id: str, error: str) -> Task:
        task = self.get(task_id)
        task.error = error
        self.save(task)
        return self.transition(task_id, "FAILED")

    def list_by_session(self, session_id: str) -> list[Task]:
        """列出 session 下所有 Task（扫描全量，Phase 1 可接受）。"""
        tasks = []
        for tid in self._store.list_ids():
            data = self._store.get(tid)
            if data and data.get("session_id") == session_id:
                tasks.append(Task.from_dict(data))
        return tasks

    def list_pending(self, session_id: str) -> list[Task]:
        """返回 session 下所有 PENDING task，按 created_at 升序排列。"""
        tasks = [t for t in self.list_by_session(session_id) if t.status == "PENDING"]
        tasks.sort(key=lambda t: t.created_at)
        return tasks
