"""Tests for TaskManager (app/orchestrator/task_manager.py).

Focuses on the event handlers and the dispatch/queue/cascade helpers. The batch
enqueue ordering, orphan reconciliation and idempotent-suspend paths already have
their own coverage in test_task_queue_batch.py, and the tool_result flush paths in
test_flush_children_as_tool_result.py / test_cascade_fail_closes_tool_result.py.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.domain.events.event_types import (
    TASK_CREATED, TASK_EXECUTION_FAILED, TASK_EXECUTION_FINISHED,
)
from app.domain.models.task import Task
from app.orchestrator.task_manager import TaskManager


def _task(tid="t1", **kw) -> Task:
    base = dict(id=tid, session_id="s1", creator_agent_id="creator", assigned_agent_id="a1",
                status="PENDING", user_prompt="", title=tid, created_at="", updated_at="")
    base.update(kw)
    return Task(**base)


class _Queue:
    """Minimal stand-in for TaskQueue that records the calls TaskManager makes."""

    def __init__(self, ready=None, blocked=None):
        self.ready = list(ready or [])
        self.blocked = list(blocked or [])
        self.notified: list[str] = []
        self.removed: list[str] = []
        self.cleared = 0
        self.popped: list[str] = []
        self.pop_returns: list[Task | None] = []

    def push(self, task_id):
        if task_id not in self.ready:
            self.ready.append(task_id)

    def pop(self):
        if self.pop_returns:
            return self.pop_returns.pop(0)
        if not self.ready:
            return None
        tid = self.ready.pop()
        self.popped.append(tid)
        return _task(tid)

    def notify_completed(self, tid):
        self.notified.append(tid)

    def remove(self, tid):
        self.removed.append(tid)
        if tid in self.ready:
            self.ready.remove(tid)

    def clear(self):
        self.cleared += 1
        self.ready.clear()
        self.blocked.clear()

    def is_empty(self):
        return not self.ready and not self.blocked

    def to_dict(self):
        return {"ready": list(self.ready), "blocked": list(self.blocked)}


def _session(status="RUNNING", queue=None, **kw):
    base = dict(id="s1", user_prompt="p", goal="g", status=status,
                failure_counter=0, failure_threshold=3, active_tasks=[],
                task_queue=queue if queue is not None else _Queue())
    base.update(kw)
    return SimpleNamespace(**base)


def _tm(session=None, tasks=None, lm=None, memory=None, agents=None,
        max_retries=3, bus=None):
    session = session if session is not None else _session()
    session_svc = MagicMock()
    session_svc.get.return_value = session
    task_svc = MagicMock()
    store = {t.id: t for t in (tasks or [])}
    task_svc.get.side_effect = lambda tid, sid=None: store[tid]
    task_svc.list_by_session.return_value = list(store.values())
    task_svc.list_children.return_value = []
    task_svc.create.side_effect = lambda **kw: _task("meta-1", title=kw.get("title", ""))
    tm = TaskManager(
        task_svc=task_svc, session_svc=session_svc,
        lifecycle_manager=lm if lm is not None else MagicMock(),
        event_bus=bus, max_task_retries=max_retries,
        memory_svc=memory, agent_store=agents,
    )
    return SimpleNamespace(tm=tm, session=session, session_svc=session_svc,
                           task_svc=task_svc, lm=tm._lm, store=store)


# ── wiring / session lifecycle ────────────────────────────────────────────────

class TestWiring:
    def test_subscribes_to_the_three_events(self):
        bus = MagicMock()
        _tm(bus=bus)
        topics = [c.args[0] for c in bus.subscribe.call_args_list]
        assert topics == [TASK_CREATED, TASK_EXECUTION_FINISHED, TASK_EXECUTION_FAILED]

    def test_no_bus_means_no_subscription(self):
        _tm(bus=None)          # must not raise

    def test_init_session_is_a_noop(self):
        h = _tm()
        h.tm.init_session("s1")

    def test_cleanup_session_drops_lock_and_pending(self):
        h = _tm()
        h.tm._get_lock("s1")
        h.tm.on_task_created(TASK_CREATED, {"task_id": "t1", "session_id": "s1"})
        h.tm.cleanup_session("s1")
        assert "s1" not in h.tm._session_locks
        assert "s1" not in h.tm._pending

    def test_get_lock_is_stable_per_session(self):
        h = _tm()
        first = h.tm._get_lock("s1")
        assert h.tm._get_lock("s1") is first
        assert h.tm._get_lock("s2") is not first


class TestOnTaskCreated:
    def test_buffers_the_task(self):
        h = _tm()
        h.tm.on_task_created(TASK_CREATED, {"task_id": "t9", "session_id": "s1"})
        assert h.tm._pending["s1"] == ["t9"]

    def test_missing_ids_are_ignored(self):
        h = _tm()
        h.tm.on_task_created(TASK_CREATED, {"task_id": "", "session_id": "s1"})
        h.tm.on_task_created(TASK_CREATED, {"task_id": "t", "session_id": ""})
        h.tm.on_task_created(TASK_CREATED, {})
        assert h.tm._pending == {}


# ── start_session ─────────────────────────────────────────────────────────────

class TestStartSession:
    def test_activates_a_queued_session_and_dispatches(self):
        q = _Queue(ready=["t1"])
        h = _tm(session=_session(status="QUEUED", queue=q), tasks=[_task("t1")])
        h.tm.start_session("s1", "a1")
        h.session_svc.transition.assert_called_once_with("s1", "RUNNING")
        assert h.lm.prepare_executor.called

    def test_running_session_is_not_re_transitioned(self):
        q = _Queue(ready=["t1"])
        h = _tm(session=_session(status="RUNNING", queue=q), tasks=[_task("t1")])
        h.tm.start_session("s1", "a1")
        assert not h.session_svc.transition.called

    def test_without_lifecycle_manager_bails(self):
        h = _tm(lm=None)
        h.tm._lm = None
        h.tm.start_session("s1", "a1")
        assert not h.session_svc.transition.called

    def test_session_load_failure_bails(self):
        h = _tm()
        h.session_svc.get.side_effect = RuntimeError("store down")
        h.tm.start_session("s1", "a1")
        assert not h.lm.prepare_executor.called

    def test_no_ready_task_does_nothing(self):
        h = _tm(session=_session(queue=_Queue()))
        h.tm.start_session("s1", "a1")
        assert not h.lm.prepare_executor.called
        assert not h.lm.release.called


# ── daemon tasks ──────────────────────────────────────────────────────────────

class TestSpawnDaemonTask:
    def test_pre_activates_and_spawns(self):
        task = _task("d1", settings={"subagent_template": "worker", "inherit_memory": True})
        h = _tm(tasks=[task])
        h.tm.spawn_daemon_task("s1", "parent-agent", "d1")
        h.task_svc.transition.assert_called_once_with("d1", "ACTIVE", "s1")
        kw = h.lm.spawn_daemon_agent.call_args.kwargs
        assert kw["template_name"] == "worker" and kw["inherit_memory"] is True
        assert kw["parent_agent_id"] == "parent-agent"

    def test_inherit_memory_defaults_false(self):
        h = _tm(tasks=[_task("d1", settings={})])
        h.tm.spawn_daemon_task("s1", "p", "d1")
        assert h.lm.spawn_daemon_agent.call_args.kwargs["inherit_memory"] is False

    def test_activation_failure_does_not_stop_the_spawn(self):
        h = _tm(tasks=[_task("d1", settings={})])
        h.task_svc.transition.side_effect = RuntimeError("bad transition")
        h.tm.spawn_daemon_task("s1", "p", "d1")
        assert h.lm.spawn_daemon_agent.called

    def test_load_failure_aborts(self):
        h = _tm()
        h.task_svc.get.side_effect = RuntimeError("missing")
        h.tm.spawn_daemon_task("s1", "p", "d1")
        assert not h.lm.spawn_daemon_agent.called

    def test_without_lifecycle_manager_bails(self):
        h = _tm()
        h.tm._lm = None
        h.tm.spawn_daemon_task("s1", "p", "d1")
        assert not h.task_svc.transition.called


class TestSpawnMetadataFiller:
    def test_creates_a_daemon_task_with_the_target_id(self):
        h = _tm(tasks=[_task("meta-1", settings={})])
        h.session.goal = "g"
        h.session.user_prompt = "g"          # goal == prompt -> not yet set
        with patch.object(TaskManager, "spawn_daemon_task") as spawn:
            h.tm.spawn_metadata_filler("s1", "a1", "build a deck", "target-9")
        kw = h.task_svc.create.call_args.kwargs
        assert kw["inputs"]["target_task_id"] == "target-9"
        assert kw["inputs"]["_daemon"] is True
        assert kw["inputs"]["subagent_template"] == "metadata_filler"
        assert "判断 session goal" in kw["description"]
        assert spawn.called

    def test_existing_goal_is_quoted_in_the_description(self):
        h = _tm(tasks=[_task("meta-1", settings={})])
        h.session.goal = "already decided"
        h.session.user_prompt = "the raw prompt"
        with patch.object(TaskManager, "spawn_daemon_task"):
            h.tm.spawn_metadata_filler("s1", "a1", "prompt", "target-9")
        assert "already decided" in h.task_svc.create.call_args.kwargs["description"]

    def test_session_load_failure_is_tolerated(self):
        h = _tm(tasks=[_task("meta-1", settings={})])
        h.session_svc.get.side_effect = RuntimeError("down")
        with patch.object(TaskManager, "spawn_daemon_task"):
            h.tm.spawn_metadata_filler("s1", "a1", "prompt", "target-9")
        assert h.task_svc.create.called

    def test_without_task_service_bails(self):
        h = _tm()
        h.tm._task_svc = None
        with patch.object(TaskManager, "spawn_daemon_task") as spawn:
            h.tm.spawn_metadata_filler("s1", "a1", "p", "t")
        assert not spawn.called

    def test_multimodal_prompt_is_flattened(self):
        h = _tm(tasks=[_task("meta-1", settings={})])
        h.session.goal = h.session.user_prompt = "g"
        with patch.object(TaskManager, "spawn_daemon_task"):
            h.tm.spawn_metadata_filler(
                "s1", "a1", [{"type": "text", "text": "flat prompt"}], "target")
        assert "flat prompt" in h.task_svc.create.call_args.kwargs["description"]


# ── on_task_finished ──────────────────────────────────────────────────────────

class TestOnTaskFinished:
    def _payload(self, tid="t1"):
        return {"session_id": "s1", "agent_id": "a1", "task_id": tid}

    def test_dispatches_the_next_ready_task(self):
        q = _Queue(ready=["t2"])
        h = _tm(session=_session(queue=q),
                tasks=[_task("t1", status="FINISHED"), _task("t2")])
        h.tm.on_task_finished(TASK_EXECUTION_FINISHED, self._payload())
        assert q.notified == ["t1"]
        assert h.lm.prepare_executor.called

    def test_non_running_session_just_releases(self):
        h = _tm(session=_session(status="SUCCEEDED"), tasks=[_task("t1")])
        h.tm.on_task_finished(TASK_EXECUTION_FINISHED, self._payload())
        h.lm.release.assert_called_once_with("s1", "a1")
        assert not h.lm.prepare_executor.called

    def test_session_load_failure_releases(self):
        h = _tm()
        h.session_svc.get.side_effect = RuntimeError("down")
        h.tm.on_task_finished(TASK_EXECUTION_FINISHED, self._payload())
        h.lm.release.assert_called_once_with("s1", "a1")

    def test_finished_task_removed_from_active_tasks(self):
        session = _session(active_tasks=["t1", "other"], queue=_Queue(ready=["t2"]))
        h = _tm(session=session, tasks=[_task("t1", status="FINISHED"), _task("t2")])
        h.tm.on_task_finished(TASK_EXECUTION_FINISHED, self._payload())
        # t1 is dropped; the newly dispatched t2 is appended by _dispatch_next
        assert "t1" not in session.active_tasks
        assert "other" in session.active_tasks

    def test_still_pending_task_is_requeued(self):
        q = _Queue()
        h = _tm(session=_session(queue=q), tasks=[_task("t1", status="PENDING")])
        h.tm.on_task_finished(TASK_EXECUTION_FINISHED, self._payload())
        # pushed back, then immediately popped again for the next turn
        assert q.popped == ["t1"]

    def test_requeue_check_failure_is_swallowed(self):
        q = _Queue(ready=["t2"])
        h = _tm(session=_session(queue=q), tasks=[_task("t2")])
        h.task_svc.get.side_effect = RuntimeError("missing")
        h.tm.on_task_finished(TASK_EXECUTION_FINISHED, self._payload())
        assert h.lm.prepare_executor.called

    def test_empty_queue_with_running_agents_defers_success(self):
        lm = MagicMock()
        lm.running_agent_count.return_value = 2
        h = _tm(session=_session(queue=_Queue()), tasks=[_task("t1", status="FINISHED")], lm=lm)
        h.tm.on_task_finished(TASK_EXECUTION_FINISHED, self._payload())
        assert not any(c.args == ("s1", "SUCCEEDED")
                       for c in h.session_svc.transition.call_args_list)

    def test_empty_queue_and_no_agents_succeeds(self):
        lm = MagicMock()
        lm.running_agent_count.return_value = 0
        h = _tm(session=_session(queue=_Queue()), tasks=[_task("t1", status="FINISHED")], lm=lm)
        h.tm.on_task_finished(TASK_EXECUTION_FINISHED, self._payload())
        h.session_svc.transition.assert_called_once_with("s1", "SUCCEEDED")
        lm.release.assert_called_once_with("s1", "a1")
        assert "s1" not in h.tm._session_locks       # cleanup ran

    def test_success_transition_failure_is_swallowed(self):
        lm = MagicMock()
        lm.running_agent_count.return_value = 0
        h = _tm(session=_session(queue=_Queue()), tasks=[_task("t1", status="FINISHED")], lm=lm)
        h.session_svc.transition.side_effect = RuntimeError("bad state")
        h.tm.on_task_finished(TASK_EXECUTION_FINISHED, self._payload())
        assert lm.release.called

    def test_blocked_tasks_keep_the_session_alive(self):
        q = _Queue(blocked=["t3"])
        lm = MagicMock()
        h = _tm(session=_session(queue=q), tasks=[_task("t1", status="FINISHED")], lm=lm)
        h.tm.on_task_finished(TASK_EXECUTION_FINISHED, self._payload())
        assert not any(c.args == ("s1", "SUCCEEDED")
                       for c in h.session_svc.transition.call_args_list)

    def test_success_resets_the_failure_counter(self):
        session = _session(failure_counter=2, queue=_Queue(ready=["t2"]))
        h = _tm(session=session, tasks=[_task("t1", status="FINISHED"), _task("t2")])
        h.tm.on_task_finished(TASK_EXECUTION_FINISHED, self._payload())
        assert session.failure_counter == 0

    def test_no_task_id_still_works(self):
        h = _tm(session=_session(queue=_Queue(ready=["t2"])), tasks=[_task("t2")])
        h.tm.on_task_finished(TASK_EXECUTION_FINISHED,
                              {"session_id": "s1", "agent_id": "a1", "task_id": ""})
        assert h.lm.prepare_executor.called


# ── on_task_failed ────────────────────────────────────────────────────────────

class TestOnTaskFailed:
    def _payload(self, tid="t1", **kw):
        return {"session_id": "s1", "agent_id": "a1", "task_id": tid, **kw}

    def test_retries_below_the_limit(self):
        q = _Queue()
        task = _task("t1", status="FAILED", retry_count=0)
        h = _tm(session=_session(queue=q), tasks=[task], max_retries=3)
        h.tm.on_task_failed(TASK_EXECUTION_FAILED, self._payload())
        h.task_svc.retry.assert_called_once_with("t1", "s1")
        assert q.popped == ["t1"]        # pushed back, then popped for the retry turn

    def test_marks_the_task_failed_when_not_already(self):
        task = _task("t1", status="ACTIVE")
        h = _tm(tasks=[task])
        h.tm.on_task_failed(TASK_EXECUTION_FAILED,
                            self._payload(error="boom", process_report="pr"))
        h.task_svc.fail.assert_called_once()
        assert h.task_svc.fail.call_args.kwargs["error"] == "boom"

    def test_already_failed_task_is_not_re_failed(self):
        h = _tm(tasks=[_task("t1", status="FAILED")])
        h.tm.on_task_failed(TASK_EXECUTION_FAILED, self._payload())
        assert not h.task_svc.fail.called

    def test_fail_transition_error_is_swallowed(self):
        h = _tm(tasks=[_task("t1", status="ACTIVE")])
        h.task_svc.fail.side_effect = RuntimeError("bad state")
        h.tm.on_task_failed(TASK_EXECUTION_FAILED, self._payload())
        assert h.task_svc.retry.called

    def test_exhausted_retries_cancels_and_fails_the_session(self):
        task = _task("t1", status="FAILED", retry_count=3)
        h = _tm(tasks=[task], max_retries=3)
        h.tm.on_task_failed(TASK_EXECUTION_FAILED, self._payload())
        h.task_svc.cancel_pending.assert_called_once_with("s1")
        assert any(c.args == ("s1", "FAILED") for c in h.session_svc.transition.call_args_list)
        assert not h.task_svc.retry.called

    def test_retry_failure_falls_back_to_failing_the_session(self):
        h = _tm(tasks=[_task("t1", status="FAILED", retry_count=0)])
        h.task_svc.retry.side_effect = RuntimeError("cannot retry")
        h.tm.on_task_failed(TASK_EXECUTION_FAILED, self._payload())
        assert h.task_svc.cancel_pending.called

    def test_cancel_pending_failure_is_swallowed(self):
        h = _tm(tasks=[_task("t1", status="FAILED", retry_count=9)], max_retries=3)
        h.task_svc.cancel_pending.side_effect = RuntimeError("down")
        h.tm.on_task_failed(TASK_EXECUTION_FAILED, self._payload())
        assert any(c.args == ("s1", "FAILED") for c in h.session_svc.transition.call_args_list)

    def test_missing_task_neither_retries_nor_fails_the_session(self):
        h = _tm()
        h.task_svc.get.side_effect = RuntimeError("gone")
        h.tm.on_task_failed(TASK_EXECUTION_FAILED, self._payload())
        assert not h.task_svc.retry.called
        assert not h.task_svc.cancel_pending.called

    def test_empty_queue_after_handling_fails_the_session(self):
        h = _tm(session=_session(queue=_Queue()),
                tasks=[_task("t1", status="FAILED", retry_count=0)])
        # retry pushes t1 back, so pop returns it and the session is not failed
        h.tm.on_task_failed(TASK_EXECUTION_FAILED, self._payload())
        assert not any(c.args == ("s1", "FAILED")
                       for c in h.session_svc.transition.call_args_list)

    def test_failed_task_removed_from_active_tasks(self):
        session = _session(active_tasks=["t1", "other"], queue=_Queue())
        h = _tm(session=session, tasks=[_task("t1", status="FAILED", retry_count=0)])
        with patch.object(TaskManager, "_dispatch_next"):   # isolate the removal
            h.tm.on_task_failed(TASK_EXECUTION_FAILED, self._payload())
        assert session.active_tasks == ["other"]

    def test_active_tasks_update_failure_is_swallowed(self):
        session = _session(active_tasks=["t1"], queue=_Queue())
        h = _tm(session=session, tasks=[_task("t1", status="FAILED", retry_count=0)])
        calls = {"n": 0}

        def flaky_save(_s):
            calls["n"] += 1
            if calls["n"] == 2:        # the active_tasks write, after record_failure's
                raise RuntimeError("down")

        h.session_svc.save.side_effect = flaky_save
        h.tm.on_task_failed(TASK_EXECUTION_FAILED, self._payload())
        assert h.task_svc.retry.called

    def test_no_task_id_skips_task_handling(self):
        h = _tm(session=_session(queue=_Queue(ready=["t2"])), tasks=[_task("t2")])
        h.tm.on_task_failed(TASK_EXECUTION_FAILED, self._payload(tid=""))
        assert not h.task_svc.fail.called
        assert h.lm.prepare_executor.called

    def test_session_transition_failure_is_swallowed(self):
        h = _tm(tasks=[_task("t1", status="FAILED", retry_count=9)], max_retries=3)
        h.session_svc.transition.side_effect = RuntimeError("bad state")
        h.tm.on_task_failed(TASK_EXECUTION_FAILED, self._payload())


class TestFailureCounter:
    def test_failure_increments_and_persists(self):
        session = _session(failure_counter=0)
        h = _tm(session=session)
        h.tm.record_failure("s1")
        assert session.failure_counter == 1
        assert h.session_svc.save.called

    def test_threshold_is_logged_not_raised(self):
        session = _session(failure_counter=2, failure_threshold=3)
        h = _tm(session=session)
        h.tm.record_failure("s1")
        assert session.failure_counter == 3

    def test_failure_recording_error_is_swallowed(self):
        h = _tm()
        h.session_svc.get.side_effect = RuntimeError("down")
        h.tm.record_failure("s1")

    def test_success_resets_only_when_nonzero(self):
        session = _session(failure_counter=0)
        h = _tm(session=session)
        h.tm.record_success("s1")
        assert not h.session_svc.save.called

    def test_success_reset_error_is_swallowed(self):
        h = _tm()
        h.session_svc.get.side_effect = RuntimeError("down")
        h.tm.record_success("s1")


# ── public queue interface ────────────────────────────────────────────────────

class TestPublicInterface:
    def test_next_task_pops(self):
        h = _tm(session=_session(queue=_Queue(ready=["t1"])), tasks=[_task("t1")])
        assert h.tm.next_task("s1").id == "t1"

    def test_activate_delegates(self):
        h = _tm()
        h.tm.activate("t1", "s1")
        h.task_svc.transition.assert_called_once_with("t1", "ACTIVE", "s1")

    def test_complete_finishes_and_resets_the_counter(self):
        session = _session(failure_counter=2)
        h = _tm(session=session)
        h.task_svc.finish.return_value = _task("t1", status="FINISHED")
        h.tm.complete("t1", process_report="pr", outputs="out", session_id="s1")
        assert h.task_svc.finish.called
        assert session.failure_counter == 0


# ── _dispatch_next ────────────────────────────────────────────────────────────

class TestDispatchNext:
    def test_none_task_releases_the_agent(self):
        h = _tm()
        h.tm._dispatch_next("s1", "a1", None)
        h.lm.release.assert_called_once_with("s1", "a1")

    def test_without_lifecycle_manager_is_a_noop(self):
        h = _tm()
        h.tm._lm = None
        h.tm._dispatch_next("s1", "a1", _task("t2"))

    def test_activates_then_prepares_then_runs(self):
        task = _task("t2", settings={"use_subagent": True, "subagent_template": "planner",
                                     "inherit_memory": False})
        h = _tm(tasks=[task])
        h.lm.prepare_executor.return_value = "exec-agent"
        h.tm._dispatch_next("s1", "a1", task)
        assert h.task_svc.transition.call_args_list[0].args == ("t2", "ACTIVE", "s1")
        kw = h.lm.prepare_executor.call_args.kwargs
        assert kw["use_subagent"] is True and kw["template_name"] == "planner"
        assert kw["inherit_memory"] is False
        assert kw["task_assigned_agent_id"] == "a1"
        h.lm.run_agent.assert_called_once_with("s1", "exec-agent", "t2")

    def test_inherit_memory_defaults_true(self):
        task = _task("t2", settings={})
        h = _tm(tasks=[task])
        h.tm._dispatch_next("s1", "a1", task)
        assert h.lm.prepare_executor.call_args.kwargs["inherit_memory"] is True

    def test_activation_failure_does_not_stop_dispatch(self):
        task = _task("t2", settings={})
        h = _tm(tasks=[task])
        h.task_svc.transition.side_effect = RuntimeError("bad state")
        h.tm._dispatch_next("s1", "a1", task)
        assert h.lm.prepare_executor.called

    def test_assigned_agent_is_persisted_and_task_marked_active(self):
        stored = _task("t2", settings={})
        session = _session(active_tasks=[])
        h = _tm(session=session, tasks=[stored])
        h.lm.prepare_executor.return_value = "exec-agent"
        h.tm._dispatch_next("s1", "a1", stored)
        assert stored.assigned_agent_id == "exec-agent"
        assert session.active_tasks == ["t2"]

    def test_already_active_task_not_duplicated(self):
        stored = _task("t2", settings={})
        session = _session(active_tasks=["t2"])
        h = _tm(session=session, tasks=[stored])
        h.lm.prepare_executor.return_value = "exec"
        h.tm._dispatch_next("s1", "a1", stored)
        assert session.active_tasks == ["t2"]

    def test_no_executor_means_no_run(self):
        task = _task("t2", settings={})
        h = _tm(tasks=[task])
        h.lm.prepare_executor.return_value = None
        h.tm._dispatch_next("s1", "a1", task)
        assert not h.lm.run_agent.called

    def test_task_save_failure_is_swallowed(self):
        task = _task("t2", settings={})
        h = _tm(tasks=[task])
        h.lm.prepare_executor.return_value = "exec"
        h.task_svc.save.side_effect = RuntimeError("down")
        h.tm._dispatch_next("s1", "a1", task)
        assert h.lm.run_agent.called

    def test_active_tasks_save_failure_is_swallowed(self):
        task = _task("t2", settings={})
        h = _tm(tasks=[task])
        h.lm.prepare_executor.return_value = "exec"
        h.session_svc.save.side_effect = RuntimeError("down")
        h.tm._dispatch_next("s1", "a1", task)
        assert h.lm.run_agent.called


# ── _sync_sibling_tracking ────────────────────────────────────────────────────

class TestSyncSiblingTracking:
    def test_siblings_added_to_tracking_and_trackers(self):
        agents = MagicMock()
        agents.get.return_value = {"id": "exec", "tracking_tasks": []}
        sib = _task("sib", assigned_agent_id="other")
        h = _tm(tasks=[sib], agents=agents)
        h.task_svc.list_children.return_value = [sib]
        task = _task("t2", parent_task_id="parent")
        h.tm._sync_sibling_tracking("s1", task, "exec")
        assert agents.save.call_args.args[0]["tracking_tasks"] == ["sib"]
        assert sib.trackers == ["exec"]

    def test_self_and_already_tracked_are_skipped(self):
        agents = MagicMock()
        agents.get.return_value = {"id": "exec", "tracking_tasks": ["known"]}
        task = _task("t2", parent_task_id="parent")
        h = _tm(agents=agents)
        h.task_svc.list_children.return_value = [task, _task("known")]
        h.tm._sync_sibling_tracking("s1", task, "exec")
        assert not agents.save.called

    def test_self_executed_sibling_skipped(self):
        agents = MagicMock()
        agents.get.return_value = {"id": "exec", "tracking_tasks": []}
        h = _tm(agents=agents)
        h.task_svc.list_children.return_value = [_task("sib", assigned_agent_id="exec")]
        h.tm._sync_sibling_tracking("s1", _task("t2", parent_task_id="p"), "exec")
        assert not agents.save.called

    def test_no_agent_store_is_a_noop(self):
        h = _tm(agents=None)
        h.tm._sync_sibling_tracking("s1", _task("t2", parent_task_id="p"), "exec")

    def test_no_parent_task_is_a_noop(self):
        agents = MagicMock()
        h = _tm(agents=agents)
        h.tm._sync_sibling_tracking("s1", _task("t2"), "exec")
        assert not agents.get.called

    def test_missing_agent_record_is_a_noop(self):
        agents = MagicMock()
        agents.get.return_value = None
        h = _tm(agents=agents)
        h.tm._sync_sibling_tracking("s1", _task("t2", parent_task_id="p"), "exec")
        assert not agents.save.called

    def test_existing_tracker_not_duplicated(self):
        agents = MagicMock()
        agents.get.return_value = {"id": "exec", "tracking_tasks": []}
        sib = _task("sib", assigned_agent_id="other", trackers=["exec"])
        h = _tm(tasks=[sib], agents=agents)
        h.task_svc.list_children.return_value = [sib]
        h.tm._sync_sibling_tracking("s1", _task("t2", parent_task_id="p"), "exec")
        assert sib.trackers == ["exec"]

    def test_sibling_save_failure_is_swallowed(self):
        agents = MagicMock()
        agents.get.return_value = {"id": "exec", "tracking_tasks": []}
        sib = _task("sib", assigned_agent_id="other")
        h = _tm(tasks=[sib], agents=agents)
        h.task_svc.list_children.return_value = [sib]
        h.task_svc.save.side_effect = RuntimeError("down")
        h.tm._sync_sibling_tracking("s1", _task("t2", parent_task_id="p"), "exec")
        assert agents.save.called

    def test_outer_failure_is_swallowed(self):
        agents = MagicMock()
        agents.get.side_effect = RuntimeError("store down")
        h = _tm(agents=agents)
        h.tm._sync_sibling_tracking("s1", _task("t2", parent_task_id="p"), "exec")


# ── _aggregate_children_result ────────────────────────────────────────────────

class TestAggregateChildrenResult:
    def test_single_finished_child(self):
        child = _task("c1", status="FINISHED", title="Child", outputs="the output")
        out = TaskManager._aggregate_children_result([child])
        assert "the output" in out and "completed" in out

    def test_failed_child_is_labelled(self):
        child = _task("c1", status="FAILED", title="Child", error="it broke")
        out = TaskManager._aggregate_children_result([child])
        assert "failed" in out and "it broke" in out

    def test_several_children_separated(self):
        a = _task("c1", status="FINISHED", title="A", outputs="oa")
        b = _task("c2", status="FINISHED", title="B", outputs="ob")
        out = TaskManager._aggregate_children_result([a, b])
        assert "oa" in out and "ob" in out and "---" in out

    def test_images_produce_multimodal_output(self):
        child = _task("c1", status="FINISHED", title="C",
                      outputs=[{"type": "image", "data": "D", "media_type": "image/png"},
                               {"type": "text", "text": "caption"}])
        out = TaskManager._aggregate_children_result([child])
        assert isinstance(out, list)
        assert out[0]["type"] == "image"
        assert "caption" in out[-1]["text"]

    def test_empty_children(self):
        assert TaskManager._aggregate_children_result([]) == ""


# ── _try_resume_parent / cascade ──────────────────────────────────────────────

class TestTryResumeParent:
    def _setup(self, child_statuses, parent_status="SUSPENDED"):
        parent = _task("parent", status=parent_status, assigned_agent_id="pa")
        children = [_task(f"c{i}", status=s, parent_task_id="parent")
                    for i, s in enumerate(child_statuses)]
        h = _tm(tasks=[parent] + children)
        h.task_svc.list_children.return_value = children
        return h, parent, children

    def test_all_finished_resumes_and_requeues(self):
        h, parent, children = self._setup(["FINISHED", "FINISHED"])
        h.tm._try_resume_parent("s1", "c0")
        h.task_svc.resume.assert_called_once_with("parent", "s1")
        assert "parent" in h.session.task_queue.ready

    def test_any_failed_cascades_the_parent(self):
        h, parent, children = self._setup(["FINISHED", "FAILED"])
        h.tm._try_resume_parent("s1", "c0")
        assert h.task_svc.fail.call_args.args[0] == "parent"
        assert "parent" in h.session.task_queue.removed
        assert not h.task_svc.resume.called

    def test_non_terminal_child_defers(self):
        h, _, _ = self._setup(["FINISHED", "ACTIVE"])
        h.tm._try_resume_parent("s1", "c0")
        assert not h.task_svc.resume.called and not h.task_svc.fail.called

    def test_no_children_defers(self):
        parent = _task("parent", status="SUSPENDED")
        child = _task("c0", status="FINISHED", parent_task_id="parent")
        h = _tm(tasks=[parent, child])
        h.task_svc.list_children.return_value = []
        h.tm._try_resume_parent("s1", "c0")
        assert not h.task_svc.resume.called

    def test_non_suspended_parent_is_left_alone(self):
        h, _, _ = self._setup(["FINISHED"], parent_status="ACTIVE")
        h.tm._try_resume_parent("s1", "c0")
        assert not h.task_svc.resume.called

    def test_child_without_parent_is_ignored(self):
        h = _tm(tasks=[_task("c0", status="FINISHED")])
        h.tm._try_resume_parent("s1", "c0")
        assert not h.task_svc.resume.called

    def test_no_task_id_is_a_noop(self):
        h = _tm()
        h.tm._try_resume_parent("s1", "")

    def test_lookup_failure_is_swallowed(self):
        h = _tm()
        h.task_svc.get.side_effect = RuntimeError("gone")
        h.tm._try_resume_parent("s1", "c0")


class TestCascadeFail:
    def test_fails_dependents_recursively(self):
        a = _task("a", status="PENDING", dag_deps=["failed"])
        b = _task("b", status="PENDING", dag_deps=["a"])
        h = _tm(tasks=[a, b])
        h.tm._cascade_fail("s1", "failed")
        failed_ids = [c.args[0] for c in h.task_svc.fail.call_args_list]
        assert failed_ids == ["a", "b"]
        assert h.session.task_queue.removed == ["a", "b"]

    def test_non_pending_tasks_untouched(self):
        h = _tm(tasks=[_task("a", status="ACTIVE", dag_deps=["failed"])])
        h.tm._cascade_fail("s1", "failed")
        assert not h.task_svc.fail.called

    def test_unrelated_tasks_untouched(self):
        h = _tm(tasks=[_task("a", status="PENDING", dag_deps=["other"])])
        h.tm._cascade_fail("s1", "failed")
        assert not h.task_svc.fail.called

    def test_list_failure_is_swallowed(self):
        h = _tm()
        h.task_svc.list_by_session.side_effect = RuntimeError("down")
        h.tm._cascade_fail("s1", "failed")

    def test_individual_fail_error_is_swallowed(self):
        h = _tm(tasks=[_task("a", status="PENDING", dag_deps=["failed"])])
        h.task_svc.fail.side_effect = RuntimeError("bad state")
        h.tm._cascade_fail("s1", "failed")


class TestSessionTermination:
    def test_cancel_remaining_clears_the_queue_and_fails(self):
        q = _Queue(ready=["a", "b"])
        h = _tm(session=_session(queue=q))
        h.tm._cancel_remaining_and_fail("s1")
        assert q.cleared == 1 and q.ready == []
        assert any(c.args == ("s1", "FAILED") for c in h.session_svc.transition.call_args_list)

    def test_fail_session_swallows_transition_errors(self):
        h = _tm()
        h.session_svc.transition.side_effect = RuntimeError("bad")
        h.tm._fail_session("s1")

    def test_lm_recycle_without_lm_is_a_noop(self):
        h = _tm()
        h.tm._lm = None
        h.tm._lm_recycle_finished("s1", "a1")


# ── queue helpers ─────────────────────────────────────────────────────────────

class TestQueueHelpers:
    def test_push_notify_remove_clear_persist(self):
        q = _Queue()
        h = _tm(session=_session(queue=q))
        h.tm._q_push("s1", "t1")
        h.tm._q_notify("s1", "t1")
        h.tm._q_remove("s1", "t1")
        h.tm._q_clear("s1")
        assert q.notified == ["t1"] and q.removed == ["t1"] and q.cleared == 1
        assert h.session_svc.save.call_count == 4

    def test_is_empty_reflects_the_queue(self):
        h = _tm(session=_session(queue=_Queue()))
        assert h.tm._q_is_empty("s1") is True

    def test_buffered_tasks_block_emptiness(self):
        h = _tm(session=_session(queue=_Queue()))
        h.tm.on_task_created(TASK_CREATED, {"task_id": "t9", "session_id": "s1"})
        assert h.tm._q_is_empty("s1") is False

    def test_q_clear_drains_buffered_tasks(self):
        h = _tm(session=_session(queue=_Queue()))
        h.tm.on_task_created(TASK_CREATED, {"task_id": "t9", "session_id": "s1"})
        h.tm._q_clear("s1")
        assert h.tm._q_is_empty("s1") is True

    def test_drain_pending_is_destructive(self):
        h = _tm()
        h.tm.on_task_created(TASK_CREATED, {"task_id": "t9", "session_id": "s1"})
        assert h.tm._drain_pending("s1") == ["t9"]
        assert h.tm._drain_pending("s1") == []
