"""Tests for batch-aware task enqueue (intra-batch FIFO, inter-batch LIFO) and
idempotent parent suspension.

Covers three layers:
  1. TaskQueue        — push/pop/blocked/idempotency primitives.
  2. TaskManager      — on_task_created buffering + reversed flush at _q_pop.
  3. control_tools    — submit_task / submit_plan suspend the parent at most once.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.common.errors import AppError
from app.domain.models.task import Task
from app.domain.state_machine import TaskStateMachine
from app.orchestrator.task_manager import TaskManager
from app.orchestrator.task_queue import TaskQueue


# ── shared fakes ──────────────────────────────────────────────────────────────

def _make_task(task_id: str, *, status: str = "PENDING", dag_deps=None,
               assigned_agent_id: str = "") -> Task:
    return Task(
        id=task_id,
        session_id="s1",
        creator_agent_id="root",
        assigned_agent_id=assigned_agent_id,
        status=status,
        user_prompt="",
        title=task_id,
        dag_deps=list(dag_deps or []),
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


class FakeTaskSvc:
    """In-memory task store that enforces the real task state machine on transition."""

    def __init__(self) -> None:
        self.store: dict[str, Task] = {}
        self._sm = TaskStateMachine()
        self._seq = 0
        self.fail_get: set[str] = set()  # ids whose get() should raise (simulate load failure)

    def add(self, task: Task) -> None:
        self.store[task.id] = task

    def get(self, task_id: str, session_id: str | None = None) -> Task:
        if task_id in self.fail_get:
            raise RuntimeError(f"simulated load failure for {task_id}")
        return self.store[task_id]

    def list_by_session(self, session_id: str) -> list[Task]:
        return list(self.store.values())

    def list_children(self, parent_task_id: str, session_id: str | None = None) -> list[Task]:
        return [t for t in self.store.values() if t.parent_task_id == parent_task_id]

    def transition(self, task_id: str, to_status: str, session_id: str | None = None,
                   **kwargs) -> Task:
        task = self.store[task_id]
        self._sm.validate_task(task.status, to_status)  # raises AppError if illegal
        task.status = to_status
        return task

    def create(self, *, session_id: str, creator_agent_id: str, user_prompt,
               title: str = "", description: str = "", inputs=None,
               parent_task_id=None, dag_deps=None, assigned_agent_id=None) -> Task:
        self._seq += 1
        t = _make_task(f"created-{self._seq}", status="PENDING",
                       dag_deps=dag_deps, assigned_agent_id=assigned_agent_id or creator_agent_id)
        t.parent_task_id = parent_task_id
        self.store[t.id] = t
        return t

    def save(self, task: Task) -> None:
        self.store[task.id] = task


class FakeSessionSvc:
    """Returns a single session object that holds a real TaskQueue."""

    def __init__(self, task_svc: FakeTaskSvc) -> None:
        self.session = SimpleNamespace(id="s1", task_queue=TaskQueue(task_svc, "s1"))
        self.saved = 0

    def get(self, session_id: str):
        return self.session

    def save(self, session) -> None:
        self.saved += 1


# ══ Layer 1: TaskQueue ═════════════════════════════════════════════════════════

class TestTaskQueue:
    def _queue(self, *tasks: Task) -> tuple[TaskQueue, FakeTaskSvc]:
        svc = FakeTaskSvc()
        for t in tasks:
            svc.add(t)
        return TaskQueue(svc, "s1"), svc

    def test_push_goes_to_ready_when_deps_satisfied(self) -> None:
        q, _ = self._queue(_make_task("a"))
        q.push("a")
        assert q.to_dict() == {"ready": ["a"], "blocked": []}

    def test_push_goes_to_blocked_when_deps_unmet(self) -> None:
        # b depends on a, and a is not terminal → b is blocked
        q, _ = self._queue(_make_task("a"), _make_task("b", dag_deps=["a"]))
        q.push("b")
        assert q.to_dict() == {"ready": [], "blocked": ["b"]}

    def test_push_is_idempotent(self) -> None:
        q, _ = self._queue(_make_task("a"))
        q.push("a")
        q.push("a")
        assert q.to_dict()["ready"] == ["a"]

    def test_pop_is_lifo(self) -> None:
        q, _ = self._queue(_make_task("a"), _make_task("b"), _make_task("c"))
        for tid in ("a", "b", "c"):
            q.push(tid)
        assert [q.pop().id, q.pop().id, q.pop().id] == ["c", "b", "a"]
        assert q.pop() is None

    def test_pop_skips_non_pending(self) -> None:
        q, svc = self._queue(_make_task("a", status="FINISHED"), _make_task("b"))
        q.push("a")
        q.push("b")
        # b is on top and PENDING; a is FINISHED and must be skipped, not returned
        assert q.pop().id == "b"
        assert q.pop() is None

    def test_notify_completed_promotes_blocked(self) -> None:
        q, svc = self._queue(_make_task("a"), _make_task("b", dag_deps=["a"]))
        q.push("a")
        q.push("b")
        assert q.to_dict() == {"ready": ["a"], "blocked": ["b"]}
        # a finishes → b's deps now satisfied → promoted to ready
        svc.store["a"].status = "FINISHED"
        q.notify_completed("a")
        assert q.to_dict() == {"ready": ["a", "b"], "blocked": []}


# ══ Layer 2: TaskManager batch enqueue ═════════════════════════════════════════

class TestTaskManagerBatch:
    def _tm(self) -> tuple[TaskManager, FakeTaskSvc, FakeSessionSvc]:
        task_svc = FakeTaskSvc()
        session_svc = FakeSessionSvc(task_svc)
        tm = TaskManager(task_svc=task_svc, session_svc=session_svc, event_bus=None)
        return tm, task_svc, session_svc

    def _created(self, tm: TaskManager, svc: FakeTaskSvc, *ids: str) -> None:
        for tid in ids:
            svc.add(_make_task(tid))
            tm.on_task_created("TASK_CREATED", {"task_id": tid, "session_id": "s1"})

    def test_on_task_created_buffers_without_pushing(self) -> None:
        tm, svc, sess = self._tm()
        self._created(tm, svc, "a")
        # nothing in the actual queue yet…
        assert sess.session.task_queue.is_empty()
        # …but the manager knows there is pending work
        assert tm._q_is_empty("s1") is False

    def test_batch_pops_in_fifo_order(self) -> None:
        tm, svc, _ = self._tm()
        self._created(tm, svc, "a", "b", "c")  # submitted A, B, C in one batch
        assert [tm._q_pop("s1").id for _ in range(3)] == ["a", "b", "c"]
        assert tm._q_pop("s1") is None

    def test_inter_batch_is_lifo_with_intra_batch_fifo(self) -> None:
        tm, svc, _ = self._tm()
        # pre-existing task X already on the stack (direct push, e.g. a re-queue)
        svc.add(_make_task("x"))
        tm._q_push("s1", "x")
        # new batch A, B submitted afterwards
        self._created(tm, svc, "a", "b")
        # newest batch runs first, in submission order; old task last
        assert [tm._q_pop("s1").id for _ in range(3)] == ["a", "b", "x"]

    def test_q_is_empty_false_while_buffered(self) -> None:
        tm, svc, sess = self._tm()
        self._created(tm, svc, "a")
        assert tm._q_is_empty("s1") is False
        tm._q_pop("s1")  # flush + pop
        assert tm._q_is_empty("s1") is True

    def test_q_clear_drains_buffer(self) -> None:
        tm, svc, sess = self._tm()
        self._created(tm, svc, "a", "b")
        tm._q_clear("s1")
        assert tm._q_pop("s1") is None
        assert tm._q_is_empty("s1") is True

    def test_cleanup_session_drains_buffer(self) -> None:
        tm, svc, _ = self._tm()
        self._created(tm, svc, "a")
        tm.cleanup_session("s1")
        assert "s1" not in tm._pending


# ══ store reconciliation before SUCCEEDED ══════════════════════════════════════

class TestOrphanReconcile:
    def _tm(self):
        task_svc = FakeTaskSvc()
        session_svc = FakeSessionSvc(task_svc)
        tm = TaskManager(task_svc=task_svc, session_svc=session_svc, event_bus=None)
        return tm, task_svc, session_svc

    def test_pending_in_store_but_not_in_queue_is_orphan(self) -> None:
        tm, svc, _ = self._tm()
        svc.add(_make_task("orphan", status="PENDING"))  # in store only
        assert tm._orphan_pending_ids("s1") == ["orphan"]

    def test_tasks_tracked_by_queue_or_buffer_are_not_orphans(self) -> None:
        tm, svc, sess = self._tm()
        # ready
        svc.add(_make_task("r", status="PENDING"))
        sess.session.task_queue.push("r")
        # blocked
        svc.add(_make_task("dep", status="PENDING"))
        svc.add(_make_task("b", status="PENDING", dag_deps=["dep"]))
        sess.session.task_queue.push("b")
        # buffered
        svc.add(_make_task("buf", status="PENDING"))
        tm.on_task_created("TASK_CREATED", {"task_id": "buf", "session_id": "s1"})
        # finished tasks never count
        svc.add(_make_task("done", status="FINISHED"))
        # 'dep' is the only PENDING task not tracked anywhere
        assert tm._orphan_pending_ids("s1") == ["dep"]

    def test_pop_drops_task_on_load_failure_and_reconcile_recovers_it(self) -> None:
        tm, svc, sess = self._tm()
        svc.add(_make_task("x", status="PENDING"))
        sess.session.task_queue.push("x")
        # Simulate a transient load failure: pop() silently drops "x" from _ready…
        svc.fail_get.add("x")
        assert sess.session.task_queue.pop() is None
        assert sess.session.task_queue.is_empty()  # queue lost it
        # …but the store still has it PENDING, so reconciliation flags it as orphan.
        assert tm._orphan_pending_ids("s1") == ["x"]
        # once the store is readable again, re-queueing + pop recovers the task
        svc.fail_get.discard("x")
        for tid in tm._orphan_pending_ids("s1"):
            tm._q_push("s1", tid)
        assert tm._q_pop("s1").id == "x"


# ══ Layer 3: idempotent parent suspension ══════════════════════════════════════

@pytest.fixture
def control_env():
    """Wire control_tools._svc to fakes and restore afterwards."""
    from app.tools import control_tools

    task_svc = FakeTaskSvc()
    saved = dict(control_tools._svc)
    control_tools._svc.clear()
    control_tools._svc.update({"task_svc": task_svc, "session_svc": None, "agent_store": None})
    try:
        yield control_tools, task_svc
    finally:
        control_tools._svc.clear()
        control_tools._svc.update(saved)


def _parent_ctx(task_svc: FakeTaskSvc):
    from app.tools.types import CallContext

    parent = _make_task("parent", status="ACTIVE")  # active task currently running
    task_svc.add(parent)
    return CallContext(session_id="s1", agent_id="root", task=parent), parent


class TestIdempotentSuspend:
    def test_submit_task_twice_suspends_parent_once(self, control_env) -> None:
        control_tools, task_svc = control_env
        ctx, parent = _parent_ctx(task_svc)

        args = {"title": "t", "description": "d", "task_prompt": "p"}
        # Two submit_task calls in the same turn share the same ctx.task object.
        r1 = control_tools.submit_task.handler(dict(args), ctx)
        r2 = control_tools.submit_task.handler(dict(args), ctx)

        # Neither call surfaces an INVALID_STATE_TRANSITION error.
        assert not r1.is_error and not r2.is_error
        # Parent ends up SUSPENDED, and two child tasks were created.
        assert parent.status == "SUSPENDED"
        assert parent.actor_done is True
        assert len([t for t in task_svc.store.values() if t.parent_task_id == "parent"]) == 2

    def test_submit_plan_then_submit_task_no_double_suspend(self, control_env) -> None:
        control_tools, task_svc = control_env
        ctx, parent = _parent_ctx(task_svc)

        plan = control_tools.submit_plan.handler(
            {"tasks": [{"title": "a", "description": "da", "task_prompt": "pa"}]}, ctx
        )
        follow = control_tools.submit_task.handler(
            {"title": "b", "description": "db", "task_prompt": "pb"}, ctx
        )

        assert not plan.is_error and not follow.is_error
        assert parent.status == "SUSPENDED"

    def test_suspended_to_suspended_transition_is_actually_illegal(self) -> None:
        # Guards the assumption behind the fix: the state machine rejects SUSPENDED→SUSPENDED.
        sm = TaskStateMachine()
        with pytest.raises(AppError):
            sm.validate_task("SUSPENDED", "SUSPENDED")
