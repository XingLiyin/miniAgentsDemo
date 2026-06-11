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


def test_rounds_interleave_with_current_memory_by_mem_index():
    # current task memory: [0]=goal, [1]=submit tool_call, [2]=tool_result.
    # round "before submit" anchored at mem_index=1 (before the submit message);
    # round "after submit" anchored at mem_index=3 (end). Result keeps the submit
    # tool_call and its tool_result adjacent.
    recent = [
        {"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1"},
        {"role": "assistant", "content": "delegating", "task_id": "t1",
         "tool_calls": [{"id": "tc9", "name": "submit_task", "input": {"title": "Z"}}]},
        {"role": "tool", "content": "child result", "task_id": "t1", "tool_call_id": "tc9"},
    ]
    task = _task(execution_rounds=[
        {"turns": [{"llm_text": "before submit", "tool_calls": []}], "process_report": "", "output": "", "mem_index": 1},
        {"turns": [{"llm_text": "after submit", "tool_calls": []}], "process_report": "", "output": "", "mem_index": 3},
    ])
    msgs = _build(recent, task)
    joined = "\n".join(str(m.content) for m in msgs)
    assert joined.index("Do X") < joined.index("before submit") < joined.index("delegating") \
        < joined.index("child result") < joined.index("after submit")
    # the submit tool_call message and its tool_result are adjacent (no round between)
    roles = [m.role for m in msgs]
    i = next(idx for idx, m in enumerate(msgs) if m.tool_calls)
    assert roles[i] == "assistant" and roles[i + 1] == "tool"


def test_context_messages_precede_current_and_rounds():
    # a prior top-level task's message (different task_id) is context and comes first,
    # regardless of any timestamps — ordering is driven by mem_index, not the clock.
    recent = [
        {"role": "assistant", "content": "OLD RESULT", "task_id": "prev", "created_at": "9999"},
        {"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1", "created_at": "0001"},
    ]
    task = _task(execution_rounds=[
        {"turns": [{"llm_text": "work", "tool_calls": []}], "process_report": "", "output": "", "mem_index": 1},
    ])
    joined = "\n".join(str(m.content) for m in _build(recent, task))
    assert joined.index("OLD RESULT") < joined.index("Do X") < joined.index("work")


def test_process_report_injected_as_user_review_note():
    recent = [{"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1"}]
    task = _task(execution_rounds=[
        {"turns": [{"llm_text": "tried", "tool_calls": []}], "process_report": "do Y next",
         "output": "", "mem_index": 1},
    ])
    msgs = _build(recent, task)
    review = [m for m in msgs if m.role == "user" and "## Last round review" in str(m.content)]
    assert review and "do Y next" in str(review[0].content)
