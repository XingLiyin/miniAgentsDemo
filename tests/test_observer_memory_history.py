"""Observer prompt carries the CURRENT TASK's prior execution rounds as a
multi-turn conversation. Only messages whose task_id matches the current task
are prepended (other tasks' memory is excluded); prior rounds are represented
by their memory assistant summary (which carries the process_report). The
evaluation content stays the last message with its rendering unchanged, and
the current task's wrapped user_prompt is kept as the leading user message so
the sequence starts with the user role.
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

    def test_includes_current_task_prior_rounds(self):
        recent = [
            {"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1"},
            {"role": "assistant", "content": "round1 process report", "task_id": "t1"},
        ]
        msgs = self._build(recent)

        # current task wrapped prompt leads (provider-safe: starts with user)
        assert msgs[0].role == "user" and "## Current Goal" in str(msgs[0].content)
        # prior round shown via its process_report (assistant summary)
        assert msgs[1].role == "assistant" and "round1 process report" in str(msgs[1].content)
        # evaluation content is the last message, rendering preserved
        assert msgs[-1].role == "user"
        assert "Current task: Do X" in str(msgs[-1].content)
        assert "Execution transcript" in str(msgs[-1].content)

    def test_excludes_other_tasks_history(self):
        recent = [
            {"role": "user", "content": "other task prompt", "task_id": "t0"},
            {"role": "assistant", "content": "other task result", "task_id": "t0"},
            {"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1"},
            {"role": "assistant", "content": "round1 process report", "task_id": "t1"},
        ]
        msgs = self._build(recent)
        joined = "\n".join(str(m.content) for m in msgs)
        # other task's memory is not carried
        assert "other task prompt" not in joined
        assert "other task result" not in joined
        # current task's round IS carried
        assert "round1 process report" in joined

    def test_keeps_ask_human_user_answer(self):
        """A human answer (user message of the current task) is preserved."""
        recent = [
            {"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1"},
            {"role": "assistant", "content": "round1 process report", "task_id": "t1"},
            {"role": "user", "content": "here is my answer", "task_id": "t1"},
        ]
        msgs = self._build(recent)
        joined = "\n".join(str(m.content) for m in msgs)
        assert "here is my answer" in joined

    def test_no_current_task_history_yields_single_eval_message(self):
        msgs = self._build([{"role": "assistant", "content": "unrelated", "task_id": "t0"}])
        # only the eval message remains (other task filtered out)
        assert len(msgs) == 1
        assert msgs[0].role == "user"
        assert "Current task: Do X" in str(msgs[0].content)
