"""Observer prompt organizes messages as a clean multi-turn conversation:

  [current task's prior rounds, from memory]   (user_prompt / assistant replies / user answers)
  [current round, reconstructed from ActorResult as real assistant(+tool) turns]
  user: assessment request (task / requirements / task list)

Only the current task's memory is included (other tasks excluded). Rendering
the current round as real turns keeps the prior user answer in its proper
conversational position instead of merging into the eval message, and avoids
duplicating the execution as inline transcript text.
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
    msgs = builder.build_messages(_session(), result, _ctx(recent), _task(), [])
    return builder.sanitize_messages(msgs)


class TestObserverConversationShape:
    def test_prior_answer_stays_in_position_not_merged_into_eval(self):
        """Regression: the prior user answer must not jump to the top of the eval."""
        recent = [
            {"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1"},
            {"role": "assistant", "content": "round A questions", "task_id": "t1"},
            {"role": "user", "content": "都没有", "task_id": "t1"},
        ]
        result = _result(turns=[ConversationTurn(round=0, messages_sent=[], llm_text="round B reply")])
        msgs = _build(recent, result)

        roles = [m.role for m in msgs]
        # clean alternating conversation, eval is its own final user message
        assert roles == ["user", "assistant", "user", "assistant", "user"]
        # the prior answer is its own turn, not glued to the eval
        assert str(msgs[2].content).strip() == "都没有"
        # current round rendered as the latest assistant turn
        assert "round B reply" in str(msgs[3].content)
        # eval message does not start with the prior answer, and has no inline transcript
        assert "都没有" not in str(msgs[-1].content)
        assert "Execution transcript" not in str(msgs[-1].content)
        assert "Please assess" in str(msgs[-1].content)

    def test_excludes_other_tasks_history(self):
        recent = [
            {"role": "user", "content": "other task prompt", "task_id": "t0"},
            {"role": "assistant", "content": "other task result", "task_id": "t0"},
            {"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1"},
        ]
        msgs = _build(recent, _result(output="cur"))
        joined = "\n".join(str(m.content) for m in msgs)
        assert "other task prompt" not in joined
        assert "other task result" not in joined

    def test_current_round_tool_calls_rendered_as_turns(self):
        turns = [ConversationTurn(
            round=0, messages_sent=[], llm_text="let me check",
            tool_calls=[ToolCallRecord(tool_name="read", arguments={"p": "f"}, result="file body", tool_call_id="tc1")],
        )]
        recent = [{"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1"}]
        msgs = _build(recent, _result(turns=turns))

        # assistant turn carries the tool call; a tool message carries its result
        asst = next(m for m in msgs if m.role == "assistant")
        assert asst.tool_calls and asst.tool_calls[0]["name"] == "read"
        tool_msg = next(m for m in msgs if m.role == "tool")
        assert "file body" in str(tool_msg.content) and tool_msg.tool_call_id == "tc1"

    def test_no_turns_falls_back_to_output_text(self):
        recent = [{"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1"}]
        msgs = _build(recent, _result(output="just text output"))
        assert any(m.role == "assistant" and "just text output" in str(m.content) for m in msgs)

    def test_eval_message_is_last_and_has_task_context(self):
        msgs = _build([], _result(output="cur"))
        assert msgs[-1].role == "user"
        assert "Current task: Do X" in str(msgs[-1].content)
        assert "User requirements:" in str(msgs[-1].content)
