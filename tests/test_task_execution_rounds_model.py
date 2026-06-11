from app.domain.models.task import Task


def _task(**kw) -> Task:
    base = dict(
        id="t1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
        status="PENDING", user_prompt="x", title="T", created_at="", updated_at="",
    )
    base.update(kw)
    return Task(**base)


def test_new_fields_default_empty():
    t = _task()
    assert t.execution_rounds == []
    assert t.parent_tool_call_id is None


def test_roundtrip_serialization():
    t = _task()
    t.execution_rounds = [{"turns": [], "process_report": "r", "output": "o", "ts": "2026-06-10T00:00:00Z"}]
    t.parent_tool_call_id = "tc_123"
    t2 = Task.from_dict(t.to_dict())
    assert t2.execution_rounds == t.execution_rounds
    assert t2.parent_tool_call_id == "tc_123"


def test_from_dict_missing_fields_backward_compatible():
    d = _task().to_dict()
    d.pop("execution_rounds", None)
    d.pop("parent_tool_call_id", None)
    t = Task.from_dict(d)
    assert t.execution_rounds == []
    assert t.parent_tool_call_id is None
