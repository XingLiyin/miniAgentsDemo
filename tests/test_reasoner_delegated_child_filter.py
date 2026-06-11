"""A delegated child task's raw transcript must NOT appear in the parent's actor
view — it is represented by the submit_task tool_result instead. Otherwise the
child's user/assistant messages land between the parent's submit tool_call and
its tool_result, breaking provider tool-pair adjacency.

Reasoner._filter_delegated_child_messages drops messages whose task_id belongs
to a delegated child (has parent_tool_call_id) and is not the current task.
"""
from __future__ import annotations

from app.domain.models.task import Task
from app.runtime.reasoner import Reasoner


class _TaskSvc:
    def __init__(self):
        self._d = {}

    def add(self, t):
        self._d[t.id] = t

    def get(self, tid, sid=None):
        if tid not in self._d:
            raise KeyError(tid)
        return self._d[tid]


def _task(tid, *, ptc=None):
    t = Task(id=tid, session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
             status="PENDING", user_prompt="", title=tid, created_at="", updated_at="")
    t.parent_tool_call_id = ptc
    return t


def _reasoner(task_svc):
    return Reasoner(memory_svc=None, blackboard_svc=None, task_svc=task_svc)


def test_delegated_child_messages_filtered_keeping_submit_pair_adjacent():
    ts = _TaskSvc()
    ts.add(_task("p1"))                 # parent (current task), not delegated
    ts.add(_task("c1", ptc="tc1"))      # delegated inline child
    r = _reasoner(ts)

    messages = [
        {"role": "user", "content": "parent goal", "task_id": "p1", "created_at": "1"},
        {"role": "assistant", "content": "delegating", "task_id": "p1", "created_at": "2",
         "tool_calls": [{"id": "tc1", "name": "submit_task", "input": {}}]},
        {"role": "user", "content": "child prompt", "task_id": "c1", "created_at": "3"},
        {"role": "assistant", "content": "child output", "task_id": "c1", "created_at": "4"},
        {"role": "tool", "content": "child result", "task_id": "p1", "created_at": "5", "tool_call_id": "tc1"},
    ]
    out = r._filter_delegated_child_messages(messages, ts.get("p1"), "s1")
    task_ids = [m["task_id"] for m in out]
    assert "c1" not in task_ids                          # child transcript removed
    # the submit tool_call and its tool_result are now adjacent
    roles = [m["role"] for m in out]
    assert roles == ["user", "assistant", "tool"]


def test_non_delegated_other_task_kept():
    ts = _TaskSvc()
    ts.add(_task("p1"))
    ts.add(_task("prev"))               # a prior top-level task, NOT delegated
    r = _reasoner(ts)
    messages = [
        {"role": "assistant", "content": "old result", "task_id": "prev", "created_at": "1"},
        {"role": "user", "content": "now", "task_id": "p1", "created_at": "2"},
    ]
    out = r._filter_delegated_child_messages(messages, ts.get("p1"), "s1")
    assert [m["task_id"] for m in out] == ["prev", "p1"]  # nothing dropped


def test_current_tasks_own_messages_never_filtered():
    # even if the current task itself is a delegated child, its own messages stay
    ts = _TaskSvc()
    ts.add(_task("c1", ptc="tc1"))      # current task is itself a delegated child
    r = _reasoner(ts)
    messages = [{"role": "user", "content": "my work", "task_id": "c1", "created_at": "1"}]
    out = r._filter_delegated_child_messages(messages, ts.get("c1"), "s1")
    assert len(out) == 1


def test_messages_without_task_id_kept():
    ts = _TaskSvc()
    ts.add(_task("p1"))
    r = _reasoner(ts)
    messages = [{"role": "assistant", "content": "[Context so far]", "task_id": None, "created_at": "1"}]
    out = r._filter_delegated_child_messages(messages, ts.get("p1"), "s1")
    assert len(out) == 1
