"""Regression tests: a task's result must not be written to the executing
agent's memory twice.

The executing agent already records its own task result via
AgentLoop._write_execution_memory. For inline tasks (use_subagent=False) the
creator/parent agent is reused as the executor, and that same agent is also
listed in task.trackers (creator is added as a tracker at submit time). The
flush path in TaskManager must therefore SKIP (no-write) the agent that equals
task.assigned_agent_id — otherwise the result would land in its memory twice.
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
