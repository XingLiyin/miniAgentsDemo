"""Tests for AgentLoop.run() (app/runtime/agent_loop.py).

The per-helper paths (execution rounds, tracking injection, submit memory,
duplicate-memory avoidance) already have focused suites; this covers the run()
orchestration itself: budget guard, prompt persistence, suspension, observer
outcomes, blackboard publishing, the finally-block status sync, and interrupts.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.common.errors import AppError
from app.common.interrupt import AgentInterruptedError, InterruptContext, InterruptRegistry
from app.domain.models.agent import Agent, AgentCapability, LoopGuard
from app.domain.models.task import Task
from app.runtime.agent_loop import AgentLoop
from app.runtime.types import (
    ActorResult, ConversationTurn, ObserverVerdict, ReasoningContext, ToolCallRecord,
)


# ── builders ──────────────────────────────────────────────────────────────────

def _task(**kw) -> Task:
    base = dict(id="t1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                status="ACTIVE", user_prompt="do X", title="Do X",
                user_prompt_in_memory=True, created_at="", updated_at="")
    base.update(kw)
    return Task(**base)


def _agent_dict(agent_id="a1", status="IDLE", **kw) -> dict:
    a = Agent(id=agent_id, session_id="s1", template_id="tpl", name="ag", status=status,
              actor=AgentCapability(), observer=AgentCapability(),
              loop_guard=LoopGuard(), **kw)
    return a.to_dict()


def _session(**kw):
    base = dict(id="s1", user_prompt="p", goal="g", status="RUNNING",
                token_budget=0, output_tokens_used=0, root_agent_id="a1", metadata={})
    base.update(kw)
    return SimpleNamespace(**base)


def _result(success=True, output="the output", exit_reason="normal",
            tool_calls=None, turns=None, context_tokens=0) -> ActorResult:
    return ActorResult(
        task_id="t1", success=success, output=output, exit_reason=exit_reason,
        tool_calls_made=tool_calls or [],
        conversation_turns=turns if turns is not None else
        [ConversationTurn(round=0, messages_sent=[], llm_text=output)],
        context_tokens=context_tokens,
    )


class _AgentStore:
    """Stateful agent store: save() is visible to the next get(), like the real one.

    Wraps a MagicMock so tests can still assert on .save call history.
    """

    def __init__(self, data):
        self._data = dict(data) if data is not None else None
        self.save = MagicMock(side_effect=self._save)
        self.get = MagicMock(side_effect=self._get)

    def _save(self, data):
        self._data = dict(data)

    def _get(self, session_id, agent_id):
        return dict(self._data) if self._data is not None else None


class _Mem:
    """Records appended messages and answers the counting queries."""

    def __init__(self, messages=None):
        self.msgs: list[dict] = list(messages or [])

    def append_message(self, agent_id, role, content, session_id="", task_id=None,
                       tool_call_id=None, tool_calls=None):
        self.msgs.append({"agent_id": agent_id, "role": role, "content": content,
                          "task_id": task_id, "tool_call_id": tool_call_id,
                          "tool_calls": tool_calls})

    def get_all_messages(self, agent_id):
        return list(self.msgs)

    def count_messages(self, agent_id):
        return len(self.msgs)


def _loop(task=None, agent_data=None, session=None, result=None, verdict=None,
          observer_task=None, mem=None, tasks=None):
    """Build an AgentLoop with mocked collaborators; returns a handle."""
    task = task if task is not None else _task()
    store = {t.id: t for t in (tasks or [task])}

    session_svc = MagicMock()
    session_svc.get.return_value = session if session is not None else _session()

    task_svc = MagicMock()
    task_svc.get.side_effect = lambda tid, sid=None: store[tid]
    task_svc.list_by_agent.return_value = []
    task_svc.list_children.return_value = []
    task_svc.list_by_session.return_value = list(store.values())

    memory = mem if mem is not None else _Mem()
    bb = MagicMock()

    agents = _AgentStore(agent_data if agent_data is not None else _agent_dict())

    reasoner = MagicMock()
    reasoner.reason.return_value = ReasoningContext(
        goal="g", recent_messages=[], blackboard_snippets=[], current_task=task)

    actor = MagicMock()
    actor.act.return_value = result if result is not None else _result()
    actor._prompt_builder.build_initial_user_content.side_effect = \
        lambda t: f"[wrapped] {t.user_prompt}"

    observer = MagicMock()
    observer.observe.return_value = verdict if verdict is not None else \
        ObserverVerdict(summary="the summary")

    loop = AgentLoop(session_svc, task_svc, memory, bb, agents, reasoner, actor, observer)
    return SimpleNamespace(loop=loop, task=task, store=store, session_svc=session_svc,
                           task_svc=task_svc, mem=memory, bb=bb, agents=agents,
                           reasoner=reasoner, actor=actor, observer=observer)


@pytest.fixture(autouse=True)
def _clean_interrupts():
    InterruptRegistry.clear("s1")
    yield
    InterruptRegistry.clear("s1")


# ── happy path ────────────────────────────────────────────────────────────────

class TestRunHappyPath:
    def test_runs_all_three_phases(self):
        h = _loop(task=_task(status="FINISHED"))
        h.loop.run("s1", "a1", "t1")
        assert h.reasoner.reason.called
        assert h.actor.act.called
        assert h.observer.observe.called

    def test_agent_marked_running_then_finished(self):
        h = _loop(task=_task(status="FINISHED"))
        h.loop.run("s1", "a1", "t1")
        statuses = [c.args[0]["status"] for c in h.agents.save.call_args_list]
        assert statuses[0] == "RUNNING" and statuses[-1] == "FINISHED"

    def test_output_written_to_memory_on_terminal_round(self):
        h = _loop(task=_task(status="FINISHED"))
        h.loop.run("s1", "a1", "t1")
        assistant = [m for m in h.mem.msgs if m["role"] == "assistant"]
        assert assistant and assistant[-1]["content"] == "the output"

    def test_execution_round_appended(self):
        task = _task(status="FINISHED")
        h = _loop(task=task)
        h.loop.run("s1", "a1", "t1")
        assert len(task.execution_rounds) == 1
        assert task.execution_rounds[0]["process_report"] == "the summary"

    def test_blackboard_publishes_report_output_and_turns(self):
        task = _task(status="FINISHED", process_report="pr")
        h = _loop(task=task)
        h.loop.run("s1", "a1", "t1")
        published = [c.args for c in h.bb.publish.call_args_list]
        assert any("pr" in str(a) for a in published)
        assert any("_root" in a for a in published)

    def test_tool_calls_are_published_too(self):
        turns = [ConversationTurn(round=0, messages_sent=[], llm_text="t",
                                  tool_calls=[ToolCallRecord(tool_name="read",
                                                             arguments={}, result="r")])]
        h = _loop(task=_task(status="FINISHED"), result=_result(turns=turns))
        h.loop.run("s1", "a1", "t1")
        assert any("Tool call: read" in str(c.args) for c in h.bb.publish.call_args_list)


# ── guards and prompt persistence ─────────────────────────────────────────────

class TestGuards:
    def test_missing_agent_raises(self):
        h = _loop()
        h.agents.get.side_effect = lambda *a, **k: None
        with pytest.raises(AppError) as e:
            h.loop.run("s1", "ghost", "t1")
        assert e.value.code == "AGENT_NOT_FOUND"

    def test_token_budget_exceeded_raises(self):
        h = _loop(session=_session(token_budget=100, output_tokens_used=100))
        with pytest.raises(AppError) as e:
            h.loop.run("s1", "a1", "t1")
        assert e.value.code == "TOKEN_BUDGET_EXCEEDED"

    def test_budget_under_the_limit_runs(self):
        h = _loop(task=_task(status="FINISHED"),
                  session=_session(token_budget=100, output_tokens_used=50))
        h.loop.run("s1", "a1", "t1")
        assert h.actor.act.called

    def test_zero_budget_is_unlimited(self):
        h = _loop(task=_task(status="FINISHED"),
                  session=_session(token_budget=0, output_tokens_used=10**9))
        h.loop.run("s1", "a1", "t1")
        assert h.actor.act.called


class TestPromptPersistence:
    def test_user_prompt_written_once(self):
        task = _task(status="FINISHED", user_prompt_in_memory=False)
        h = _loop(task=task)
        h.loop.run("s1", "a1", "t1")
        user_msgs = [m for m in h.mem.msgs if m["role"] == "user"]
        assert user_msgs[0]["content"] == "[wrapped] do X"
        assert task.user_prompt_in_memory is True

    def test_already_persisted_prompt_is_not_rewritten(self):
        h = _loop(task=_task(status="FINISHED", user_prompt_in_memory=True))
        h.loop.run("s1", "a1", "t1")
        assert not any(m["role"] == "user" for m in h.mem.msgs)

    def test_daemon_task_prompt_is_not_persisted(self):
        h = _loop(task=_task(status="FINISHED", user_prompt_in_memory=False,
                             settings={"_daemon": True}))
        h.loop.run("s1", "a1", "t1")
        assert not any(m["role"] == "user" for m in h.mem.msgs)

    def test_empty_prompt_is_skipped(self):
        h = _loop(task=_task(status="FINISHED", user_prompt="", user_prompt_in_memory=False))
        h.loop.run("s1", "a1", "t1")
        assert not any(m["role"] == "user" for m in h.mem.msgs)

    def test_stale_pending_answer_is_cleared_up_front(self):
        task = _task(status="FINISHED", pending_user_answer="old answer")
        h = _loop(task=task)
        h.loop.run("s1", "a1", "t1")
        assert task.pending_user_answer is None

    def test_daemon_task_skips_tracking_injection(self):
        h = _loop(task=_task(status="FINISHED", settings={"_daemon": True}))
        with patch.object(AgentLoop, "_inject_tracking_updates") as inject:
            h.loop.run("s1", "a1", "t1")
        assert not inject.called

    def test_normal_task_injects_tracking(self):
        h = _loop(task=_task(status="FINISHED"))
        with patch.object(AgentLoop, "_inject_tracking_updates") as inject:
            h.loop.run("s1", "a1", "t1")
        assert inject.called


# ── actor phase ───────────────────────────────────────────────────────────────

class TestActorPhase:
    def test_stale_outputs_cleared_before_the_run(self):
        task = _task(status="FINISHED", outputs="previous output")
        h = _loop(task=task)
        h.loop.run("s1", "a1", "t1")
        assert task.outputs == "the output"      # replaced, not appended to

    def test_text_output_stored_on_the_task(self):
        task = _task(status="FINISHED")
        h = _loop(task=task, result=_result(output="fresh"))
        h.loop.run("s1", "a1", "t1")
        assert task.outputs == "fresh"

    def test_images_become_multimodal_outputs(self):
        img = SimpleNamespace(data="D", media_type="image/png", source_type="base64")
        turns = [ConversationTurn(round=0, messages_sent=[], llm_text="caption",
                                  images=[img])]
        task = _task(status="FINISHED")
        h = _loop(task=task, result=_result(output="caption", turns=turns))
        h.loop.run("s1", "a1", "t1")
        assert task.outputs[0]["type"] == "image"
        assert task.outputs[1] == {"type": "text", "text": "caption"}

    def test_images_without_text(self):
        img = SimpleNamespace(data="D", media_type="image/png", source_type="base64")
        turns = [ConversationTurn(round=0, messages_sent=[], llm_text="", images=[img])]
        task = _task(status="FINISHED")
        h = _loop(task=task, result=_result(output="", turns=turns))
        h.loop.run("s1", "a1", "t1")
        assert len(task.outputs) == 1 and task.outputs[0]["type"] == "image"

    def test_abnormal_exit_does_not_store_output(self):
        task = _task(status="FINISHED")
        h = _loop(task=task, result=_result(exit_reason="max_rounds"))
        h.loop.run("s1", "a1", "t1")
        assert task.outputs == ""

    def test_context_tokens_recorded_on_the_agent(self):
        h = _loop(task=_task(status="FINISHED"), result=_result(context_tokens=777))
        h.loop.run("s1", "a1", "t1")
        guards = [c.args[0]["loop_guard"]["context_tokens"]
                  for c in h.agents.save.call_args_list]
        assert 777 in guards

    def test_no_context_tokens_leaves_the_guard_alone(self):
        h = _loop(task=_task(status="FINISHED"), result=_result(context_tokens=0))
        h.loop.run("s1", "a1", "t1")
        guards = [c.args[0]["loop_guard"]["context_tokens"]
                  for c in h.agents.save.call_args_list]
        assert set(guards) == {0}


# ── suspension ────────────────────────────────────────────────────────────────

class TestSuspension:
    def _suspended(self):
        task = _task(status="SUSPENDED")
        call = ToolCallRecord(tool_name="submit_task", arguments={"title": "child"},
                              result="created", tool_call_id="tc1")
        h = _loop(task=task, result=_result(tool_calls=[call]))
        return h, task

    def test_observer_is_skipped(self):
        h, _ = self._suspended()
        h.loop.run("s1", "a1", "t1")
        assert not h.observer.observe.called

    def test_submit_tool_call_written_to_memory(self):
        h, _ = self._suspended()
        h.loop.run("s1", "a1", "t1")
        assistant = [m for m in h.mem.msgs if m["role"] == "assistant"][-1]
        assert assistant["tool_calls"][0]["id"] == "tc1"
        assert assistant["tool_calls"][0]["name"] == "submit_task"

    def test_round_is_recorded_before_the_submit_memory(self):
        h, task = self._suspended()
        h.loop.run("s1", "a1", "t1")
        assert len(task.execution_rounds) == 1
        assert task.execution_rounds[0]["mem_index"] == 0

    def test_agent_ends_up_waiting(self):
        h, _ = self._suspended()
        h.loop.run("s1", "a1", "t1")
        assert h.agents.save.call_args_list[-1].args[0]["status"] == "WAITING"

    def test_children_get_the_parent_tool_call_id(self):
        h, task = self._suspended()
        child = _task(id="c1", parent_task_id="t1")
        h.task_svc.list_children.return_value = [child]
        h.loop.run("s1", "a1", "t1")
        assert child.parent_tool_call_id == "tc1"

    def test_already_linked_children_are_not_overwritten(self):
        h, _ = self._suspended()
        child = _task(id="c1", parent_tool_call_id="earlier")
        h.task_svc.list_children.return_value = [child]
        h.loop.run("s1", "a1", "t1")
        assert child.parent_tool_call_id == "earlier"

    def test_backfill_failure_is_swallowed(self):
        h, _ = self._suspended()
        h.task_svc.list_children.side_effect = RuntimeError("store down")
        h.loop.run("s1", "a1", "t1")

    def test_suspension_without_a_submit_call_keeps_the_text(self):
        task = _task(status="SUSPENDED")
        h = _loop(task=task, result=_result(output="some text", tool_calls=[]))
        h.loop.run("s1", "a1", "t1")
        assistant = [m for m in h.mem.msgs if m["role"] == "assistant"]
        assert assistant[-1]["content"] == "some text"
        assert assistant[-1]["tool_calls"] is None

    def test_suspension_without_submit_or_text_writes_nothing(self):
        task = _task(status="SUSPENDED")
        h = _loop(task=task, result=_result(output="", tool_calls=[]))
        h.loop.run("s1", "a1", "t1")
        assert not any(m["role"] == "assistant" for m in h.mem.msgs)


# ── observer outcomes ─────────────────────────────────────────────────────────

class TestObserverOutcomes:
    def test_to_be_observed_transition_happens_first(self):
        h = _loop(task=_task(status="ACTIVE"))
        h.loop.run("s1", "a1", "t1")
        h.task_svc.to_be_observed.assert_called_once_with("t1", "s1")

    @pytest.mark.parametrize("status", ["TO_BE_OBSERVED", "FINISHED", "FAILED", "CANCELED"])
    def test_already_settled_task_is_not_transitioned(self, status):
        h = _loop(task=_task(status=status))
        try:
            h.loop.run("s1", "a1", "t1")
        except AppError:
            pass                      # FAILED raises, which is expected
        assert not h.task_svc.to_be_observed.called

    def test_failed_task_raises_for_the_caller(self):
        h = _loop(task=_task(status="FAILED", process_report="it broke"))
        with pytest.raises(AppError) as e:
            h.loop.run("s1", "a1", "t1")
        assert e.value.code == "TASK_FAILED_BY_OBSERVER"
        assert e.value.message == "it broke"

    def _requeued(self):
        """Observer reports the 'active' outcome: the task goes back to PENDING."""
        task = _task(status="ACTIVE")
        h = _loop(task=task)

        def observe(*a, **kw):
            task.status = "PENDING"
            return ObserverVerdict(summary="more to do")

        h.observer.observe.side_effect = observe
        return h, task

    def test_pending_task_returns_without_publishing(self):
        h, _ = self._requeued()
        h.loop.run("s1", "a1", "t1")
        assert not h.bb.publish.called

    def test_pending_round_does_not_write_output_to_memory(self):
        h, _ = self._requeued()
        h.loop.run("s1", "a1", "t1")
        assert not any(m["role"] == "assistant" for m in h.mem.msgs)

    def test_verdict_context_tokens_raise_the_guard(self):
        h = _loop(task=_task(status="FINISHED"),
                  verdict=ObserverVerdict(summary="s", context_tokens=4321))
        h.loop.run("s1", "a1", "t1")
        guards = [c.args[0]["loop_guard"]["context_tokens"]
                  for c in h.agents.save.call_args_list]
        assert 4321 in guards

    def test_task_list_is_passed_to_the_observer(self):
        h = _loop(task=_task(status="FINISHED"))
        h.task_svc.list_by_agent.return_value = [_task(id="other")]
        h.loop.run("s1", "a1", "t1")
        assert len(h.observer.observe.call_args.args[4]) == 1

    def test_multimodal_output_written_to_memory(self):
        task = _task(status="FINISHED")
        img = SimpleNamespace(data="D", media_type="image/png", source_type="base64")
        turns = [ConversationTurn(round=0, messages_sent=[], llm_text="cap", images=[img])]
        h = _loop(task=task, result=_result(output="cap", turns=turns))
        h.loop.run("s1", "a1", "t1")
        content = [m for m in h.mem.msgs if m["role"] == "assistant"][-1]["content"]
        assert content[0]["type"] == "image"
        assert content[-1]["text"] == "cap"

    def test_image_only_output_written_without_a_text_block(self):
        task = _task(status="FINISHED")
        img = SimpleNamespace(data="D", media_type="image/png", source_type="base64")
        turns = [ConversationTurn(round=0, messages_sent=[], llm_text="", images=[img])]
        h = _loop(task=task, result=_result(output="", turns=turns))
        h.loop.run("s1", "a1", "t1")
        content = [m for m in h.mem.msgs if m["role"] == "assistant"][-1]["content"]
        assert len(content) == 1 and content[0]["type"] == "image"

    def test_pending_answer_cleared_after_a_terminal_round(self):
        task = _task(status="FINISHED")

        def observe(*a, **kw):
            task.pending_user_answer = "the human said yes"
            return ObserverVerdict(summary="s")

        h = _loop(task=task)
        h.observer.observe.side_effect = observe
        h.loop.run("s1", "a1", "t1")
        assert task.pending_user_answer is None


# ── finally block ─────────────────────────────────────────────────────────────

class TestFinallyBlock:
    def test_non_running_agent_status_is_left_alone(self):
        """Something else parked the agent (e.g. WAITING) — the final sync must not run."""
        h = _loop(task=_task(status="FINISHED"))
        h.agents._data["status"] = "WAITING"
        h.agents.save = MagicMock()          # swallow writes so the reload stays WAITING
        h.loop.run("s1", "a1", "t1")
        statuses = [c.args[0]["status"] for c in h.agents.save.call_args_list]
        assert "FINISHED" not in statuses

    def test_task_reload_failure_defaults_to_finished(self):
        h = _loop(task=_task(status="FINISHED"))
        calls = {"n": 0}
        real = h.task_svc.get.side_effect

        def flaky(tid, sid=None):
            calls["n"] += 1
            if calls["n"] > 3:
                raise RuntimeError("store down")
            return real(tid, sid)

        h.task_svc.get.side_effect = flaky
        h.loop.run("s1", "a1", "t1")
        assert h.agents.save.call_args_list[-1].args[0]["status"] == "FINISHED"

    def test_status_synced_even_when_the_task_failed(self):
        h = _loop(task=_task(status="FAILED"))
        with pytest.raises(AppError):
            h.loop.run("s1", "a1", "t1")
        assert h.agents.save.call_args_list[-1].args[0]["status"] == "FINISHED"


# ── interrupts ────────────────────────────────────────────────────────────────

class TestInterrupts:
    def test_flag_set_before_the_actor_cancels_the_task(self):
        h = _loop()
        InterruptRegistry.set("s1")
        InterruptRegistry.get_or_create("s1").set()
        h.loop.run("s1", "a1", "t1")            # must not raise
        assert not h.actor.act.called
        h.task_svc.transition.assert_called_with("t1", "CANCELED", "s1")

    def test_snapshot_written_to_the_root_agent(self):
        h = _loop()
        InterruptRegistry.get_or_create("s1").set()
        h.loop.run("s1", "a1", "t1")
        snapshot = [m for m in h.mem.msgs if m["role"] == "assistant"][-1]
        assert snapshot["agent_id"] == "a1"
        assert "[Session interrupted by user]" in snapshot["content"]

    def test_daemon_task_ignores_the_interrupt_flag(self):
        h = _loop(task=_task(status="FINISHED", settings={"_daemon": True}))
        InterruptRegistry.get_or_create("s1").set()
        h.loop.run("s1", "a1", "t1")
        assert h.actor.act.called

    def test_actor_interrupt_is_handled_not_raised(self):
        h = _loop()
        h.actor.act.side_effect = AgentInterruptedError(
            "stopped", context=InterruptContext(partial_text="half a sentence"))
        h.loop.run("s1", "a1", "t1")
        snapshot = [m for m in h.mem.msgs if m["role"] == "assistant"][-1]["content"]
        assert "half a sentence" in snapshot

    def test_snapshot_lists_completed_tool_calls(self):
        h = _loop()
        ctx = InterruptContext(tool_calls=[
            ToolCallRecord(tool_name="read", arguments={}, result="r"),
            ToolCallRecord(tool_name="write", arguments={}, result="e", is_error=True)])
        h.actor.act.side_effect = AgentInterruptedError("stopped", context=ctx)
        h.loop.run("s1", "a1", "t1")
        snapshot = [m for m in h.mem.msgs if m["role"] == "assistant"][-1]["content"]
        assert "Tools called (2):" in snapshot
        assert "✓ read" in snapshot and "✗ write" in snapshot

    def test_only_active_or_pending_tasks_are_cancelled(self):
        h = _loop(task=_task(status="FINISHED"))
        h.actor.act.side_effect = AgentInterruptedError("stopped")
        h.loop.run("s1", "a1", "t1")
        assert not any(c.args[1:2] == ("CANCELED",)
                       for c in h.task_svc.transition.call_args_list)

    def test_daemon_task_is_not_cancelled(self):
        h = _loop(task=_task(settings={"_daemon": True}))
        h.loop._handle_interrupt("s1", "a1", "t1", h.task, None)
        assert not h.task_svc.transition.called

    def test_cancel_failure_is_swallowed(self):
        h = _loop()
        h.task_svc.transition.side_effect = RuntimeError("bad state")
        h.loop._handle_interrupt("s1", "a1", "t1", h.task, None)

    def test_snapshot_write_failure_is_swallowed(self):
        h = _loop()
        h.mem.append_message = MagicMock(side_effect=RuntimeError("mem down"))
        h.loop._handle_interrupt("s1", "a1", "t1", h.task, None)

    def test_root_agent_fallback_when_session_lookup_fails(self):
        h = _loop()
        h.session_svc.get.side_effect = RuntimeError("down")
        assert h.loop._get_root_agent_id("s1") is None


class TestBuildInterruptSnapshot:
    def _snapshot(self, h, task=None, context=None):
        return h.loop._build_interrupt_snapshot("s1", task, context)

    def test_includes_prompt_and_title(self):
        h = _loop()
        out = self._snapshot(h, _task(user_prompt="build a deck", title="Deck"))
        assert "Task prompt: build a deck" in out and "Task title: Deck" in out

    def test_long_prompt_is_truncated(self):
        h = _loop()
        out = self._snapshot(h, _task(user_prompt="x" * 600))
        assert "…" in out and len(out) < 900

    def test_multimodal_prompt_is_flattened(self):
        h = _loop()
        out = self._snapshot(h, _task(user_prompt=[{"type": "text", "text": "flat"}]))
        assert "Task prompt: flat" in out

    def test_daemon_task_details_are_omitted(self):
        h = _loop()
        out = self._snapshot(h, _task(user_prompt="secret", settings={"_daemon": True}))
        assert "secret" not in out

    def test_no_task_still_produces_a_snapshot(self):
        h = _loop()
        out = self._snapshot(h, None)
        assert out.startswith("[Session interrupted by user]")
        assert out.endswith("Awaiting new instructions.")

    def test_long_partial_output_is_truncated(self):
        h = _loop()
        out = self._snapshot(h, None, InterruptContext(partial_text="y" * 900))
        assert "[truncated]" in out

    def test_blank_partial_output_is_skipped(self):
        h = _loop()
        assert "Partial output" not in self._snapshot(h, None,
                                                      InterruptContext(partial_text="   "))

    def test_task_flow_lists_suspended_and_planned(self):
        parent = _task(id="p1", title="Parent", description="Parent work")
        child = _task(id="c1", title="Child", parent_task_id="p1")
        planned = _task(id="pl1", title="Planned work")
        h = _loop(tasks=[parent, child, planned])
        h.session_svc.get.return_value = _session(
            metadata={"_interrupted_task_ids": ["p1", "pl1"]})
        out = self._snapshot(h, None)
        assert "[Suspended] Parent work" in out
        assert "└─ waiting for: Child" in out
        assert "[Planned, not started] Planned work" in out

    def test_daemon_tasks_excluded_from_the_flow(self):
        d = _task(id="d1", title="Daemon", settings={"_daemon": True})
        h = _loop(tasks=[d])
        h.session_svc.get.return_value = _session(
            metadata={"_interrupted_task_ids": ["d1"]})
        assert "Daemon" not in self._snapshot(h, None)

    def test_no_interrupted_ids_means_no_flow_section(self):
        h = _loop()
        h.session_svc.get.return_value = _session(metadata={})
        assert "Task execution flow" not in self._snapshot(h, None)

    def test_flow_lookup_failure_is_swallowed(self):
        h = _loop()
        h.session_svc.get.side_effect = RuntimeError("down")
        assert "Awaiting new instructions." in self._snapshot(h, None)

    def test_unknown_interrupted_id_is_ignored(self):
        h = _loop()
        h.session_svc.get.return_value = _session(
            metadata={"_interrupted_task_ids": ["ghost"]})
        assert "Task execution flow" not in self._snapshot(h, None)

    def test_suspended_parent_without_titled_children(self):
        parent = _task(id="p1", title="Parent", description="")
        child = _task(id="c1", title="", parent_task_id="p1")
        h = _loop(tasks=[parent, child])
        h.session_svc.get.return_value = _session(
            metadata={"_interrupted_task_ids": ["p1"]})
        out = self._snapshot(h, None)
        assert "[Suspended] Parent" in out and "waiting for: c1" in out
