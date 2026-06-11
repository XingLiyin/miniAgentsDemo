"""C2 regression: when a SUSPENDED parent's children cascade-fail, the parent's
submit tool_call must still be closed with a matching tool_result (carrying the
failure), so no dangling tool_use is left in the parent agent's memory.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.domain.models.task import Task
from app.orchestrator.task_manager import TaskManager


class _Mem:
    def __init__(self):
        self.msgs = []

    def append_message(self, agent_id, role, content, session_id="", task_id=None,
                       tool_call_id=None, tool_calls=None):
        self.msgs.append(SimpleNamespace(agent_id=agent_id, role=role, content=content,
                                         tool_call_id=tool_call_id))


class _Agents:
    def __init__(self):
        self._d = {}

    def add(self, aid, tracking=None):
        self._d[aid] = {"id": aid, "session_id": "s1", "status": "WAITING",
                        "tracking_tasks": list(tracking or [])}

    def get(self, sid, aid):
        d = self._d.get(aid)
        return dict(d) if d else None

    def save(self, d):
        self._d[d["id"]] = dict(d)


class _TaskSvc:
    def __init__(self):
        self._d = {}
        self.failed = []

    def add(self, t):
        self._d[t.id] = t.to_dict()

    def get(self, tid, sid=None):
        return Task.from_dict(self._d[tid])

    def save(self, t):
        self._d[t.id] = t.to_dict()

    def list_children(self, pid, sid=None):
        return [Task.from_dict(d) for d in self._d.values() if d.get("parent_task_id") == pid]

    def list_by_session(self, sid):
        return [Task.from_dict(d) for d in self._d.values()]

    def fail(self, tid, error=None, session_id=None, **kw):
        self.failed.append(tid)
        t = Task.from_dict(self._d[tid])
        t.status = "FAILED"
        t.error = error
        self._d[tid] = t.to_dict()


def _tm(task_svc, agents, mem):
    tm = TaskManager(task_svc=task_svc, session_svc=SimpleNamespace(),
                     memory_svc=mem, agent_store=agents)
    # Isolate the new flush behavior from queue/cascade plumbing (covered elsewhere).
    tm._q_remove = lambda *a, **k: None
    tm._cascade_fail = lambda *a, **k: None
    return tm


def test_cascade_fail_writes_tool_result_for_submit_call():
    task_svc, agents, mem = _TaskSvc(), _Agents(), _Mem()
    parent = Task(id="p1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                  status="SUSPENDED", user_prompt="", title="P", created_at="", updated_at="")
    child = Task(id="c1", session_id="s1", creator_agent_id="a1", assigned_agent_id="b1",
                 status="FAILED", user_prompt="", title="C", parent_task_id="p1",
                 created_at="", updated_at="")
    child.error = "boom"
    child.process_report = "tried"
    child.parent_tool_call_id = "tc1"
    task_svc.add(parent)
    task_svc.add(child)
    agents.add("a1", tracking=["c1"])

    _tm(task_svc, agents, mem)._try_resume_parent("s1", "c1")

    tool_msgs = [m for m in mem.msgs if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].tool_call_id == "tc1"
    assert "boom" in str(tool_msgs[0].content)
    assert "p1" in task_svc.failed  # parent still failed after closing the pair
