"""Tests for the control tools (app/tools/control_tools.py).

Every tool is a ToolDefinition whose handler takes (arguments, ctx) and pulls its
services from the module-level `_svc` dict that get_control_tools() populates, so
the `services` fixture installs mocks there and restores the previous contents.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.domain.models.task import Task
from app.tools import control_tools as ct
from app.tools.control_tools import (
    _add_tracking_tasks, _apply_reviews, _ask_human, _track_parent_and_siblings,
    ask_human, control_tool, get_control_tools, get_tracked_task_output,
    submit_plan, submit_task, submit_task_assessment, update_task_metadata,
)
from app.tools.types import CallContext, ToolDefinition, ToolResult


def _task(**kw) -> Task:
    base = dict(id="t1", session_id="s1", creator_agent_id="creator", assigned_agent_id="a1",
                status="ACTIVE", user_prompt="do X", title="Do X", description="d",
                created_at="", updated_at="")
    base.update(kw)
    return Task(**base)


def _ctx(task=None, working_dir="", session_id="s1", agent_id="a1") -> CallContext:
    return CallContext(session_id=session_id, agent_id=agent_id,
                       task=task if task is not None else _task(), working_dir=working_dir)


@pytest.fixture
def services():
    """Install mock services into control_tools._svc and restore afterwards."""
    task_svc, session_svc, agent_store = MagicMock(), MagicMock(), MagicMock()
    task_svc.create.side_effect = lambda **kw: _task(
        id=f"new-{kw.get('title', 'x')}", title=kw.get("title", ""),
        session_id=kw.get("session_id", "s1"))
    task_svc.list_by_session.return_value = []
    task_svc.list_children.return_value = []
    agent_store.get.return_value = {"id": "a1", "session_id": "s1", "tracking_tasks": []}
    session_svc.get.return_value = SimpleNamespace(metadata={})
    saved = dict(ct._svc)
    get_control_tools(task_svc, session_svc, agent_store)
    yield SimpleNamespace(task=task_svc, session=session_svc, agents=agent_store)
    ct._svc.clear()
    ct._svc.update(saved)


@pytest.fixture
def hitl():
    """Patch the HITL store and SSE bus used by the ask_human paths."""
    store, bus = MagicMock(), MagicMock()
    store.wait.return_value = "the answer"
    with patch("app.storage.file.hitl_store.get_hitl_store", return_value=store), \
         patch("app.common.sse_bus.get_sse_bus", return_value=bus):
        yield SimpleNamespace(store=store, bus=bus)


# ── decorator / registration ──────────────────────────────────────────────────

class TestControlToolDecorator:
    def test_every_tool_is_a_control_tool_definition(self):
        for t in (ask_human, update_task_metadata, submit_task_assessment,
                  submit_plan, submit_task, get_tracked_task_output):
            assert isinstance(t, ToolDefinition) and t.is_control

    def test_service_params_are_not_in_the_schema(self):
        for t in (ask_human, submit_task, submit_plan):
            props = t.input_schema.properties
            assert "task_svc" not in props and "session_svc" not in props
            assert "ctx" not in props

    def test_schema_is_derived_from_annotations(self):
        props = submit_task.input_schema.properties
        assert props["title"]["type"] == "string"
        assert props["use_subagent"]["type"] == "boolean"
        assert set(submit_task.input_schema.require) == {"title", "description", "task_prompt"}

    def test_planned_task_typed_dict_expanded(self):
        items = submit_plan.input_schema.properties["tasks"]["items"]
        assert items["type"] == "object"
        assert "title" in items["properties"] and "use_subagent" in items["properties"]
        assert set(items["required"]) == {"title", "description"}

    def test_description_comes_from_the_docstring(self):
        assert submit_task.description.startswith("Create a single new task")

    def test_get_control_tools_populates_services_and_returns_all(self, services):
        tools = get_control_tools(services.task, services.session, services.agents)
        assert ct._svc["task_svc"] is services.task
        assert ct._svc["agent_store"] is services.agents
        assert {t.name for t in tools} >= {
            "ask_human", "submit_task", "submit_plan", "submit_task_assessment",
            "update_task_metadata", "get_tracked_task_output"}

    def test_decorator_registers_a_new_tool(self):
        before = len(ct._CONTROL_SCHEMAS)

        @control_tool
        def sample_control(x: str = "", *, ctx=None, task_svc=None, session_svc=None):
            """Docs."""
            return ToolResult(content=f"got {x}")

        try:
            assert len(ct._CONTROL_SCHEMAS) == before + 1
            assert sample_control.handler({"x": "v"}, None).content == "got v"
        finally:
            ct._CONTROL_SCHEMAS.remove(sample_control)


# ── _add_tracking_tasks ───────────────────────────────────────────────────────

class TestAddTrackingTasks:
    def test_appends_to_agent_and_task(self, services):
        stored = _task(id="tnew", trackers=[])
        services.task.get.return_value = stored
        _add_tracking_tasks("s1", ["a1"], "tnew")
        saved = services.agents.save.call_args.args[0]
        assert saved["tracking_tasks"] == ["tnew"]
        assert stored.trackers == ["a1"]
        services.task.save.assert_called_once_with(stored)

    def test_idempotent_when_already_tracking(self, services):
        services.agents.get.return_value = {"id": "a1", "tracking_tasks": ["tnew"]}
        _add_tracking_tasks("s1", ["a1"], "tnew")
        assert not services.agents.save.called
        assert not services.task.save.called

    def test_unknown_agent_is_skipped(self, services):
        services.agents.get.return_value = None
        _add_tracking_tasks("s1", ["ghost"], "tnew")
        assert not services.agents.save.called

    def test_existing_tracker_not_duplicated_on_task(self, services):
        stored = _task(id="tnew", trackers=["a1"])
        services.task.get.return_value = stored
        _add_tracking_tasks("s1", ["a1"], "tnew")
        assert stored.trackers == ["a1"]
        assert not services.task.save.called

    def test_no_agent_store_is_a_noop(self, services):
        ct._svc["agent_store"] = None
        _add_tracking_tasks("s1", ["a1"], "tnew")
        assert not services.task.save.called

    def test_task_lookup_failure_is_swallowed(self, services):
        services.task.get.side_effect = RuntimeError("gone")
        _add_tracking_tasks("s1", ["a1"], "tnew")
        assert services.agents.save.called

    def test_multiple_agents(self, services):
        services.agents.get.side_effect = lambda sid, aid: {"id": aid, "tracking_tasks": []}
        services.task.get.return_value = _task(id="tnew", trackers=[])
        _add_tracking_tasks("s1", ["a1", "a2"], "tnew")
        assert services.agents.save.call_count == 2


class TestTrackParentAndSiblings:
    def test_parent_and_siblings_tracked(self, services):
        services.task.get.side_effect = lambda tid, sid: (
            _task(id="parent", assigned_agent_id="pa") if tid == "parent"
            else _task(id=tid, trackers=[]))
        services.task.list_children.return_value = [
            _task(id="sibA", assigned_agent_id="sa"),
            _task(id="new", assigned_agent_id="na"),     # itself, excluded
        ]
        with patch.object(ct, "_add_tracking_tasks") as add:
            _track_parent_and_siblings("s1", "parent", "new", services.task)
        assert set(add.call_args.args[1]) == {"pa", "sa"}

    def test_no_parent_is_a_noop(self, services):
        with patch.object(ct, "_add_tracking_tasks") as add:
            _track_parent_and_siblings("s1", None, "new", services.task)
        assert not add.called

    def test_no_task_service_is_a_noop(self, services):
        with patch.object(ct, "_add_tracking_tasks") as add:
            _track_parent_and_siblings("s1", "parent", "new", None)
        assert not add.called

    def test_parent_lookup_failure_tolerated(self, services):
        services.task.get.side_effect = RuntimeError("gone")
        services.task.list_children.return_value = [_task(id="sibA", assigned_agent_id="sa")]
        with patch.object(ct, "_add_tracking_tasks") as add:
            _track_parent_and_siblings("s1", "parent", "new", services.task)
        assert add.call_args.args[1] == ["sa"]

    def test_children_lookup_failure_tolerated(self, services):
        services.task.get.return_value = _task(id="parent", assigned_agent_id="pa")
        services.task.list_children.side_effect = RuntimeError("gone")
        with patch.object(ct, "_add_tracking_tasks") as add:
            _track_parent_and_siblings("s1", "parent", "new", services.task)
        assert add.call_args.args[1] == ["pa"]

    def test_no_agents_found_skips_the_call(self, services):
        services.task.get.return_value = _task(id="parent", assigned_agent_id="")
        services.task.list_children.return_value = []
        with patch.object(ct, "_add_tracking_tasks") as add:
            _track_parent_and_siblings("s1", "parent", "new", services.task)
        assert not add.called


# ── ask_human ─────────────────────────────────────────────────────────────────

class TestAskHumanTool:
    def test_returns_the_answer_and_cycles_the_session(self, services, hitl):
        r = ask_human.handler({"prompt": "Continue?", "context": "background"}, _ctx())
        assert r.content == "the answer"
        assert services.session.transition.call_args_list[0].args == ("s1", "WAITING_INPUT")
        assert services.session.transition.call_args_list[1].args == ("s1", "RUNNING")

    def test_pushes_context_then_waiting_input(self, services, hitl):
        ask_human.handler({"prompt": "Q?", "context": "ctx text"}, _ctx())
        types = [c.args[1]["type"] for c in hitl.bus.push.call_args_list]
        assert types == ["message", "waiting_input"]
        assert hitl.bus.push.call_args_list[0].args[1]["content"] == "ctx text"
        assert hitl.bus.push.call_args_list[1].args[1]["prompt"] == "Q?"

    def test_prompt_stashed_in_session_metadata_then_cleared(self, services, hitl):
        sess = SimpleNamespace(metadata={})
        services.session.get.return_value = sess
        ask_human.handler({"prompt": "Q?"}, _ctx())
        assert "_hitl_prompt" not in sess.metadata      # cleared on the way out
        assert services.session.save.call_count == 2

    def test_metadata_failures_are_swallowed(self, services, hitl):
        services.session.get.side_effect = RuntimeError("store down")
        assert ask_human.handler({"prompt": "Q?"}, _ctx()).content == "the answer"

    def test_sse_failure_is_swallowed(self, services, hitl):
        hitl.bus.push.side_effect = RuntimeError("bus down")
        assert ask_human.handler({"prompt": "Q?"}, _ctx()).content == "the answer"

    def test_without_ctx_uses_empty_ids(self, services, hitl):
        ask_human.handler({"prompt": "Q?"}, None)
        assert hitl.store.wait.call_args.args[0] == ""

    def test_default_context_is_empty(self, services, hitl):
        ask_human.handler({"prompt": "Q?"}, _ctx())
        assert hitl.bus.push.call_args_list[0].args[1]["content"] == ""


class TestAskHumanHelper:
    def test_sends_a_blank_waiting_input(self, services, hitl):
        answer = _ask_human(_task(), "the process report", session_svc=services.session)
        assert answer == "the answer"
        ev = hitl.bus.push.call_args.args[1]
        assert ev["type"] == "waiting_input" and ev["prompt"] == ""
        assert hitl.store.wait.call_args.args[2] == ""      # no prompt text shown

    def test_input_type_is_user_input(self, services, hitl):
        _ask_human(_task(), "report", session_svc=services.session)
        assert hitl.store.wait.call_args.args[3] == "user_input"

    def test_metadata_failure_tolerated(self, services, hitl):
        services.session.get.side_effect = RuntimeError("down")
        assert _ask_human(_task(), "r", session_svc=services.session) == "the answer"

    def test_sse_failure_tolerated(self, services, hitl):
        hitl.bus.push.side_effect = RuntimeError("down")
        assert _ask_human(_task(), "r", session_svc=services.session) == "the answer"


# ── update_task_metadata ──────────────────────────────────────────────────────

class TestUpdateTaskMetadata:
    def _run(self, services, target=None, **args):
        task = _task(settings={"target_task_id": "target-1"} if target is not None else {})
        if target is not None:
            services.task.get.return_value = target
        ctx = _ctx(task=task)
        with patch("app.common.sse_bus.get_sse_bus", return_value=MagicMock()) as bus:
            result = update_task_metadata.handler(args, ctx)
        return result, task, bus

    def test_updates_the_target_task(self, services):
        target = _task(id="target-1", title="old", description="old d")
        r, task, _ = self._run(services, target=target,
                               title="new title", description="new desc")
        assert target.title == "new title" and target.description == "new desc"
        services.task.save.assert_called_once_with(target)
        assert task.actor_done is True
        assert r.content == "ok"

    def test_blank_fields_are_not_applied(self, services):
        target = _task(id="target-1", title="keep", description="keep d")
        self._run(services, target=target, title="", description="")
        assert target.title == "keep" and target.description == "keep d"

    def test_without_target_task_id_nothing_is_saved(self, services):
        r, task, _ = self._run(services, title="t", description="d")
        assert not services.task.save.called
        assert task.actor_done is True

    def test_session_goal_is_set(self, services):
        target = _task(id="target-1")
        self._run(services, target=target, title="t", description="d",
                  session_goal="the new goal")
        services.session.set_goal.assert_called_once_with("s1", "the new goal")

    def test_session_goal_needs_a_resolved_session(self, services):
        self._run(services, title="t", description="d", session_goal="goal")
        assert not services.session.set_goal.called

    def test_set_goal_failure_is_swallowed(self, services):
        services.session.set_goal.side_effect = RuntimeError("down")
        target = _task(id="target-1")
        r, _, _ = self._run(services, target=target, title="t", description="d",
                            session_goal="g")
        assert r.content == "ok"

    def test_target_lookup_failure_is_logged_not_raised(self, services):
        services.task.get.side_effect = RuntimeError("missing")
        task = _task(settings={"target_task_id": "target-1"})
        r = update_task_metadata.handler({"title": "t", "description": "d"}, _ctx(task=task))
        assert r.content == "ok"

    def test_daemon_target_pushes_daemon_event(self, services):
        target = _task(id="target-1", settings={"_daemon": True})
        bus = MagicMock()
        task = _task(settings={"target_task_id": "target-1"})
        services.task.get.return_value = target
        with patch("app.common.sse_bus.get_sse_bus", return_value=bus):
            update_task_metadata.handler({"title": "t", "description": "d"}, _ctx(task=task))
        assert bus.push.call_args.args[1]["type"] == "daemon_task_updated"

    def test_normal_target_pushes_task_updated(self, services):
        target = _task(id="target-1")
        bus = MagicMock()
        task = _task(settings={"target_task_id": "target-1"})
        services.task.get.return_value = target
        with patch("app.common.sse_bus.get_sse_bus", return_value=bus):
            update_task_metadata.handler({"title": "t", "description": "d"}, _ctx(task=task))
        assert bus.push.call_args.args[1]["type"] == "task_updated"

    def test_without_ctx(self, services):
        assert update_task_metadata.handler(
            {"title": "t", "description": "d"}, None).content == "ok"


# ── submit_task_assessment ────────────────────────────────────────────────────

class TestSubmitTaskAssessment:
    def _run(self, services, task=None, **args):
        args.setdefault("task_process_report", "the report")
        task = task if task is not None else _task(outputs="some output")
        return submit_task_assessment.handler(args, _ctx(task=task)), task

    def test_success_finishes_the_task(self, services):
        r, task = self._run(services, task_status="success")
        services.task.finish.assert_called_once()
        assert task.status == "FINISHED" and task.process_report == "the report"
        assert "outcome=success" in r.content

    def test_failed_records_the_reason(self, services):
        r, task = self._run(services, task_status="failed",
                            task_failure_reason="step 3 blew up")
        services.task.fail.assert_called_once()
        assert task.status == "FAILED" and task.error == "step 3 blew up"
        assert "Failure reason: step 3 blew up" in r.content

    def test_failed_without_reason_omits_the_clause(self, services):
        r, _ = self._run(services, task_status="failed")
        assert "Failure reason" not in r.content

    def test_active_requeues_as_pending(self, services):
        r, task = self._run(services, task_status="active")
        assert task.status == "PENDING"
        assert services.task.transition.call_args.args[:2] == ("t1", "PENDING")

    def test_unknown_status_becomes_failed(self, services):
        r, task = self._run(services, task_status="nonsense")
        assert task.status == "FAILED" and "outcome=failed" in r.content

    def test_success_without_output_is_downgraded_to_active(self, services):
        r, task = self._run(services, task=_task(outputs=""), task_status="success")
        assert task.status == "PENDING"
        assert "final output" in task.process_report
        assert not services.task.finish.called

    def test_next_step_hint_is_appended(self, services):
        _, task = self._run(services, task_status="success",
                            next_step_hint="watch the rate limit")
        assert "Next Step Hint: watch the rate limit" in task.process_report

    def test_no_hint_leaves_the_report_alone(self, services):
        _, task = self._run(services, task_status="success")
        assert task.process_report == "the report"

    def test_ask_human_waits_and_requeues(self, services, hitl):
        stored = _task()
        services.task.get.return_value = stored
        r, task = self._run(services, task_status="ask_human")
        assert task.status == "PENDING"
        assert stored.pending_user_answer == "the answer"
        services.task.save.assert_called_once_with(stored)

    def test_ask_human_stash_failure_is_swallowed(self, services, hitl):
        services.task.get.side_effect = RuntimeError("gone")
        r, task = self._run(services, task_status="ask_human")
        assert task.status == "PENDING"

    def test_without_ctx_only_returns_text(self, services):
        r = submit_task_assessment.handler(
            {"task_status": "success", "task_process_report": "r"}, None)
        assert not services.task.finish.called
        assert "outcome=success" in r.content

    def test_reviews_are_applied(self, services):
        sibling = _task(id="sib", title="Sibling", status="FINISHED", assigned_agent_id="a1")
        services.task.list_by_session.return_value = [sibling]
        r, _ = self._run(services, task_status="success", task_reviews=[
            {"task_title": "Sibling", "review_status": "reopen", "reasoning": "incomplete"}])
        services.task.reopen.assert_called_once_with("sib", "s1")
        assert "Reviews applied: Sibling → reopen" in r.content

    def test_empty_reviews_add_nothing(self, services):
        r, _ = self._run(services, task_status="success", task_reviews=[])
        assert "Reviews applied" not in r.content


class TestApplyReviews:
    def _reviewable(self, services, **kw):
        base = dict(id="sib", title="Sibling", status="FINISHED", assigned_agent_id="a1")
        base.update(kw)
        services.task.list_by_session.return_value = [_task(**base)]

    def test_reopen(self, services):
        self._reviewable(services)
        out = _apply_reviews([{"task_title": "Sibling", "review_status": "reopen",
                               "reasoning": "why"}], _ctx(), task_svc=services.task)
        assert services.task.reopen.called and "Sibling → reopen" in out

    def test_skip_finishes_with_the_reasoning(self, services):
        self._reviewable(services)
        _apply_reviews([{"task_title": "Sibling", "review_status": "skip",
                         "reasoning": "done elsewhere"}], _ctx(), task_svc=services.task)
        assert services.task.finish.call_args.kwargs["process_report"] == "done elsewhere"

    def test_skip_without_reasoning_uses_a_default(self, services):
        self._reviewable(services)
        _apply_reviews([{"task_title": "Sibling", "review_status": "skip"}],
                       _ctx(), task_svc=services.task)
        assert "Completed indirectly" in services.task.finish.call_args.kwargs["process_report"]

    def test_confirmed_applies_nothing_but_is_reported(self, services):
        self._reviewable(services)
        out = _apply_reviews([{"task_title": "Sibling", "review_status": "confirmed",
                               "reasoning": "ok"}], _ctx(), task_svc=services.task)
        assert not services.task.reopen.called and not services.task.finish.called
        assert "Sibling → confirmed" in out

    def test_non_dict_entries_ignored(self, services):
        self._reviewable(services)
        assert _apply_reviews(["junk", 42], _ctx(), task_svc=services.task) == ""

    def test_missing_title_ignored(self, services):
        self._reviewable(services)
        assert _apply_reviews([{"review_status": "reopen"}], _ctx(),
                              task_svc=services.task) == ""

    def test_invalid_status_ignored(self, services):
        self._reviewable(services)
        assert _apply_reviews([{"task_title": "Sibling", "review_status": "bogus"}],
                              _ctx(), task_svc=services.task) == ""

    def test_unmatched_title_ignored(self, services):
        self._reviewable(services)
        assert _apply_reviews([{"task_title": "Nope", "review_status": "reopen"}],
                              _ctx(), task_svc=services.task) == ""

    def test_other_agents_tasks_are_not_reviewable(self, services):
        self._reviewable(services, assigned_agent_id="other")
        assert _apply_reviews([{"task_title": "Sibling", "review_status": "reopen"}],
                              _ctx(), task_svc=services.task) == ""

    def test_active_tasks_are_not_reviewable(self, services):
        self._reviewable(services, status="ACTIVE")
        assert _apply_reviews([{"task_title": "Sibling", "review_status": "reopen"}],
                              _ctx(), task_svc=services.task) == ""

    def test_pending_tasks_are_reviewable(self, services):
        self._reviewable(services, status="PENDING")
        out = _apply_reviews([{"task_title": "Sibling", "review_status": "reopen"}],
                             _ctx(), task_svc=services.task)
        assert "Sibling → reopen" in out

    def test_the_current_task_is_excluded(self, services):
        self._reviewable(services, id="t1")       # same id as ctx.task
        assert _apply_reviews([{"task_title": "Sibling", "review_status": "reopen"}],
                              _ctx(), task_svc=services.task) == ""

    def test_apply_failure_is_swallowed_but_still_reported(self, services):
        self._reviewable(services)
        services.task.reopen.side_effect = RuntimeError("cannot reopen")
        out = _apply_reviews([{"task_title": "Sibling", "review_status": "reopen"}],
                             _ctx(), task_svc=services.task)
        assert "Sibling → reopen" in out

    def test_without_ctx(self, services):
        services.task.list_by_session.return_value = []
        assert _apply_reviews([{"task_title": "x", "review_status": "reopen"}],
                              None, task_svc=services.task) == ""


# ── submit_plan ───────────────────────────────────────────────────────────────

class TestSubmitPlan:
    def _run(self, services, tasks, task=None, working_dir=""):
        task = task if task is not None else _task()
        ctx = _ctx(task=task, working_dir=working_dir)
        return submit_plan.handler({"tasks": tasks}, ctx), task

    def test_creates_tasks_in_order_with_dag_deps(self, services):
        r, task = self._run(services, [
            {"title": "A", "description": "da"}, {"title": "B", "description": "db"}])
        calls = services.task.create.call_args_list
        assert [c.kwargs["title"] for c in calls] == ["A", "B"]
        assert calls[0].kwargs["dag_deps"] == []
        assert calls[1].kwargs["dag_deps"] == ["new-A"]
        assert "Planned 2 tasks: A, B" in r.content

    def test_parent_is_suspended_once(self, services):
        r, task = self._run(services, [{"title": "A", "description": "d"}])
        assert task.status == "SUSPENDED" and task.actor_done is True
        services.task.transition.assert_called_once_with("t1", "SUSPENDED", "s1")

    def test_already_suspended_parent_not_transitioned_again(self, services):
        r, task = self._run(services, [{"title": "A", "description": "d"}],
                            task=_task(status="SUSPENDED"))
        assert not services.task.transition.called

    def test_empty_plan_means_goal_complete(self, services):
        r, task = self._run(services, [])
        assert "No further tasks needed" in r.content
        assert task.status == "ACTIVE" and task.actor_done is False

    def test_working_dir_propagated_to_children(self, services):
        self._run(services, [{"title": "A", "description": "d"}], working_dir="/w")
        assert services.task.create.call_args.kwargs["inputs"]["working_dir"] == "/w"

    def test_skill_name_propagated(self, services):
        self._run(services, [{"title": "A", "description": "d", "skill_name": "pptx"}])
        assert services.task.create.call_args.kwargs["inputs"]["skill_name"] == "pptx"

    def test_null_skill_name_omitted(self, services):
        self._run(services, [{"title": "A", "description": "d", "skill_name": None}])
        assert "skill_name" not in services.task.create.call_args.kwargs["inputs"]

    def test_subagent_settings(self, services):
        self._run(services, [{"title": "A", "description": "d", "use_subagent": True,
                              "subagent_template": "planner", "inherit_memory": False}])
        inputs = services.task.create.call_args.kwargs["inputs"]
        assert inputs["use_subagent"] is True
        assert inputs["subagent_template"] == "planner"
        assert inputs["inherit_memory"] is False

    def test_subagent_template_defaults_from_settings(self, services):
        with patch("app.tools.control_tools.get_settings",
                   return_value=SimpleNamespace(default_agent_template_name="sysdefault")):
            self._run(services, [{"title": "A", "description": "d", "use_subagent": True}])
        inputs = services.task.create.call_args.kwargs["inputs"]
        assert inputs["subagent_template"] == "sysdefault"
        assert inputs["inherit_memory"] is True

    def test_non_subagent_task_has_no_subagent_keys(self, services):
        self._run(services, [{"title": "A", "description": "d"}])
        inputs = services.task.create.call_args.kwargs["inputs"]
        assert "use_subagent" not in inputs and "subagent_template" not in inputs

    def test_task_prompt_becomes_user_prompt(self, services):
        self._run(services, [{"title": "A", "description": "d", "task_prompt": "full ctx"}])
        assert services.task.create.call_args.kwargs["user_prompt"] == "full ctx"

    def test_missing_task_prompt_defaults_empty(self, services):
        self._run(services, [{"title": "A", "description": "d"}])
        assert services.task.create.call_args.kwargs["user_prompt"] == ""

    def test_json_string_payload_is_parsed(self, services):
        r, _ = self._run(services, json.dumps([{"title": "A", "description": "d"}]))
        assert "Planned 1 tasks: A" in r.content

    def test_unparseable_string_payload_becomes_empty(self, services):
        r, _ = self._run(services, "not json at all")
        assert "No further tasks needed" in r.content

    def test_bare_string_entry_is_promoted(self, services):
        self._run(services, ["Just do it"])
        kw = services.task.create.call_args.kwargs
        assert kw["title"] == "Just do it" and kw["description"] == "Just do it"

    def test_creator_is_the_assigned_agent(self, services):
        self._run(services, [{"title": "A", "description": "d"}])
        assert services.task.create.call_args.kwargs["creator_agent_id"] == "a1"

    def test_parent_task_id_recorded(self, services):
        self._run(services, [{"title": "A", "description": "d"}])
        assert services.task.create.call_args.kwargs["parent_task_id"] == "t1"

    def test_without_ctx_uses_blank_ids(self, services):
        r = submit_plan.handler({"tasks": [{"title": "A", "description": "d"}]}, None)
        assert services.task.create.call_args.kwargs["session_id"] == ""
        assert "Planned 1 tasks" in r.content

    def test_tracking_is_wired_up(self, services):
        with patch.object(ct, "_add_tracking_tasks") as add, \
             patch.object(ct, "_track_parent_and_siblings") as track:
            self._run(services, [{"title": "A", "description": "d"}])
        assert set(add.call_args.args[1]) == {"a1", "creator"}
        assert track.called


# ── submit_task ───────────────────────────────────────────────────────────────

class TestSubmitTask:
    def _run(self, services, task=None, working_dir="", **args):
        args.setdefault("title", "T")
        args.setdefault("description", "d")
        args.setdefault("task_prompt", "p")
        task = task if task is not None else _task()
        return submit_task.handler(args, _ctx(task=task, working_dir=working_dir)), task

    def test_creates_a_task_and_suspends_the_parent(self, services):
        r, task = self._run(services)
        assert services.task.create.called
        assert task.status == "SUSPENDED" and task.actor_done is True
        assert "Task created: id=new-T" in r.content

    def test_already_suspended_parent_not_transitioned(self, services):
        r, task = self._run(services, task=_task(status="SUSPENDED"))
        assert not services.task.transition.called

    def test_skill_name_forces_a_subagent(self, services):
        self._run(services, skill_name="pptx")
        inputs = services.task.create.call_args.kwargs["inputs"]
        assert inputs["skill_name"] == "pptx" and inputs["use_subagent"] is True

    def test_plain_task_is_inline(self, services):
        self._run(services)
        assert "use_subagent" not in services.task.create.call_args.kwargs["inputs"]

    def test_subagent_template_only_set_when_given(self, services):
        self._run(services, use_subagent=True)
        assert "subagent_template" not in services.task.create.call_args.kwargs["inputs"]
        self._run(services, use_subagent=True, subagent_template="planner")
        assert services.task.create.call_args.kwargs["inputs"]["subagent_template"] == "planner"

    def test_inherit_memory_forwarded(self, services):
        self._run(services, use_subagent=True, inherit_memory=False)
        assert services.task.create.call_args.kwargs["inputs"]["inherit_memory"] is False

    def test_working_dir_forwarded(self, services):
        self._run(services, working_dir="/w")
        assert services.task.create.call_args.kwargs["inputs"]["working_dir"] == "/w"

    def test_creator_tracks_the_child(self, services):
        with patch.object(ct, "_add_tracking_tasks") as add, \
             patch.object(ct, "_track_parent_and_siblings"):
            self._run(services)
        assert add.call_args.args[1] == ["a1"]

    def test_no_creator_skips_tracking(self, services):
        with patch.object(ct, "_add_tracking_tasks") as add, \
             patch.object(ct, "_track_parent_and_siblings"):
            self._run(services, task=_task(assigned_agent_id=""))
        assert not add.called

    def test_without_ctx(self, services):
        r = submit_task.handler({"title": "T", "description": "d", "task_prompt": "p"}, None)
        assert services.task.create.call_args.kwargs["session_id"] == ""
        assert "Task created" in r.content


# ── get_tracked_task_output ───────────────────────────────────────────────────

class TestGetTrackedTaskOutput:
    def _tracked(self, services, tasks, tracking=None):
        services.agents.get.return_value = {
            "id": "a1", "tracking_tasks": tracking if tracking is not None
            else [t.id for t in tasks]}
        by_id = {t.id: t for t in tasks}
        services.task.get.side_effect = lambda tid, sid: by_id[tid]

    def test_returns_matching_output(self, services):
        self._tracked(services, [_task(id="x", title="Report", status="FINISHED",
                                       outputs="the output")])
        r = get_tracked_task_output.handler({"title": "Report"}, _ctx())
        assert "Task「Report」" in r.content and "Output:\nthe output" in r.content

    def test_includes_process_report_and_error(self, services):
        self._tracked(services, [_task(id="x", title="Report", outputs="o",
                                       process_report="pr", error="err")])
        r = get_tracked_task_output.handler({"title": "Report"}, _ctx())
        assert "Process Report:\npr" in r.content and "Error:\nerr" in r.content

    def test_no_match_reports_so(self, services):
        self._tracked(services, [_task(id="x", title="Other", outputs="o")])
        r = get_tracked_task_output.handler({"title": "Report"}, _ctx())
        assert "No tracked task found" in r.content

    def test_untracked_tasks_are_not_searched(self, services):
        self._tracked(services, [_task(id="x", title="Report", outputs="o")], tracking=[])
        assert "No tracked task found" in get_tracked_task_output.handler(
            {"title": "Report"}, _ctx()).content

    def test_lookup_failure_is_skipped(self, services):
        services.agents.get.return_value = {"id": "a1", "tracking_tasks": ["gone"]}
        services.task.get.side_effect = RuntimeError("missing")
        assert "No tracked task found" in get_tracked_task_output.handler(
            {"title": "Report"}, _ctx()).content

    def test_several_matches_are_joined(self, services):
        self._tracked(services, [
            _task(id="x", title="Report", outputs="first"),
            _task(id="y", title="Report", outputs="second")])
        r = get_tracked_task_output.handler({"title": "Report"}, _ctx())
        assert "first" in r.content and "second" in r.content and "---" in r.content

    def test_multimodal_output_returns_blocks(self, services):
        outputs = [{"type": "image", "data": "D", "media_type": "image/png"},
                   {"type": "text", "text": "caption"}]
        self._tracked(services, [_task(id="x", title="Report", outputs=outputs)])
        r = get_tracked_task_output.handler({"title": "Report"}, _ctx())
        assert isinstance(r.content, list)
        assert r.content[0]["type"] == "image"
        assert "caption" in r.content[1]["text"]

    def test_multimodal_without_text_part(self, services):
        outputs = [{"type": "image", "data": "D", "media_type": "image/png"}]
        self._tracked(services, [_task(id="x", title="Report", outputs=outputs)])
        r = get_tracked_task_output.handler({"title": "Report"}, _ctx())
        assert isinstance(r.content, list)

    def test_mixed_matches_with_images_use_block_form(self, services):
        self._tracked(services, [
            _task(id="x", title="Report", outputs="plain"),
            _task(id="y", title="Report",
                  outputs=[{"type": "image", "data": "D", "media_type": "image/png"}])])
        r = get_tracked_task_output.handler({"title": "Report"}, _ctx())
        assert isinstance(r.content, list)
        assert any(p.get("text") == "\n\n---\n\n" for p in r.content if isinstance(p, dict))

    def test_empty_outputs_still_reports_the_header(self, services):
        self._tracked(services, [_task(id="x", title="Report", outputs="")])
        r = get_tracked_task_output.handler({"title": "Report"}, _ctx())
        assert "Task「Report」" in r.content and "Output:" not in r.content

    def test_no_agent_store_finds_nothing(self, services):
        ct._svc["agent_store"] = None
        assert "No tracked task found" in get_tracked_task_output.handler(
            {"title": "Report"}, _ctx()).content

    def test_missing_agent_record_finds_nothing(self, services):
        services.agents.get.return_value = None
        assert "No tracked task found" in get_tracked_task_output.handler(
            {"title": "Report"}, _ctx()).content

    def test_without_ctx_finds_nothing(self, services):
        assert "No tracked task found" in get_tracked_task_output.handler(
            {"title": "Report"}, None).content
