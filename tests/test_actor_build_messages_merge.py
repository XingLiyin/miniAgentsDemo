from app.domain.models.task import Task
from app.runtime.prompt_builder import PromptBuilderFactory
from app.runtime.types import ReasoningContext


def _task(**kw):
    base = dict(id="t1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                status="ACTIVE", user_prompt="do X", title="Do X", description="d",
                user_prompt_in_memory=True, created_at="", updated_at="")
    base.update(kw)
    return Task(**base)


def _ctx(recent, task):
    return ReasoningContext(goal="g", recent_messages=recent, blackboard_snippets=[], current_task=task)


def _build(recent, task):
    builder = PromptBuilderFactory.for_actor()
    return builder.build_messages(task, _ctx(recent, task))


def test_rounds_interleave_with_memory_by_timestamp():
    recent = [
        {"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1", "created_at": "1"},
        {"role": "assistant", "content": "delegating", "task_id": "t1", "created_at": "4",
         "tool_calls": [{"id": "tc9", "name": "submit_task", "input": {"title": "Z"}}]},
        {"role": "tool", "content": "child output", "task_id": "t1", "created_at": "6", "tool_call_id": "tc9"},
    ]
    task = _task(execution_rounds=[
        {"turns": [{"llm_text": "before submit", "tool_calls": []}], "process_report": "", "output": "", "ts": "2"},
        {"turns": [{"llm_text": "after submit", "tool_calls": []}], "process_report": "", "output": "", "ts": "7"},
    ])
    msgs = _build(recent, task)
    joined = "\n".join(str(m.content) for m in msgs)
    assert joined.index("Do X") < joined.index("before submit") < joined.index("delegating") \
        < joined.index("child output") < joined.index("after submit")


def test_process_report_injected_as_user_review_note():
    recent = [{"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1", "created_at": "1"}]
    task = _task(execution_rounds=[
        {"turns": [{"llm_text": "tried", "tool_calls": []}], "process_report": "do Y next", "output": "", "ts": "2"},
    ])
    msgs = _build(recent, task)
    review = [m for m in msgs if m.role == "user" and "## Last round review" in str(m.content)]
    assert review and "do Y next" in str(review[0].content)
