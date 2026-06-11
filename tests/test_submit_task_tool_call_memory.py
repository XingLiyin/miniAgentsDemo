from app.domain.models.task import Task
from app.runtime.agent_loop import AgentLoop
from app.runtime.types import ActorResult, ToolCallRecord


class _Mem:
    def __init__(self):
        self.msgs = []
    def append_message(self, agent_id, role, content, session_id="", task_id=None,
                       tool_call_id=None, tool_calls=None):
        self.msgs.append({"role": role, "content": content, "tool_calls": tool_calls,
                          "tool_call_id": tool_call_id})


class _TaskSvc:
    def __init__(self, children):
        self._c = {c.id: c for c in children}
        self.saved = []
    def get(self, tid, sid=None):
        return self._c[tid]
    def save(self, t):
        self.saved.append(t)
        self._c[t.id] = t
    def list_children(self, parent_id, sid=None):
        return [c for c in self._c.values() if c.parent_task_id == parent_id]


def _loop(mem, task_svc):
    return AgentLoop(None, task_svc, mem, None, None, None, None, None)


def _parent():
    return Task(id="p1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                status="SUSPENDED", user_prompt="x", title="P", created_at="", updated_at="")


def _child(cid):
    return Task(id=cid, session_id="s1", creator_agent_id="a1", assigned_agent_id="",
                status="PENDING", user_prompt="x", title=cid, parent_task_id="p1",
                created_at="", updated_at="")


def test_suspension_writes_submit_tool_call_and_backfills_children():
    c1, c2 = _child("c1"), _child("c2")
    mem, ts = _Mem(), _TaskSvc([c1, c2, _parent()])
    parent = _parent()
    result = ActorResult(task_id="p1", success=True, output="delegating",
                         tool_calls_made=[ToolCallRecord(tool_name="submit_task", arguments={"title": "Z"},
                                                         result="created", tool_call_id="tc77")])
    _loop(mem, ts)._write_suspension_memory("a1", parent, result, "s1", "p1")

    tc_msgs = [m for m in mem.msgs if m["role"] == "assistant" and m["tool_calls"]]
    assert tc_msgs and tc_msgs[0]["tool_calls"][0]["name"] == "submit_task"
    assert tc_msgs[0]["tool_calls"][0]["id"] == "tc77"
    assert tc_msgs[0]["content"] == "delegating"
    assert ts.get("c1").parent_tool_call_id == "tc77"
    assert ts.get("c2").parent_tool_call_id == "tc77"


def test_backfill_skips_children_already_linked():
    c1 = _child("c1")
    c2 = _child("c2"); c2.parent_tool_call_id = "old_tc"  # earlier batch, must NOT be overwritten
    mem, ts = _Mem(), _TaskSvc([c1, c2, _parent()])
    result = ActorResult(task_id="p1", success=True, output="more",
                         tool_calls_made=[ToolCallRecord(tool_name="submit_plan", arguments={"tasks": []},
                                                         result="planned", tool_call_id="tc88")])
    _loop(mem, ts)._write_suspension_memory("a1", _parent(), result, "s1", "p1")

    assert ts.get("c1").parent_tool_call_id == "tc88"
    assert ts.get("c2").parent_tool_call_id == "old_tc"
