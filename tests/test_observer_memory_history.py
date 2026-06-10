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

    def test_prior_progress_section_holds_reports_and_user_replies(self):
        recent = [
            {"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1"},   # wrapped prompt, dropped
            {"role": "assistant", "content": "round A process report", "task_id": "t1"},
            {"role": "user", "content": "都没有", "task_id": "t1"},
        ]
        result = _result(turns=[ConversationTurn(round=0, messages_sent=[], llm_text="round B reply")])
        content = str(_build(recent, result)[0].content)

        assert "Prior progress" in content
        assert "Progress: round A process report" in content
        assert "User reply: 都没有" in content
        # wrapped prompt is not duplicated into prior progress
        assert "## Current Goal" not in content.split("Prior progress")[1]
        # current round shows under Current turns
        assert "Current turns" in content
        assert "round B reply" in content

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
