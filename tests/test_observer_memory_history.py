"""Observer prompt keeps a single system + single user message. Within the
user message it renders sections:

  Current task / Task description
  Sub-task results
  User requirements
  Prior progress  — current task's earlier rounds (process_report summaries +
                    user replies), from memory, excluding the wrapped prompt
  Current turns   — this round's transcript
  [Session task list]

Only the current task's memory is used (other tasks excluded).
"""

from __future__ import annotations

from types import SimpleNamespace

from app.domain.models.task import Task
from app.runtime.prompt_builder import PromptBuilderFactory
from app.runtime.types import ActorResult, ConversationTurn, ReasoningContext, ToolCallRecord


def _task(task_id: str = "t1", **overrides) -> Task:
    t = Task(
        id=task_id,
        session_id="s1",
        creator_agent_id="a1",
        assigned_agent_id="a1",
        status="TO_BE_OBSERVED",
        user_prompt="please do X",
        title="Do X",
        description="Detailed X",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    for k, v in overrides.items():
        setattr(t, k, v)
    return t


def _ctx(recent_messages: list[dict]) -> ReasoningContext:
    return ReasoningContext(goal="g", recent_messages=recent_messages, blackboard_snippets=[])


def _session() -> SimpleNamespace:
    return SimpleNamespace(user_prompt="session goal prompt")


def _result(turns=None, output="") -> ActorResult:
    return ActorResult(task_id="t1", success=True, output=output, conversation_turns=turns or [])


def _build(recent, result):
    builder = PromptBuilderFactory.for_observer()
    return builder.build_messages(_session(), result, _ctx(recent), _task(), [])


class TestObserverSingleMessage:
    def test_single_user_message(self):
        msgs = _build([], _result(output="cur"))
        assert len(msgs) == 1
        assert msgs[0].role == "user"

    def test_prior_progress_grouped_by_round_with_clear_labels(self):
        recent = [
            {"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1"},   # wrapped prompt, dropped
            {"role": "assistant",
             "content": "asking the question\n\n# Process Report\n\nloaded refs, awaiting answer",
             "task_id": "t1"},
            {"role": "user", "content": "都没有", "task_id": "t1"},
        ]
        result = _result(turns=[ConversationTurn(round=0, messages_sent=[], llm_text="round B reply")])
        content = str(_build(recent, result)[0].content)

        assert "Prior progress" in content
        # round boundary + role/section labels are distinct
        assert "=== Round 1 ===" in content
        assert "[Agent reply]\nasking the question" in content
        assert "[Process report]\nloaded refs, awaiting answer" in content
        assert "[User reply]\n都没有" in content
        # the '# Process Report' marker is consumed by the split (not left inline)
        prior = content.split("Prior progress")[1].split("Current turns")[0]
        assert "# Process Report" not in prior
        # wrapped prompt is not duplicated into prior progress
        assert "## Current Goal" not in prior
        # current round shows under Current turns
        assert "Current turns" in content
        assert "round B reply" in content

    def test_each_assistant_starts_a_new_round(self):
        recent = [
            {"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1"},
            {"role": "assistant", "content": "r1 reply\n\n# Process Report\n\nr1 report", "task_id": "t1"},
            {"role": "user", "content": "都没有", "task_id": "t1"},
            {"role": "assistant", "content": "r2 reply\n\n# Process Report\n\nr2 report", "task_id": "t1"},
            {"role": "user", "content": "不知道", "task_id": "t1"},
        ]
        content = str(_build(recent, _result(output="cur"))[0].content)
        assert "=== Round 1 ===" in content and "=== Round 2 ===" in content
        # ordering preserved: round 1 before its user reply before round 2
        i_r1 = content.index("=== Round 1 ===")
        i_u1 = content.index("都没有")
        i_r2 = content.index("=== Round 2 ===")
        i_u2 = content.index("不知道")
        assert i_r1 < i_u1 < i_r2 < i_u2

    def test_excludes_other_tasks(self):
        recent = [
            {"role": "assistant", "content": "other task report", "task_id": "t0"},
            {"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1"},
            {"role": "assistant", "content": "current task report", "task_id": "t1"},
        ]
        content = str(_build(recent, _result(output="cur"))[0].content)
        assert "other task report" not in content
        assert "current task report" in content

    def test_first_round_has_no_prior_progress(self):
        recent = [{"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1"}]
        content = str(_build(recent, _result(output="cur"))[0].content)
        assert "Prior progress: none (first round)." in content

    def test_current_turns_render_tool_calls(self):
        turns = [ConversationTurn(
            round=0, messages_sent=[], llm_text="checking",
            tool_calls=[ToolCallRecord(tool_name="read", arguments={"p": "f"}, result="body", tool_call_id="tc1")],
        )]
        content = str(_build([], _result(turns=turns))[0].content)
        assert "Tool call: read" in content
        assert "Result [OK]: body" in content
        assert "Agent reply: checking" in content
