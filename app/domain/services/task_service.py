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
        creator_agent_id: str,
        user_prompt: str | list,
        title: str = "",
        description: str = "",
        inputs: dict | None = None,
        assigned_agent_id: str | None = None,
        parent_task_id: str | None = None,
        dag_deps: list[str] | None = None,
    ) -> Task:
        """创建新 Task，初始状态 PENDING。

        assigned_agent_id 默认与 creator_agent_id 相同，auto-spawn 时会被更新。
        """
        now = now_iso()
        task = Task(
            id=new_task_id(),
            session_id=session_id,
            creator_agent_id=creator_agent_id,
            assigned_agent_id=assigned_agent_id or creator_agent_id,
            user_prompt=user_prompt,
            title=title,
            description=description,
            settings=inputs or {},
            status="PENDING",
            parent_task_id=parent_task_id,
            dag_deps=dag_deps or [],
            created_at=now,
            updated_at=now,
        )
        self._store.save(task.to_dict())
        self._bus.publish(TASK_CREATED, {"task_id": task.id, "session_id": session_id})
        try:
            from app.common.sse_bus import get_sse_bus
            get_sse_bus().push(session_id, {"type": "task_created", "task": task.to_dict()})
        except Exception:
            pass
        return task

    def get(self, task_id: str, session_id: str | None = None) -> Task:
        """获取 Task。提供 session_id 时走 O(1) 直接路径，否则回退扫描。"""
        data = self._store.get(task_id, session_id)
        if data is None:
            raise AppError("TASK_NOT_FOUND", f"Task {task_id} not found")
        return Task.from_dict(data)

    def save(self, task: Task) -> None:
        task.updated_at = now_iso()
        self._store.save(task.to_dict())

    def transition(self, task_id: str, to_status: str, session_id: str | None = None) -> Task:
        """校验并执行状态转换，持久化后发布事件。"""
        task = self.get(task_id, session_id)
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
        try:
            from app.common.sse_bus import get_sse_bus
            get_sse_bus().push(task.session_id, {"type": "task_updated", "task": task.to_dict()})
        except Exception:
            pass
        return task

    def finish(self, task_id: str, result: str | None = None, outputs: str | None = None, session_id: str | None = None) -> Task:
        """完成 Task，写入 result/outputs 后转为 FINISHED。"""
        task = self.get(task_id, session_id)
        if result is not None:
            task.result = result
        if outputs is not None:
            task.outputs = outputs
        self.save(task)
        return self.transition(task_id, "FINISHED", task.session_id)

    def fail(self, task_id: str, error: str, session_id: str | None = None) -> Task:
        task = self.get(task_id, session_id)
        task.error = error
        self.save(task)
        return self.transition(task_id, "FAILED", task.session_id)

    def to_be_observed(self, task_id: str, session_id: str | None = None) -> Task:
        """Actor 完成后转入待观察状态。"""
        return self.transition(task_id, "TO_BE_OBSERVED", session_id)

    def reopen(self, task_id: str, session_id: str | None = None) -> Task:
        """将 FINISHED 任务重置为 PENDING（Observer 复核不通过时使用）。"""
        task = self.get(task_id, session_id)
        task.result = None
        task.outputs = ""
        self.save(task)
        return self.transition(task_id, "PENDING", task.session_id)

    def retry(self, task_id: str, session_id: str | None = None) -> Task:
        """将 FAILED 任务打回 PENDING，并递增重试计数。"""
        task = self.get(task_id, session_id)
        task.error = None
        task.retry_count += 1
        self.save(task)
        return self.transition(task_id, "PENDING", task.session_id)

    def resume(self, task_id: str, session_id: str | None = None) -> Task:
        """将 SUSPENDED 任务打回 PENDING（子任务完成后父任务重新入队）。"""
        return self.transition(task_id, "PENDING", session_id)

    def list_by_session(self, session_id: str) -> list[Task]:
        """O(1) 目录列表 + 批量读取，不再全量扫描。"""
        tasks = []
        for tid in self._store.list_by_session(session_id):
            data = self._store.get(tid, session_id)
            if data:
                tasks.append(Task.from_dict(data))
        return tasks

    def list_by_agent(self, session_id: str, agent_id: str) -> list[Task]:
        """列出 session 下指定 agent 被分配的所有 Task。"""
        return [
            t for t in self.list_by_session(session_id)
            if t.assigned_agent_id == agent_id
        ]

    def cancel_pending(self, session_id: str) -> int:
        """取消 session 内所有 PENDING tasks。

        用于 replan：清空当前规划，从新 plan task 重新开始。
        返回取消数量。
        """
        cancelled = 0
        for task in self.list_by_session(session_id):
            if task.status == "PENDING":
                self.transition(task.id, "CANCELED", session_id)
                cancelled += 1
        return cancelled

    def list_children(self, parent_task_id: str, session_id: str) -> list[Task]:
        """返回 session 下所有 parent_task_id 匹配的子任务。"""
        return [t for t in self.list_by_session(session_id) if t.parent_task_id == parent_task_id]

    def list_pending(self, session_id: str) -> list[Task]:
        """返回 session 下所有 PENDING task，按 created_at 升序排列。"""
        tasks = [t for t in self.list_by_session(session_id) if t.status == "PENDING"]
        tasks.sort(key=lambda t: t.created_at)
        return tasks
