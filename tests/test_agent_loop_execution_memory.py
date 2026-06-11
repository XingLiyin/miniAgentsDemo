from app.domain.models.task import Task
from app.runtime.agent_loop import AgentLoop
from app.runtime.types import ActorResult, ConversationTurn, ObserverVerdict, ToolCallRecord


class _Mem:
    def __init__(self):
        self.msgs = []
    def append_message(self, agent_id, role, content, session_id="", task_id=None,
                       tool_call_id=None, tool_calls=None):
        self.msgs.append({"role": role, "content": content, "tool_call_id": tool_call_id,
                          "tool_calls": tool_calls, "task_id": task_id})
    def get_all_messages(self, agent_id):
        return list(self.msgs)


class _TaskSvc:
    def __init__(self):
        self.saved = []
    def save(self, task):
        self.saved.append(task)


def _loop(mem, task_svc):
    return AgentLoop(None, task_svc, mem, None, None, None, None, None)


def _task():
    return Task(id="t1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                status="TO_BE_OBSERVED", user_prompt="x", title="T", created_at="", updated_at="")


def test_append_round_records_turns_and_report():
    mem, ts = _Mem(), _TaskSvc()
    task = _task()
    task.outputs = "final out"
    result = ActorResult(
        task_id="t1", success=True, output="final out",
        conversation_turns=[ConversationTurn(
            round=0, messages_sent=[], llm_text="working",
            tool_calls=[ToolCallRecord(tool_name="read", arguments={}, result="r", tool_call_id="tc1")],
        )],
    )
    _loop(mem, ts)._append_execution_round("a1", task, result, "all good", "s1", "t1")
    assert len(task.execution_rounds) == 1
    rec = task.execution_rounds[0]
    assert rec["process_report"] == "all good"
    assert rec["output"] == "final out"
    assert rec["turns"][0]["tool_calls"][0]["tool_name"] == "read"
    assert rec["mem_index"] == 0          # no current-task memory yet → anchors at front
    assert task in ts.saved


def test_append_round_mem_index_counts_current_task_messages():
    mem, ts = _Mem(), _TaskSvc()
    # two messages already in memory for this task, one for another task
    mem.append_message("a1", "user", "goal", task_id="t1")
    mem.append_message("a1", "assistant", "other", task_id="t0")
    mem.append_message("a1", "assistant", "sub", task_id="t1")
    task = _task()
    task.outputs = "out"
    result = ActorResult(task_id="t1", success=True, output="out")
    _loop(mem, ts)._append_execution_round("a1", task, result, "", "s1", "t1")
    assert task.execution_rounds[0]["mem_index"] == 2   # only t1 messages counted


def test_write_execution_memory_is_output_only_no_process_report():
    mem, ts = _Mem(), _TaskSvc()
    task = _task()
    task.outputs = "the answer"
    verdict = ObserverVerdict(summary="did the work")
    _loop(mem, ts)._write_execution_memory("a1", task, verdict, "s1", "t1")
    assert len(mem.msgs) == 1
    assert mem.msgs[0]["role"] == "assistant"
    assert mem.msgs[0]["content"] == "the answer"
    assert "# Process Report" not in str(mem.msgs[0]["content"])


def test_write_execution_memory_skips_when_no_output():
    mem, ts = _Mem(), _TaskSvc()
    task = _task()
    task.outputs = ""
    _loop(mem, ts)._write_execution_memory("a1", task, ObserverVerdict(summary="report only"), "s1", "t1")
    assert mem.msgs == []
