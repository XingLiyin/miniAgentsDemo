"""Regression tests: a task's result must not be written to the executing
agent's memory twice.

The executing agent already records its own task result via
AgentLoop._write_execution_memory. For inline tasks (use_subagent=False) the
creator/parent agent is reused as the executor, and that same agent is also
listed in task.trackers (creator is added as a tracker at submit time). The
tracker-notification paths in TaskManager must therefore SKIP the agent that
equals task.assigned_agent_id — otherwise the result lands in its memory twice.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.models.task import Task
from app.orchestrator.task_manager import TaskManager


# ── fakes ──────────────────────────────────────────────────────────────────────

def _make_task(task_id="t1", *, session_id="s1", assigned="a1",
               status="FINISHED", **overrides) -> Task:
    t = Task(
        id=task_id,
        session_id=session_id,
        creator_agent_id=assigned,
        assigned_agent_id=assigned,
        status=status,
        user_prompt="",
        title="Child task",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    t.outputs = "the result"
    t.process_report = "did the work"
    for k, v in overrides.items():
        setattr(t, k, v)
    return t


class FakeAgentStore:
    def __init__(self) -> None:
        self._d: dict[str, dict] = {}

    def add(self, agent_id, *, status="WAITING", tracking_tasks=None) -> None:
        self._d[agent_id] = {
            "id": agent_id,
            "session_id": "s1",
            "status": status,
            "tracking_tasks": list(tracking_tasks or []),
        }

    def get(self, session_id, agent_id):
        d = self._d.get(agent_id)
        return dict(d) if d is not None else None

    def save(self, data) -> None:
        self._d[data["id"]] = dict(data)


class FakeMemorySvc:
    def __init__(self) -> None:
        self.messages: list[SimpleNamespace] = []

    def append_message(self, agent_id, role, content, session_id="", task_id=None,
                       tool_call_id=None, tool_calls=None):
        self.messages.append(SimpleNamespace(agent_id=agent_id, role=role, content=content))
        return None


class FakeTaskSvc:
    def __init__(self) -> None:
        self._d: dict[str, dict] = {}

    def add(self, task: Task) -> None:
        self._d[task.id] = task.to_dict()

    def get(self, task_id, session_id=None) -> Task:
        return Task.from_dict(self._d[task_id])

    def save(self, task: Task) -> None:
        self._d[task.id] = task.to_dict()


def _tm(task_svc, agent_store, memory_svc) -> TaskManager:
    return TaskManager(
        task_svc=task_svc,
        session_svc=SimpleNamespace(),
        memory_svc=memory_svc,
        agent_store=agent_store,
    )


# ── tests ────────────────────────────────────────────────────────────────────

class TestNotifyTrackersSkipsExecutor:
    def test_executor_not_notified_other_tracker_is(self):
        task_svc = FakeTaskSvc()
        agents = FakeAgentStore()
        mem = FakeMemorySvc()

        # a1 executed the task (assigned) and also tracks it; a2 is a separate tracker.
        task = _make_task(assigned="a1", trackers=["a1", "a2"])
        task_svc.add(task)
        agents.add("a1", tracking_tasks=["t1"])
        agents.add("a2", tracking_tasks=["t1"])

        _tm(task_svc, agents, mem)._notify_trackers("s1", task)

        notified = [m.agent_id for m in mem.messages]
        # executor (a1) already has the result via _write_execution_memory → skip it
        assert "a1" not in notified
        # the genuine external tracker still gets it exactly once
        assert notified.count("a2") == 1

    def test_executor_removed_from_trackers_without_write(self):
        task_svc = FakeTaskSvc()
        agents = FakeAgentStore()
        mem = FakeMemorySvc()

        task = _make_task(assigned="a1", trackers=["a1"])
        task_svc.add(task)
        agents.add("a1", tracking_tasks=["t1"])

        _tm(task_svc, agents, mem)._notify_trackers("s1", task)

        assert mem.messages == []  # nothing written
        # bookkeeping still cleaned up so it is never revisited
        assert "a1" not in task_svc.get("t1").trackers
        assert "t1" not in agents.get("s1", "a1")["tracking_tasks"]


class TestFlushTrackingSkipsExecutor:
    def test_parent_executed_tracked_task_not_rewritten(self):
        task_svc = FakeTaskSvc()
        agents = FakeAgentStore()
        mem = FakeMemorySvc()

        # parent agent "a1" tracks t2, but it also executed t2 inline (assigned=a1).
        child = _make_task("t2", assigned="a1", status="FINISHED")
        task_svc.add(child)
        agents.add("a1", tracking_tasks=["t2"])

        parent = _make_task("p1", assigned="a1", status="SUSPENDED")
        _tm(task_svc, agents, mem)._flush_tracking_tasks_to_memory("s1", parent)

        assert mem.messages == []  # parent already has t2's result from execution
        assert "t2" not in agents.get("s1", "a1")["tracking_tasks"]


class TestWriteSibResultSkipsExecutor:
    def test_sibling_executed_by_same_agent_not_rewritten(self):
        task_svc = FakeTaskSvc()
        agents = FakeAgentStore()
        mem = FakeMemorySvc()

        # agent a1 already executed sibling t2 → it must not get t2's result again.
        sib = _make_task("t2", assigned="a1", status="FINISHED")
        _tm(task_svc, agents, mem)._write_sib_result_to_memory("s1", sib, "a1", "t1")

        assert mem.messages == []

    def test_sibling_executed_by_other_agent_is_written(self):
        task_svc = FakeTaskSvc()
        agents = FakeAgentStore()
        mem = FakeMemorySvc()

        sib = _make_task("t2", assigned="a2", status="FINISHED")
        _tm(task_svc, agents, mem)._write_sib_result_to_memory("s1", sib, "a1", "t1")

        assert [m.agent_id for m in mem.messages] == ["a1"]
