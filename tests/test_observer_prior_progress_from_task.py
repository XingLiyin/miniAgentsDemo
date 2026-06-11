from types import SimpleNamespace

from app.domain.models.task import Task
from app.runtime.prompt_builder import PromptBuilderFactory
from app.runtime.types import ActorResult, ConversationTurn, ReasoningContext


def _task(**kw):
    base = dict(id="t1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                status="TO_BE_OBSERVED", user_prompt="please do X", title="Do X",
                description="Detailed X", created_at="", updated_at="")
    base.update(kw)
    return Task(**base)


def _build(task, result):
    builder = PromptBuilderFactory.for_observer()
    ctx = ReasoningContext(goal="g", recent_messages=[], blackboard_snippets=[])
    return builder.build_messages(SimpleNamespace(user_prompt="sess"), result, ctx, task, [])


def test_prior_progress_reads_rounds_from_task():
    task = _task(execution_rounds=[
        {"turns": [{"llm_text": "asking the question", "tool_calls": []}],
         "process_report": "loaded refs, awaiting answer", "output": "", "ts": "2026-06-10T00:00:01Z"},
    ])
    result = ActorResult(task_id="t1", success=True, output="cur",
                         conversation_turns=[ConversationTurn(round=0, messages_sent=[], llm_text="round B reply")])
    content = str(_build(task, result)[0].content)
    assert "Prior progress" in content
    assert "=== Round 1 ===" in content
    assert "[Agent reply]\nasking the question" in content
    assert "[Process report]\nloaded refs, awaiting answer" in content
    assert "Current turns" in content
    assert "round B reply" in content


def test_first_round_no_prior_progress():
    task = _task(execution_rounds=[])
    content = str(_build(task, ActorResult(task_id="t1", success=True, output="cur"))[0].content)
    assert "Prior progress: none (first round)." in content


def test_multiple_rounds_numbered():
    task = _task(execution_rounds=[
        {"turns": [{"llm_text": "r1 reply", "tool_calls": []}], "process_report": "r1 report", "output": "", "ts": "1"},
        {"turns": [{"llm_text": "r2 reply", "tool_calls": []}], "process_report": "r2 report", "output": "", "ts": "2"},
    ])
    content = str(_build(task, ActorResult(task_id="t1", success=True, output="cur"))[0].content)
    assert "=== Round 1 ===" in content and "=== Round 2 ===" in content
    assert content.index("r1 report") < content.index("r2 report")
