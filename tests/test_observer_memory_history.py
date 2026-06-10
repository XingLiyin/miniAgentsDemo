"""Observer prompt now carries memory history as a multi-turn conversation,
like the actor: ctx.recent_messages is prepended, the trailing current-round
user_prompt is dropped (the eval message restates it), and the observer's
evaluation content stays the last message with its rendering unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.domain.models.task import Task
from app.runtime.prompt_builder import PromptBuilderFactory
from app.runtime.types import ActorResult, ReasoningContext


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


def _result() -> ActorResult:
    return ActorResult(task_id="t1", success=True, output="did the thing")


def _session() -> SimpleNamespace:
    return SimpleNamespace(user_prompt="session goal prompt")


class TestObserverMemoryHistory:
    def _build(self, recent):
        builder = PromptBuilderFactory.for_observer()
        return builder.build_messages(_session(), _result(), _ctx(recent), _task(), [])

    def test_prepends_prior_rounds_as_multi_turn(self):
        recent = [
            {"role": "user", "content": "round1 prompt", "task_id": "t1"},
            {"role": "assistant", "content": "round1 result summary", "task_id": "t1"},
            {"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1"},  # current round
        ]
        msgs = self._build(recent)

        # prior round shows up as its own turns
        assert msgs[0].role == "user" and "round1 prompt" in str(msgs[0].content)
        assert msgs[1].role == "assistant" and "round1 result summary" in str(msgs[1].content)
        # evaluation content is the last message, rendering preserved
        assert msgs[-1].role == "user"
        assert "Current task: Do X" in str(msgs[-1].content)
        assert "Execution transcript" in str(msgs[-1].content)

    def test_strips_trailing_current_round_user_prompt(self):
        recent = [
            {"role": "assistant", "content": "round1 result summary", "task_id": "t1"},
            {"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1"},  # current round, dropped
        ]
        msgs = self._build(recent)
        # the wrapped current-round user_prompt must not be carried as its own message
        assert all("## Current Goal" not in str(m.content) for m in msgs[:-1])
        # only the prior assistant summary + the eval message remain
        assert [m.role for m in msgs] == ["assistant", "user"]

    def test_does_not_strip_when_trailing_is_other_task(self):
        recent = [
            {"role": "user", "content": "different task prompt", "task_id": "t0"},
        ]
        msgs = self._build(recent)
        # trailing entry belongs to another task → kept
        assert "different task prompt" in str(msgs[0].content)
        assert len(msgs) == 2  # history + eval

    def test_empty_history_yields_single_eval_message(self):
        msgs = self._build([])
        assert len(msgs) == 1
        assert msgs[0].role == "user"
        assert "Current task: Do X" in str(msgs[0].content)
