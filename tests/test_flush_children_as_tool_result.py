from types import SimpleNamespace
from app.domain.models.task import Task
from app.orchestrator.task_manager import TaskManager


class _Mem:
    def __init__(self): self.msgs = []
    def append_message(self, agent_id, role, content, session_id="", task_id=None,
                       tool_call_id=None, tool_calls=None):
        self.msgs.append(SimpleNamespace(agent_id=agent_id, role=role, content=content,
                                         tool_call_id=tool_call_id))


class _Agents:
    def __init__(self): self._d = {}
    def add(self, aid, tracking=None): self._d[aid] = {"id": aid, "session_id": "s1",
                                                       "status": "WAITING", "tracking_tasks": list(tracking or [])}
    def get(self, sid, aid): d = self._d.get(aid); return dict(d) if d else None
    def save(self, d): self._d[d["id"]] = dict(d)


class _TaskSvc:
    def __init__(self): self._d = {}
    def add(self, t): self._d[t.id] = t.to_dict()
    def get(self, tid, sid=None): return Task.from_dict(self._d[tid])
    def save(self, t): self._d[t.id] = t.to_dict()


def _child(cid, *, assigned="b1", tcid="tc1", output="out", report="rep", status="FINISHED"):
    t = Task(id=cid, session_id="s1", creator_agent_id="a1", assigned_agent_id=assigned,
             status=status, user_prompt="", title=cid, created_at="", updated_at="")
    t.outputs = output; t.process_report = report; t.parent_tool_call_id = tcid
    return t


def _tm(task_svc, agents, mem):
    return TaskManager(task_svc=task_svc, session_svc=SimpleNamespace(),
                       memory_svc=mem, agent_store=agents)


def test_self_submitted_children_delivered_as_tool_result():
    task_svc, agents, mem = _TaskSvc(), _Agents(), _Mem()
    c1 = _child("c1", tcid="tc1", output="o1", report="r1")
    c2 = _child("c2", tcid="tc1", output="o2", report="r2")  # same tool_call -> aggregate
    task_svc.add(c1); task_svc.add(c2)
    agents.add("a1", tracking=["c1", "c2"])
    parent = Task(id="p1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                  status="SUSPENDED", user_prompt="", title="P", created_at="", updated_at="")
    task_svc.add(parent)

    _tm(task_svc, agents, mem)._flush_tracking_tasks_to_memory("s1", parent)

    tool_msgs = [m for m in mem.msgs if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].tool_call_id == "tc1"
    body = str(tool_msgs[0].content)
    assert "o1" in body and "o2" in body
    assert "r1" in body and "r2" in body  # process report allowed in tool_result
    assert "id=c1" in body and "id=c2" in body  # which task the result belongs to
    # tracking cleared after flush
    assert agents.get("s1", "a1")["tracking_tasks"] == []


def test_tool_result_includes_all_round_process_reports():
    task_svc, agents, mem = _TaskSvc(), _Agents(), _Mem()
    c1 = _child("c1", tcid="tc1", output="final", report="latest snapshot")
    c1.execution_rounds = [
        {"turns": [], "process_report": "round 1 progress", "output": "", "ts": "", "mem_index": 0},
        {"turns": [], "process_report": "round 2 progress", "output": "", "ts": "", "mem_index": 1},
    ]
    task_svc.add(c1)
    agents.add("a1", tracking=["c1"])
    parent = Task(id="p1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                  status="SUSPENDED", user_prompt="", title="P", created_at="", updated_at="")
    task_svc.add(parent)

    _tm(task_svc, agents, mem)._flush_tracking_tasks_to_memory("s1", parent)
    body = str([m for m in mem.msgs if m.role == "tool"][0].content)
    assert "round 1 progress" in body and "round 2 progress" in body  # full history, not just last
    assert "Round 1" in body and "Round 2" in body


def test_distinct_tool_calls_produce_separate_tool_results():
    # two children under different submit calls -> two tool messages, each keyed to its id
    task_svc, agents, mem = _TaskSvc(), _Agents(), _Mem()
    c1 = _child("c1", tcid="tcA", output="oA")
    c2 = _child("c2", tcid="tcB", output="oB")
    task_svc.add(c1); task_svc.add(c2)
    agents.add("a1", tracking=["c1", "c2"])
    parent = Task(id="p1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                  status="SUSPENDED", user_prompt="", title="P", created_at="", updated_at="")
    task_svc.add(parent)

    _tm(task_svc, agents, mem)._flush_tracking_tasks_to_memory("s1", parent)

    tool_msgs = {m.tool_call_id: str(m.content) for m in mem.msgs if m.role == "tool"}
    assert set(tool_msgs) == {"tcA", "tcB"}
    assert "oA" in tool_msgs["tcA"] and "oB" not in tool_msgs["tcA"]
    assert "oB" in tool_msgs["tcB"] and "oA" not in tool_msgs["tcB"]


def test_failed_child_includes_error_in_tool_result():
    task_svc, agents, mem = _TaskSvc(), _Agents(), _Mem()
    c1 = _child("c1", tcid="tc5", output="", report="tried", status="FAILED")
    c1.error = "boom"
    task_svc.add(c1)
    agents.add("a1", tracking=["c1"])
    parent = Task(id="p1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                  status="SUSPENDED", user_prompt="", title="P", created_at="", updated_at="")
    task_svc.add(parent)

    _tm(task_svc, agents, mem)._flush_tracking_tasks_to_memory("s1", parent)
    tool_msgs = [m for m in mem.msgs if m.role == "tool"]
    assert len(tool_msgs) == 1 and tool_msgs[0].tool_call_id == "tc5"
    assert "boom" in str(tool_msgs[0].content)


def test_tracked_not_submitted_child_left_for_user_message_injection():
    # child has NO parent_tool_call_id, executed by another agent -> NOT flushed here;
    # it stays in tracking and is delivered via _inject_tracking_updates (user message).
    task_svc, agents, mem = _TaskSvc(), _Agents(), _Mem()
    c1 = _child("c1", assigned="b1", output="oX", report="rX")
    c1.parent_tool_call_id = None
    task_svc.add(c1)
    agents.add("a1", tracking=["c1"])
    parent = Task(id="p1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                  status="SUSPENDED", user_prompt="", title="P", created_at="", updated_at="")
    task_svc.add(parent)

    _tm(task_svc, agents, mem)._flush_tracking_tasks_to_memory("s1", parent)
    assert mem.msgs == []                                          # no assistant text written
    assert agents.get("s1", "a1")["tracking_tasks"] == ["c1"]      # left for user-message injection
