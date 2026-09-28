"""Tests for the domain services (app/domain/services/).

Stores are mocked, the state machines are real (so illegal transitions raise for
real), and the SSE bus is patched out so nothing touches the event log.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.common.errors import AppError
from app.domain.events.event_types import (
    SESSION_CANCELED, SESSION_CREATED, SESSION_FAILED, SESSION_INTERRUPTED,
    SESSION_STARTED, SESSION_SUCCEEDED, TASK_CREATED, TASK_FAILED, TASK_FINISHED,
    TASK_STARTED,
)
from app.domain.models.blackboard import BlackboardEntry
from app.domain.models.memory import MemorySummary
from app.domain.models.session import Session
from app.domain.models.task import Task
from app.domain.services.blackboard_service import BlackboardService
from app.domain.services.memory_service import MemoryService, PromptContext
from app.domain.services.session_service import SessionService
from app.domain.services.task_service import TaskService
from app.domain.state_machine import SessionStateMachine, TaskStateMachine


@pytest.fixture(autouse=True)
def _no_sse():
    bus = MagicMock()
    with patch("app.common.sse_bus.get_sse_bus", return_value=bus):
        yield bus


class _Bus:
    def __init__(self):
        self.published: list[tuple[str, dict]] = []

    def publish(self, topic, payload):
        self.published.append((topic, payload))

    def topics(self):
        return [t for t, _ in self.published]


# ── TaskService ───────────────────────────────────────────────────────────────

def _task(tid="t1", status="PENDING", **kw) -> Task:
    base = dict(id=tid, session_id="s1", creator_agent_id="c", assigned_agent_id="a1",
                status=status, user_prompt="p", title=tid, created_at="2026-01-01",
                updated_at="2026-01-01")
    base.update(kw)
    return Task(**base)


def _task_svc(tasks=None):
    store = MagicMock()
    by_id = {t.id: t.to_dict() for t in (tasks or [])}
    store.get.side_effect = lambda tid, sid=None: by_id.get(tid)
    store.list_by_session.return_value = list(by_id)
    store.save.side_effect = lambda d: by_id.__setitem__(d["id"], d)
    bus = _Bus()
    return TaskService(store, TaskStateMachine(), bus), store, bus


class TestTaskServiceCreate:
    def test_creates_a_pending_task_and_publishes(self):
        svc, store, bus = _task_svc()
        t = svc.create("s1", "creator", "do it", title="T", description="d")
        assert t.status == "PENDING" and t.session_id == "s1"
        assert t.creator_agent_id == "creator" and t.assigned_agent_id == "creator"
        assert store.save.called
        assert (TASK_CREATED, {"task_id": t.id, "session_id": "s1"}) in bus.published

    def test_explicit_assignee_overrides_the_creator(self):
        svc, _, _ = _task_svc()
        assert svc.create("s1", "creator", "p", assigned_agent_id="worker") \
            .assigned_agent_id == "worker"

    def test_inputs_dag_deps_and_parent_are_recorded(self):
        svc, _, _ = _task_svc()
        t = svc.create("s1", "c", "p", inputs={"skill_name": "x"},
                       parent_task_id="parent", dag_deps=["dep1"])
        assert t.settings == {"skill_name": "x"}
        assert t.parent_task_id == "parent" and t.dag_deps == ["dep1"]

    def test_defaults(self):
        svc, _, _ = _task_svc()
        t = svc.create("s1", "c", "p")
        assert t.settings == {} and t.dag_deps == [] and t.parent_task_id is None

    def test_daemon_input_selects_the_daemon_sse_event(self, _no_sse):
        svc, _, _ = _task_svc()
        svc.create("s1", "c", "p", inputs={"_daemon": True})
        assert _no_sse.push.call_args.args[1]["type"] == "daemon_task_created"

    def test_normal_task_uses_task_created(self, _no_sse):
        svc, _, _ = _task_svc()
        svc.create("s1", "c", "p")
        assert _no_sse.push.call_args.args[1]["type"] == "task_created"

    def test_sse_failure_is_swallowed(self, _no_sse):
        _no_sse.push.side_effect = RuntimeError("bus down")
        svc, _, _ = _task_svc()
        assert svc.create("s1", "c", "p").status == "PENDING"


class TestTaskServiceReadWrite:
    def test_get_returns_a_task(self):
        svc, _, _ = _task_svc([_task()])
        assert svc.get("t1").id == "t1"

    def test_get_missing_raises(self):
        svc, _, _ = _task_svc()
        with pytest.raises(AppError) as e:
            svc.get("ghost")
        assert e.value.code == "TASK_NOT_FOUND"

    def test_save_stamps_updated_at(self):
        svc, store, _ = _task_svc()
        t = _task()
        svc.save(t)
        assert t.updated_at != "2026-01-01"
        assert store.save.called


class TestTaskServiceTransitions:
    def test_legal_transition_publishes(self):
        svc, _, bus = _task_svc([_task(status="PENDING")])
        out = svc.transition("t1", "ACTIVE")
        assert out.status == "ACTIVE"
        assert TASK_STARTED in bus.topics()

    def test_illegal_transition_raises(self):
        svc, _, _ = _task_svc([_task(status="FINISHED")])
        with pytest.raises(AppError):
            svc.transition("t1", "ACTIVE")

    def test_extra_fields_are_written(self):
        svc, _, _ = _task_svc([_task(status="ACTIVE")])
        out = svc.transition("t1", "TO_BE_OBSERVED", process_report="pr",
                             task_output="out", error="err")
        assert out.process_report == "pr" and out.outputs == "out" and out.error == "err"

    def test_blank_extras_are_not_written(self):
        svc, _, _ = _task_svc([_task(status="ACTIVE", process_report="keep")])
        out = svc.transition("t1", "TO_BE_OBSERVED", process_report="")
        assert out.process_report == "keep"

    def test_non_event_status_publishes_nothing(self):
        svc, _, bus = _task_svc([_task(status="ACTIVE")])
        svc.transition("t1", "TO_BE_OBSERVED")
        assert bus.topics() == []

    def test_daemon_task_update_event(self, _no_sse):
        svc, _, _ = _task_svc([_task(status="PENDING", settings={"_daemon": True})])
        svc.transition("t1", "ACTIVE")
        assert _no_sse.push.call_args.args[1]["type"] == "daemon_task_updated"

    def test_sse_failure_is_swallowed(self, _no_sse):
        _no_sse.push.side_effect = RuntimeError("down")
        svc, _, _ = _task_svc([_task(status="PENDING")])
        assert svc.transition("t1", "ACTIVE").status == "ACTIVE"

    def test_finish_writes_report_and_outputs(self):
        svc, _, bus = _task_svc([_task(status="TO_BE_OBSERVED")])
        out = svc.finish("t1", process_report="pr", outputs="o")
        assert out.status == "FINISHED" and out.process_report == "pr" and out.outputs == "o"
        assert TASK_FINISHED in bus.topics()

    def test_fail_writes_the_error(self):
        svc, _, bus = _task_svc([_task(status="ACTIVE")])
        out = svc.fail("t1", error="boom")
        assert out.status == "FAILED" and out.error == "boom"
        assert TASK_FAILED in bus.topics()

    def test_to_be_observed(self):
        svc, _, _ = _task_svc([_task(status="ACTIVE")])
        assert svc.to_be_observed("t1").status == "TO_BE_OBSERVED"

    def test_reopen_clears_results_and_requeues(self):
        svc, _, bus = _task_svc([_task(status="FINISHED", process_report="old",
                                       outputs="old out")])
        out = svc.reopen("t1")
        assert out.status == "PENDING"
        assert out.process_report is None and out.outputs == ""
        assert TASK_CREATED in bus.topics()

    def test_retry_increments_and_clears_the_error(self):
        svc, _, _ = _task_svc([_task(status="FAILED", error="boom", retry_count=1)])
        out = svc.retry("t1")
        assert out.status == "PENDING" and out.retry_count == 2 and out.error is None

    def test_resume_requeues_a_suspended_task(self):
        svc, _, _ = _task_svc([_task(status="SUSPENDED")])
        assert svc.resume("t1").status == "PENDING"


class TestTaskServiceQueries:
    def test_list_by_session(self):
        svc, _, _ = _task_svc([_task("t1"), _task("t2")])
        assert {t.id for t in svc.list_by_session("s1")} == {"t1", "t2"}

    def test_list_by_session_skips_unreadable(self):
        svc, store, _ = _task_svc([_task("t1")])
        store.get.side_effect = lambda tid, sid=None: None
        assert svc.list_by_session("s1") == []

    def test_list_by_agent(self):
        svc, _, _ = _task_svc([_task("t1", assigned_agent_id="a1"),
                               _task("t2", assigned_agent_id="a2")])
        assert [t.id for t in svc.list_by_agent("s1", "a1")] == ["t1"]

    def test_list_children(self):
        svc, _, _ = _task_svc([_task("c1", parent_task_id="p"), _task("other")])
        assert [t.id for t in svc.list_children("p", "s1")] == ["c1"]

    def test_list_pending_is_sorted_by_created_at(self):
        svc, _, _ = _task_svc([
            _task("late", created_at="2026-02-01"),
            _task("early", created_at="2026-01-01"),
            _task("done", status="FINISHED"),
        ])
        assert [t.id for t in svc.list_pending("s1")] == ["early", "late"]

    def test_cancel_pending_returns_the_count(self):
        svc, _, _ = _task_svc([_task("t1"), _task("t2"), _task("t3", status="ACTIVE")])
        assert svc.cancel_pending("s1") == 2

    def test_cancel_pending_with_nothing_to_do(self):
        svc, _, _ = _task_svc([_task("t1", status="FINISHED")])
        assert svc.cancel_pending("s1") == 0


# ── SessionService ────────────────────────────────────────────────────────────

def _session_dict(sid="s1", status="QUEUED", **kw) -> dict:
    s = Session(id=sid, user_prompt="p", goal="g", status=status,
                template_id=None, root_agent_id=None, **kw)
    return s.to_dict()


def _session_svc(sessions=None):
    store = MagicMock()
    by_id = {d["id"]: d for d in (sessions or [])}
    store.get.side_effect = lambda sid: by_id.get(sid)
    store.list_ids.return_value = list(by_id)
    store.save.side_effect = lambda d: by_id.__setitem__(d["id"], d)
    bus = _Bus()
    return SessionService(store, SessionStateMachine(), bus, MagicMock()), store, bus


class TestSessionServiceCreate:
    def test_creates_a_queued_session_with_a_queue(self):
        svc, store, bus = _session_svc()
        s = svc.create("build a deck", token_budget=500, llm_provider="p",
                       llm_model="m", working_dir="/w")
        assert s.status == "QUEUED" and s.goal == "build a deck"
        assert s.token_budget == 500 and s.working_dir == "/w"
        assert s.task_queue is not None
        assert SESSION_CREATED in bus.topics()
        assert store.save.called

    def test_defaults(self):
        svc, _, _ = _session_svc()
        s = svc.create("p")
        assert s.token_budget == 200_000 and s.template_id is None


class TestSessionServiceGet:
    def test_rehydrates_an_empty_queue(self):
        svc, _, _ = _session_svc([_session_dict()])
        assert svc.get("s1").task_queue is not None

    def test_rehydrates_a_persisted_queue(self):
        data = _session_dict()
        data["task_queue"] = {"ready": ["t1"], "blocked": []}
        svc, _, _ = _session_svc([data])
        assert svc.get("s1").task_queue.to_dict()["ready"] == ["t1"]

    def test_missing_raises(self):
        svc, _, _ = _session_svc()
        with pytest.raises(AppError) as e:
            svc.get("ghost")
        assert e.value.code == "SESSION_NOT_FOUND"

    def test_save_stamps_updated_at(self):
        svc, store, _ = _session_svc([_session_dict()])
        s = svc.get("s1")
        svc.save(s)
        assert store.save.called and s.updated_at


class TestSessionServiceTransitions:
    @pytest.mark.parametrize("to_status,event", [
        ("RUNNING", SESSION_STARTED),
    ])
    def test_legal_transition_publishes(self, to_status, event):
        svc, _, bus = _session_svc([_session_dict(status="QUEUED")])
        assert svc.transition("s1", to_status).status == to_status
        assert event in bus.topics()

    @pytest.mark.parametrize("to_status,event", [
        ("SUCCEEDED", SESSION_SUCCEEDED),
        ("FAILED", SESSION_FAILED),
        ("CANCELED", SESSION_CANCELED),
    ])
    def test_terminal_transitions(self, to_status, event):
        svc, _, bus = _session_svc([_session_dict(status="RUNNING")])
        svc.transition("s1", to_status)
        assert event in bus.topics()

    def test_terminal_transition_also_pushes_done(self, _no_sse):
        svc, _, _ = _session_svc([_session_dict(status="RUNNING")])
        svc.transition("s1", "SUCCEEDED")
        types = [c.args[1]["type"] for c in _no_sse.push.call_args_list]
        assert "session_update" in types and "done" in types

    def test_non_terminal_transition_does_not_push_done(self, _no_sse):
        svc, _, _ = _session_svc([_session_dict(status="QUEUED")])
        svc.transition("s1", "RUNNING")
        types = [c.args[1]["type"] for c in _no_sse.push.call_args_list]
        assert "done" not in types

    def test_illegal_transition_raises(self):
        svc, _, _ = _session_svc([_session_dict(status="SUCCEEDED")])
        with pytest.raises(AppError):
            svc.transition("s1", "RUNNING")

    def test_sse_failure_is_swallowed(self, _no_sse):
        _no_sse.push.side_effect = RuntimeError("down")
        svc, _, _ = _session_svc([_session_dict(status="QUEUED")])
        assert svc.transition("s1", "RUNNING").status == "RUNNING"

    def test_set_root_agent(self):
        svc, _, _ = _session_svc([_session_dict()])
        svc.set_root_agent("s1", "agent-9")
        assert svc.get("s1").root_agent_id == "agent-9"


class TestAddTokens:
    def test_accumulates_both_counters(self):
        svc, _, _ = _session_svc([_session_dict(input_tokens_used=10,
                                                output_tokens_used=5)])
        out = svc.add_tokens("s1", input_tokens=3, output_tokens=2)
        assert out.input_tokens_used == 13 and out.output_tokens_used == 7

    def test_missing_session_raises(self):
        svc, _, _ = _session_svc()
        with pytest.raises(AppError) as e:
            svc.add_tokens("ghost", input_tokens=1)
        assert e.value.code == "SESSION_NOT_FOUND"

    def test_budget_exhaustion_raises(self):
        svc, _, _ = _session_svc([_session_dict(token_budget=10, output_tokens_used=8)])
        with pytest.raises(AppError) as e:
            svc.add_tokens("s1", output_tokens=5)
        assert e.value.code == "TOKEN_BUDGET_EXCEEDED"

    def test_zero_budget_never_raises(self):
        svc, _, _ = _session_svc([_session_dict(token_budget=0)])
        assert svc.add_tokens("s1", output_tokens=10**6).output_tokens_used == 10**6

    def test_pushes_a_token_update(self, _no_sse):
        svc, _, _ = _session_svc([_session_dict()])
        svc.add_tokens("s1", input_tokens=1, output_tokens=2, context_tokens=99)
        ev = _no_sse.push.call_args.args[1]
        assert ev["type"] == "token_update" and ev["context_tokens"] == 99

    def test_sse_failure_is_swallowed(self, _no_sse):
        _no_sse.push.side_effect = RuntimeError("down")
        svc, _, _ = _session_svc([_session_dict()])
        assert svc.add_tokens("s1", input_tokens=1).input_tokens_used == 1


class TestSetGoal:
    def test_updates_the_goal_and_pushes(self, _no_sse):
        svc, _, _ = _session_svc([_session_dict()])
        svc.set_goal("s1", "the new goal")
        assert svc.get("s1").goal == "the new goal"
        assert _no_sse.push.call_args.args[1]["type"] == "session_goal_updated"

    def test_missing_session_is_a_noop(self):
        svc, store, _ = _session_svc()
        svc.set_goal("ghost", "g")
        assert not store.save.called

    def test_sse_failure_is_swallowed(self, _no_sse):
        _no_sse.push.side_effect = RuntimeError("down")
        svc, _, _ = _session_svc([_session_dict()])
        svc.set_goal("s1", "g")


class TestSessionServiceMisc:
    def test_list_ids(self):
        svc, _, _ = _session_svc([_session_dict("a"), _session_dict("b")])
        assert sorted(svc.list_ids()) == ["a", "b"]

    def test_delete_delegates(self):
        svc, store, _ = _session_svc([_session_dict()])
        svc.delete("s1")
        store.delete.assert_called_once_with("s1")

    def test_working_dir_in_use_by_another_session(self):
        svc, _, _ = _session_svc([_session_dict("s1", working_dir="/w"),
                                  _session_dict("s2", working_dir="/w")])
        assert svc.is_working_dir_in_use("/w", exclude_session_id="s1") is True

    def test_working_dir_only_used_by_the_excluded_session(self):
        svc, _, _ = _session_svc([_session_dict("s1", working_dir="/w")])
        assert svc.is_working_dir_in_use("/w", exclude_session_id="s1") is False

    def test_different_working_dirs(self):
        svc, _, _ = _session_svc([_session_dict("s1", working_dir="/a"),
                                  _session_dict("s2", working_dir="/b")])
        assert svc.is_working_dir_in_use("/a", exclude_session_id="s1") is False

    def test_unreadable_session_is_skipped(self):
        svc, store, _ = _session_svc([_session_dict("s2", working_dir="/w")])
        store.get.side_effect = lambda sid: None
        assert svc.is_working_dir_in_use("/w", exclude_session_id="s1") is False


# ── BlackboardService ─────────────────────────────────────────────────────────

class TestBlackboardService:
    def _svc(self):
        store = MagicMock()
        store.read_all.return_value = []
        store.read_since.return_value = []
        store.list_topics.return_value = []
        return BlackboardService(store), store

    def test_publish_appends_an_entry(self):
        svc, store = self._svc()
        entry = svc.publish("s1", "topic", "publisher", "the content")
        assert isinstance(entry, BlackboardEntry)
        assert entry.topic == "topic" and entry.publisher_id == "publisher"
        assert entry.content == "the content" and entry.id
        assert store.append.call_args.args[:2] == ("s1", "topic")

    def test_publish_accepts_multimodal_content(self):
        svc, _ = self._svc()
        blocks = [{"type": "text", "text": "x"}]
        assert svc.publish("s1", "t", "p", blocks).content == blocks

    def test_pull_advances_the_cursor(self):
        svc, store = self._svc()
        first = svc.publish("s1", "t", "p", "a").to_dict()
        second = svc.publish("s1", "t", "p", "b").to_dict()
        store.read_since.side_effect = [[first, second], []]
        assert [e.content for e in svc.pull("s1", "t", "a1")] == ["a", "b"]
        assert svc.pull("s1", "t", "a1") == []
        assert store.read_since.call_args_list[1].args[2] == 2

    def test_cursors_are_per_agent(self):
        svc, store = self._svc()
        entry = svc.publish("s1", "t", "p", "a").to_dict()
        store.read_since.return_value = [entry]
        assert len(svc.pull("s1", "t", "a1")) == 1
        assert len(svc.pull("s1", "t", "a2")) == 1
        assert store.read_since.call_args.args[2] == 0

    def test_subscribe_reads_everything(self):
        svc, store = self._svc()
        entry = svc.publish("s1", "t", "p", "a").to_dict()
        store.read_all.return_value = [entry]
        assert [e.content for e in svc.subscribe("s1", "t")] == ["a"]

    def test_list_topics_delegates(self):
        svc, store = self._svc()
        store.list_topics.return_value = ["alpha"]
        assert svc.list_topics("s1") == ["alpha"]


# ── MemoryService ─────────────────────────────────────────────────────────────

class TestMemoryService:
    def _svc(self, count=0, summary=None):
        store = MagicMock()
        store.read_messages.return_value = []
        store.read_window.return_value = []
        store.count_messages.return_value = count
        store.get_summary.return_value = summary
        return MemoryService(store), store

    def test_append_message_builds_an_item(self):
        svc, store = self._svc()
        item = svc.append_message("a1", "user", "hi", session_id="s1", task_id="t1",
                                  tool_call_id="tc", tool_calls=[{"id": "tc"}])
        assert item.agent_id == "a1" and item.role == "user" and item.content == "hi"
        assert item.task_id == "t1" and item.tool_call_id == "tc"
        assert store.append_message.call_args.args[0] == "a1"

    def test_get_all_messages_delegates(self):
        svc, store = self._svc()
        store.read_messages.return_value = [{"role": "user"}]
        assert svc.get_all_messages("a1") == [{"role": "user"}]

    def test_get_window_uses_the_configured_default(self):
        svc, store = self._svc()
        with patch("app.domain.services.memory_service.get_settings",
                   return_value=SimpleNamespace(default_short_window_size=7)):
            svc.get_window("a1")
        assert store.read_window.call_args.args[1] == 7

    def test_get_window_explicit_n(self):
        svc, store = self._svc()
        svc.get_window("a1", n=3)
        assert store.read_window.call_args.args[1] == 3

    def test_get_summary_none(self):
        svc, _ = self._svc(summary=None)
        assert svc.get_summary("a1") is None

    def test_get_summary_roundtrip(self):
        s = MemorySummary(session_id="s1", agent_id="a1", summary_text="so far",
                          covered_up_to=3, created_at="2026-01-01")
        svc, _ = self._svc(summary=s.to_dict())
        assert svc.get_summary("a1").summary_text == "so far"

    def test_save_summary_delegates(self):
        svc, store = self._svc()
        s = MemorySummary(session_id="s1", agent_id="a1", summary_text="t",
                          covered_up_to=1, created_at="")
        svc.save_summary("a1", s)
        assert store.save_summary.call_args.args[0] == "a1"

    def test_should_summarize_on_context_pressure(self):
        svc, _ = self._svc()
        assert svc.should_summarize("a1", context_tokens=800, context_limit=1000) is True

    def test_context_below_the_threshold_does_not_trigger(self):
        svc, _ = self._svc(count=0)
        with patch("app.domain.services.memory_service.get_settings",
                   return_value=SimpleNamespace(default_summary_threshold=20)):
            assert svc.should_summarize("a1", context_tokens=100, context_limit=1000) is False

    def test_should_summarize_on_message_count(self):
        svc, _ = self._svc(count=25)
        assert svc.should_summarize("a1", threshold=20) is True

    def test_covered_messages_are_discounted(self):
        s = MemorySummary(session_id="s1", agent_id="a1", summary_text="",
                          covered_up_to=20, created_at="")
        svc, _ = self._svc(count=25, summary=s.to_dict())
        assert svc.should_summarize("a1", threshold=20) is False

    def test_threshold_defaults_from_settings(self):
        svc, _ = self._svc(count=5)
        with patch("app.domain.services.memory_service.get_settings",
                   return_value=SimpleNamespace(default_summary_threshold=3)):
            assert svc.should_summarize("a1") is True

    def test_zero_context_limit_skips_that_check(self):
        svc, _ = self._svc(count=0)
        assert svc.should_summarize("a1", threshold=5, context_tokens=10**6,
                                    context_limit=0) is False

    def test_passthrough_helpers(self):
        svc, store = self._svc(count=4)
        assert svc.count_messages("a1") == 4
        svc.bulk_write_messages("a1", [{"role": "user"}])
        svc.rewrite_messages("a1", [{"role": "user"}])
        svc.delete_agent("a1")
        assert store.bulk_write_messages.called and store.rewrite_messages.called
        assert store.delete_agent.called


class TestPromptContext:
    def test_defaults(self):
        pc = PromptContext(system_prompt="s", goal="g", task_description="d")
        assert pc.blackboard_snippets == [] and pc.recent_messages == []
        assert pc.summary_text == "" and pc.token_estimate == 0
