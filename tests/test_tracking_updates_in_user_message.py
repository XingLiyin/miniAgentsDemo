"""Tracker/sibling results are delivered to a tracking agent as a USER message on
each run via AgentLoop._inject_tracking_updates (the single delivery path):

  - non-self-submitted, non-self-executed, terminal tracked task → user message + cleared from tracking
  - self-submitted (has parent_tool_call_id) → NOT injected (goes via submit_task tool_result)
  - self-executed (assigned to this agent) → cleared without writing (already in own memory)
  - non-terminal → left in tracking for a later run
"""
from __future__ import annotations

from app.domain.models.task import Task
from app.runtime.agent_loop import AgentLoop


class _Mem:
    def __init__(self):
        self.msgs = []

    def append_message(self, agent_id, role, content, session_id="", task_id=None,
                       tool_call_id=None, tool_calls=None):
        self.msgs.append({"agent_id": agent_id, "role": role, "content": content, "task_id": task_id})


class _Agents:
    def __init__(self):
        self._d = {}

    def add(self, aid, tracking=None):
        self._d[aid] = {"id": aid, "session_id": "s1", "tracking_tasks": list(tracking or [])}

    def get(self, sid, aid):
        d = self._d.get(aid)
        return dict(d) if d else None

    def save(self, d):
        self._d[d["id"]] = dict(d)


class _TaskSvc:
    def __init__(self):
        self._d = {}

    def add(self, t):
        self._d[t.id] = t

    def get(self, tid, sid=None):
        return self._d[tid]

    def save(self, t):
        self._d[t.id] = t


def _loop(mem, agents, task_svc):
    return AgentLoop(None, task_svc, mem, None, agents, None, None, None)


def _tracked(tid, *, status="FINISHED", assigned="b1", ptc=None, output="out", report="rep"):
    t = Task(id=tid, session_id="s1", creator_agent_id="a1", assigned_agent_id=assigned,
             status=status, user_prompt="", title=tid, created_at="", updated_at="")
    t.outputs = output
    t.process_report = report
    t.parent_tool_call_id = ptc
    t.trackers = ["a1"]
    return t


def test_injects_tracked_result_as_user_message_and_clears_tracking():
    mem, agents, ts = _Mem(), _Agents(), _TaskSvc()
    ts.add(_tracked("c1", output="sib out"))
    agents.add("a1", tracking=["c1"])
    _loop(mem, agents, ts)._inject_tracking_updates("s1", "a1", "t1")
    user_msgs = [m for m in mem.msgs if m["role"] == "user"]
    assert len(user_msgs) == 1
    assert "sib out" in str(user_msgs[0]["content"])
    assert agents.get("s1", "a1")["tracking_tasks"] == []          # cleared
    assert "a1" not in ts.get("c1").trackers                       # tracker bookkeeping cleaned


def test_self_submitted_not_injected():
    mem, agents, ts = _Mem(), _Agents(), _TaskSvc()
    ts.add(_tracked("c1", ptc="tc1"))  # parent_tool_call_id -> tool_result path
    agents.add("a1", tracking=["c1"])
    _loop(mem, agents, ts)._inject_tracking_updates("s1", "a1", "t1")
    assert mem.msgs == []
    assert agents.get("s1", "a1")["tracking_tasks"] == ["c1"]      # left for tool_result delivery


def test_self_executed_cleared_without_write():
    mem, agents, ts = _Mem(), _Agents(), _TaskSvc()
    ts.add(_tracked("c1", assigned="a1"))  # executed by self
    agents.add("a1", tracking=["c1"])
    _loop(mem, agents, ts)._inject_tracking_updates("s1", "a1", "t1")
    assert mem.msgs == []
    assert agents.get("s1", "a1")["tracking_tasks"] == []          # cleared, no write


def test_non_terminal_not_injected():
    mem, agents, ts = _Mem(), _Agents(), _TaskSvc()
    ts.add(_tracked("c1", status="ACTIVE"))
    agents.add("a1", tracking=["c1"])
    _loop(mem, agents, ts)._inject_tracking_updates("s1", "a1", "t1")
    assert mem.msgs == []
    assert agents.get("s1", "a1")["tracking_tasks"] == ["c1"]      # still tracked
