"""Tests for LifecycleManager (app/orchestrator/lifecycle_manager.py).

Task scheduling moved to TaskManager, so LM no longer takes task_svc/task_manager
and no longer has _auto_spawn_for_task / schedule_task / _run_task_safe. Its surface
is now: init_session / register_root_agent / prepare_executor / release / run_agent /
spawn_daemon_agent, plus the agent-tree bookkeeping behind them.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.domain.events.event_types import (
    LIFECYCLE_AGENT_RECYCLED, LIFECYCLE_AGENT_SCHEDULED, SPAWN_REJECTED,
    TASK_EXECUTION_FAILED, TASK_EXECUTION_FINISHED,
)
from app.orchestrator.lifecycle_manager import AgentMeta, LifecycleManager, LMState


class _Bus:
    """Records published events instead of dispatching them."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def publish(self, topic, payload):
        self.events.append((topic, payload))

    def topics(self) -> list[str]:
        return [t for t, _ in self.events]


def _lm(**kw) -> LifecycleManager:
    defaults = dict(
        session_svc=MagicMock(),
        agent_store=MagicMock(),
        event_bus=_Bus(),
        max_concurrent_agents=5,
        max_concurrent_tasks=10,
        max_spawn_depth=1,
    )
    defaults.update(kw)
    return LifecycleManager(**defaults)


def _with_root(lm: LifecycleManager, session_id="s1", root="root-agent") -> LifecycleManager:
    lm.init_session(session_id)
    lm.register_root_agent(session_id, root)
    return lm


# ── session lifecycle ─────────────────────────────────────────────────────────

class TestSessionLifecycle:
    def test_init_session_creates_state_and_lock(self):
        lm = _lm()
        lm.init_session("s1")
        assert isinstance(lm._states["s1"], LMState)
        assert isinstance(lm._locks["s1"], type(threading.Lock()))
        assert lm._states["s1"].max_concurrent_agents == 5

    def test_cleanup_session_removes_state(self):
        lm = _lm()
        lm.init_session("s1")
        lm.cleanup_session("s1")
        assert "s1" not in lm._states and "s1" not in lm._locks

    def test_cleanup_unknown_session_is_noop(self):
        _lm().cleanup_session("ghost")

    def test_cleanup_evicts_reasoner_session(self):
        reasoner = MagicMock()
        lm = _lm(reasoner=reasoner)
        lm.init_session("s1")
        lm.cleanup_session("s1")
        reasoner.evict_session.assert_called_once_with("s1")


class TestRegisterRootAgent:
    def test_registers_at_depth_zero(self):
        lm = _with_root(_lm())
        meta = lm._states["s1"].agent_registry["root-agent"]
        assert meta.spawn_depth == 0 and meta.parent_id is None
        assert meta.status == "RUNNING" and meta.task_id is None
        assert lm._states["s1"].concurrent_agents == 1
        assert lm._states["s1"].root_agent_id == "root-agent"

    def test_re_registering_only_flips_status(self):
        lm = _with_root(_lm())
        lm._states["s1"].agent_registry["root-agent"].status = "FINISHED"
        lm.register_root_agent("s1", "root-agent")
        assert lm._states["s1"].agent_registry["root-agent"].status == "RUNNING"
        assert lm._states["s1"].concurrent_agents == 1   # not double-counted

    def test_without_init_session_is_noop(self):
        lm = _lm()
        lm.register_root_agent("s1", "root-agent")
        assert lm._states == {}


# ── prepare_executor ──────────────────────────────────────────────────────────

class TestPrepareExecutorGuards:
    def test_unknown_session_returns_none(self):
        assert _lm().prepare_executor("ghost", "a", "t", False, "", True) is None

    def test_missing_state_returns_none(self):
        lm = _lm()
        lm.init_session("s1")
        del lm._states["s1"]
        assert lm.prepare_executor("s1", "a", "t", False, "", True) is None

    def test_unknown_finished_agent_returns_none(self):
        lm = _with_root(_lm())
        assert lm.prepare_executor("s1", "ghost-agent", "t", False, "", True) is None


class TestPrepareExecutorResume:
    def test_previously_dispatched_agent_returned_directly(self):
        lm = _with_root(_lm())
        lm._states["s1"].agent_registry["sub-1"] = AgentMeta("sub-1", "root-agent", None, 1, "WAITING")
        out = lm.prepare_executor(
            "s1", "root-agent", "t", True, "", True,
            task_assigned_agent_id="sub-1", task_creator_agent_id="root-agent")
        assert out == "sub-1"
        assert lm._states["s1"].agent_registry["sub-1"].status == "RUNNING"

    def test_assigned_equals_creator_is_not_a_resume(self):
        lm = _with_root(_lm())
        out = lm.prepare_executor(
            "s1", "root-agent", "t", False, "", True,
            task_assigned_agent_id="root-agent", task_creator_agent_id="root-agent")
        assert out == "root-agent"

    def test_assigned_agent_gone_falls_through(self):
        lm = _with_root(_lm())
        out = lm.prepare_executor(
            "s1", "root-agent", "t", False, "", True,
            task_assigned_agent_id="evicted", task_creator_agent_id="root-agent")
        assert out == "root-agent"


class TestPrepareExecutorInline:
    def test_inline_task_reuses_root(self):
        lm = _with_root(_lm())
        assert lm.prepare_executor("s1", "root-agent", "t", False, "", True) == "root-agent"

    def test_waiting_agent_runs_inline_task_itself(self):
        lm = _with_root(_lm())
        lm._states["s1"].agent_registry["sub-1"] = AgentMeta("sub-1", "root-agent", None, 1, "WAITING")
        assert lm.prepare_executor("s1", "sub-1", "t", False, "", True) == "sub-1"
        assert lm._states["s1"].agent_registry["sub-1"].status == "RUNNING"

    def test_finished_subagent_recycled_and_parent_returned(self):
        bus = _Bus()
        lm = _with_root(_lm(event_bus=bus))
        st = lm._states["s1"]
        st.agent_registry["sub-1"] = AgentMeta("sub-1", "root-agent", "t0", 1, "FINISHED")
        st.concurrent_agents += 1
        st.concurrent_tasks += 1
        assert lm.prepare_executor("s1", "sub-1", "t", False, "", True) == "root-agent"
        assert "sub-1" not in st.agent_registry
        assert st.concurrent_agents == 1 and st.concurrent_tasks == 0
        assert LIFECYCLE_AGENT_RECYCLED in bus.topics()

    def test_orphan_subagent_falls_back_to_root(self):
        lm = _with_root(_lm())
        lm._states["s1"].agent_registry["sub-1"] = AgentMeta("sub-1", None, None, 1, "FINISHED")
        assert lm.prepare_executor("s1", "sub-1", "t", False, "", True) == "root-agent"


class TestPrepareExecutorSpawn:
    def test_spawns_child_and_counts_it(self):
        bus = _Bus()
        lm = _with_root(_lm(event_bus=bus, max_spawn_depth=2))
        lm._instantiate_sub_agent = MagicMock(return_value="sub-new")
        out = lm.prepare_executor("s1", "root-agent", "t9", True, "planner", True)
        assert out == "sub-new"
        meta = lm._states["s1"].agent_registry["sub-new"]
        assert meta.parent_id == "root-agent" and meta.spawn_depth == 1
        assert meta.status == "RUNNING" and meta.task_id is None
        assert lm._states["s1"].concurrent_agents == 2
        assert lm._states["s1"].concurrent_tasks == 1
        assert LIFECYCLE_AGENT_SCHEDULED in bus.topics()

    def test_template_name_forwarded(self):
        lm = _with_root(_lm(max_spawn_depth=2))
        lm._instantiate_sub_agent = MagicMock(return_value="sub-new")
        lm.prepare_executor("s1", "root-agent", "t9", True, "planner", False)
        kw = lm._instantiate_sub_agent.call_args.kwargs
        assert kw["template_name"] == "planner"
        assert kw["inherit_memory"] is False
        assert kw["spawn_depth"] == 1
        assert kw["parent_agent_id"] == "root-agent"

    def test_instantiate_failure_falls_back_to_base(self):
        lm = _with_root(_lm(max_spawn_depth=2))
        lm._instantiate_sub_agent = MagicMock(side_effect=RuntimeError("template broken"))
        assert lm.prepare_executor("s1", "root-agent", "t", True, "x", True) == "root-agent"

    def test_depth_limit_rejects_and_publishes(self):
        bus = _Bus()
        lm = _with_root(_lm(event_bus=bus, max_spawn_depth=1))
        st = lm._states["s1"]
        st.agent_registry["sub-1"] = AgentMeta("sub-1", "root-agent", None, 1, "WAITING")
        out = lm.prepare_executor("s1", "sub-1", "t", True, "", True)
        assert out == "sub-1"
        assert SPAWN_REJECTED in bus.topics()
        reason = [p for t, p in bus.events if t == SPAWN_REJECTED][0]["reason"]
        assert "Max spawn depth" in reason

    def test_concurrency_limit_rejects(self):
        bus = _Bus()
        lm = _with_root(_lm(event_bus=bus, max_concurrent_agents=1, max_spawn_depth=3))
        out = lm.prepare_executor("s1", "root-agent", "t", True, "", True)
        assert out == "root-agent"
        reason = [p for t, p in bus.events if t == SPAWN_REJECTED][0]["reason"]
        assert "concurrent_agents limit" in reason

    def test_token_budget_exhaustion_rejects(self):
        svc = MagicMock()
        svc.get.return_value = SimpleNamespace(token_budget=1000, output_tokens_used=950)
        bus = _Bus()
        lm = _with_root(_lm(session_svc=svc, event_bus=bus, max_spawn_depth=3))
        assert lm.prepare_executor("s1", "root-agent", "t", True, "", True) == "root-agent"
        reason = [p for t, p in bus.events if t == SPAWN_REJECTED][0]["reason"]
        assert "Token budget" in reason

    def test_budget_within_limit_allows_spawn(self):
        svc = MagicMock()
        svc.get.return_value = SimpleNamespace(token_budget=1000, output_tokens_used=100)
        lm = _with_root(_lm(session_svc=svc, max_spawn_depth=3))
        lm._instantiate_sub_agent = MagicMock(return_value="sub-new")
        assert lm.prepare_executor("s1", "root-agent", "t", True, "", True) == "sub-new"

    def test_zero_budget_means_unlimited(self):
        svc = MagicMock()
        svc.get.return_value = SimpleNamespace(token_budget=0, output_tokens_used=10**9)
        lm = _with_root(_lm(session_svc=svc, max_spawn_depth=3))
        lm._instantiate_sub_agent = MagicMock(return_value="sub-new")
        assert lm.prepare_executor("s1", "root-agent", "t", True, "", True) == "sub-new"

    def test_session_lookup_failure_does_not_block_spawn(self):
        svc = MagicMock()
        svc.get.side_effect = RuntimeError("store down")
        lm = _with_root(_lm(session_svc=svc, max_spawn_depth=3))
        lm._instantiate_sub_agent = MagicMock(return_value="sub-new")
        assert lm.prepare_executor("s1", "root-agent", "t", True, "", True) == "sub-new"

    def test_permission_check_for_unknown_agent(self):
        lm = _with_root(_lm())
        st = lm._states["s1"]
        assert lm._check_spawn_permission(st, "ghost") == "Requesting agent not in registry"


# ── running_agent_count ───────────────────────────────────────────────────────

class TestRunningAgentCount:
    def test_counts_only_running_agents_with_a_task(self):
        lm = _with_root(_lm())
        reg = lm._states["s1"].agent_registry
        reg["a"] = AgentMeta("a", "root-agent", "t1", 1, "RUNNING")
        reg["b"] = AgentMeta("b", "root-agent", None, 1, "RUNNING")     # no task
        reg["c"] = AgentMeta("c", "root-agent", "t2", 1, "WAITING")     # not running
        assert lm.running_agent_count("s1") == 1

    def test_unknown_session_is_zero(self):
        assert _lm().running_agent_count("ghost") == 0

    def test_missing_state_is_zero(self):
        lm = _lm()
        lm.init_session("s1")
        del lm._states["s1"]
        assert lm.running_agent_count("s1") == 0


# ── release ───────────────────────────────────────────────────────────────────

class TestRelease:
    def test_recycles_everything_and_cleans_up(self):
        bus = _Bus()
        lm = _with_root(_lm(event_bus=bus))
        lm._states["s1"].agent_registry["sub-1"] = AgentMeta("sub-1", "root-agent", "t", 1, "FINISHED")
        lm.release("s1", "sub-1")
        assert "s1" not in lm._states
        assert bus.topics().count(LIFECYCLE_AGENT_RECYCLED) == 2

    def test_unknown_session_is_noop(self):
        _lm().release("ghost", "a")

    def test_missing_state_is_noop(self):
        lm = _lm()
        lm.init_session("s1")
        del lm._states["s1"]
        lm.release("s1", "a")

    def test_recycle_unknown_agent_is_noop(self):
        lm = _with_root(_lm())
        st = lm._states["s1"]
        lm._recycle(st, "s1", "ghost")
        assert st.concurrent_agents == 1


# ── run_agent / _execute ──────────────────────────────────────────────────────

class TestRunAgent:
    def _join_agent_threads(self):
        for t in threading.enumerate():
            if t.name.startswith("agent-"):
                t.join(timeout=5)

    def test_records_task_and_runs_loop(self):
        bus = _Bus()
        lm = _with_root(_lm(event_bus=bus))
        loop = MagicMock()
        lm.set_agent_loop(loop)
        lm._agent_store.get.return_value = {"status": "FINISHED"}
        lm.run_agent("s1", "root-agent", "task-1")
        self._join_agent_threads()
        loop.run.assert_called_once_with("s1", "root-agent", "task-1")
        assert TASK_EXECUTION_FINISHED in bus.topics()

    def test_publishes_failure_on_exception(self):
        bus = _Bus()
        lm = _with_root(_lm(event_bus=bus))
        loop = MagicMock()
        loop.run.side_effect = RuntimeError("boom")
        lm.set_agent_loop(loop)
        lm.run_agent("s1", "root-agent", "task-1")
        self._join_agent_threads()
        assert TASK_EXECUTION_FAILED in bus.topics()
        payload = [p for t, p in bus.events if t == TASK_EXECUTION_FAILED][0]
        assert payload["error"] == "boom"

    def test_after_cleanup_is_ignored(self):
        lm = _lm()
        loop = MagicMock()
        lm.set_agent_loop(loop)
        lm.run_agent("ghost", "a", "t")
        self._join_agent_threads()
        assert not loop.run.called

    def test_task_id_recorded_on_meta(self):
        lm = _with_root(_lm())
        lm.set_agent_loop(MagicMock())
        lm._agent_store.get.return_value = {"status": "FINISHED"}
        lm.run_agent("s1", "root-agent", "task-9")
        self._join_agent_threads()
        # meta survives because root is never recycled here
        assert lm._states["s1"].agent_registry["root-agent"].task_id == "task-9"

    def test_execute_without_loop_logs_and_returns(self):
        bus = _Bus()
        lm = _with_root(_lm(event_bus=bus))
        lm._execute("s1", "root-agent", "t")
        assert bus.events == []

    def test_untracked_agent_completes_silently(self):
        bus = _Bus()
        lm = _with_root(_lm(event_bus=bus))
        lm.set_agent_loop(MagicMock())
        lm._execute("s1", "daemon-agent", "t")      # not in registry
        assert TASK_EXECUTION_FINISHED not in bus.topics()

    def test_untracked_failure_is_also_silent(self):
        bus = _Bus()
        lm = _with_root(_lm(event_bus=bus))
        loop = MagicMock()
        loop.run.side_effect = RuntimeError("x")
        lm.set_agent_loop(loop)
        lm._execute("s1", "daemon-agent", "t")
        assert TASK_EXECUTION_FAILED not in bus.topics()


class TestSyncMetaStatus:
    def test_status_copied_from_store(self):
        lm = _with_root(_lm())
        lm._agent_store.get.return_value = {"status": "WAITING"}
        lm._sync_meta_status("s1", "root-agent")
        assert lm._states["s1"].agent_registry["root-agent"].status == "WAITING"

    def test_missing_status_key_defaults_to_finished(self):
        lm = _with_root(_lm())
        lm._agent_store.get.return_value = {"id": "root-agent"}   # truthy, no status
        lm._sync_meta_status("s1", "root-agent")
        assert lm._states["s1"].agent_registry["root-agent"].status == "FINISHED"

    def test_empty_agent_data_leaves_status_alone(self):
        lm = _with_root(_lm())
        lm._agent_store.get.return_value = {}                     # falsy -> no update
        lm._sync_meta_status("s1", "root-agent")
        assert lm._states["s1"].agent_registry["root-agent"].status == "RUNNING"

    def test_no_agent_data_leaves_status_alone(self):
        lm = _with_root(_lm())
        lm._agent_store.get.return_value = None
        lm._sync_meta_status("s1", "root-agent")
        assert lm._states["s1"].agent_registry["root-agent"].status == "RUNNING"

    def test_unknown_session_is_noop(self):
        _lm()._sync_meta_status("ghost", "a")

    def test_unregistered_agent_is_noop(self):
        lm = _with_root(_lm())
        lm._sync_meta_status("s1", "other-agent")


# ── spawn_daemon_agent ────────────────────────────────────────────────────────

class TestSpawnDaemonAgent:
    def test_spawns_and_runs_without_registering(self):
        lm = _with_root(_lm())
        lm._instantiate_sub_agent = MagicMock(return_value="daemon-1")
        lm.run_agent = MagicMock()
        lm.spawn_daemon_agent("s1", "root-agent", "t1", "worker", True)
        lm.run_agent.assert_called_once_with("s1", "daemon-1", "t1")
        assert "daemon-1" not in lm._states["s1"].agent_registry

    def test_instantiate_failure_skips_run(self):
        lm = _with_root(_lm())
        lm._instantiate_sub_agent = MagicMock(side_effect=RuntimeError("nope"))
        lm.run_agent = MagicMock()
        lm.spawn_daemon_agent("s1", "root-agent", "t1", "worker", True)
        assert not lm.run_agent.called


# ── _instantiate_sub_agent ────────────────────────────────────────────────────

class TestInstantiateSubAgent:
    def _store(self, parent: dict | None = None) -> MagicMock:
        store = MagicMock()
        store.get.return_value = parent if parent is not None else {
            "template_id": "tpl-parent",
            "template_name": "default",
            "actor": {"instruction_md": "A", "tools": ["read"]},
            "observer": {"instruction_md": "O", "tools": []},
            "settings": {"working_dir": "/work"},
        }
        return store

    def test_inherits_parent_config_without_template(self):
        store = self._store()
        lm = _lm(agent_store=store, max_spawn_depth=2)
        agent_id = lm._instantiate_sub_agent("s1", "t1", "root-agent", 1)
        saved = store.save.call_args.args[0]
        assert saved["id"] == agent_id
        assert saved["template_id"] == "tpl-parent"
        assert saved["name"] == "sub-agent-default"
        assert saved["actor"]["tools"] == ["read"]
        assert saved["settings"]["working_dir"] == "/work"
        assert saved["spawn_depth"] == 1
        assert saved["has_spawn_permission"] is True
        assert saved["status"] == "IDLE"

    def test_spawn_permission_false_at_depth_limit(self):
        lm = _lm(agent_store=self._store(), max_spawn_depth=1)
        lm._instantiate_sub_agent("s1", "t1", "root-agent", 1)
        saved = lm._agent_store.save.call_args.args[0]
        assert saved["has_spawn_permission"] is False

    def test_template_details_override_parent(self):
        loader = MagicMock()
        details = SimpleNamespace(
            actor_soul="ACTOR SOUL",
            observer_role="OBSERVER ROLE",
            actor_capability=SimpleNamespace(
                effective_tools=lambda: ["write"], required_mcp_servers=["srv"]),
            observer_capability=SimpleNamespace(
                effective_tools=lambda: [], required_mcp_servers=[]),
        )
        loader.get_details.return_value = (details, "tpl-planner")
        lm = _lm(agent_store=self._store(), template_loader=loader, max_spawn_depth=2)
        lm._instantiate_sub_agent("s1", "t1", "root-agent", 1, template_name="planner")
        saved = lm._agent_store.save.call_args.args[0]
        assert saved["template_id"] == "tpl-planner"
        assert saved["name"] == "sub-agent-planner"
        assert saved["actor"]["instruction_md"] == "ACTOR SOUL"
        assert saved["actor"]["mcp_servers"] == ["srv"]
        assert saved["observer"]["instruction_md"] == "OBSERVER ROLE"
        loader.get_details.assert_called_once_with("planner", "/work")

    def test_template_load_failure_falls_back_to_parent(self):
        loader = MagicMock()
        loader.get_details.side_effect = RuntimeError("missing template")
        lm = _lm(agent_store=self._store(), template_loader=loader, max_spawn_depth=2)
        lm._instantiate_sub_agent("s1", "t1", "root-agent", 1, template_name="ghost")
        saved = lm._agent_store.save.call_args.args[0]
        assert saved["template_id"] == "tpl-parent"

    def test_missing_parent_data_is_tolerated(self):
        lm = _lm(agent_store=self._store(parent={}), max_spawn_depth=2)
        lm._instantiate_sub_agent("s1", "t1", "root-agent", 1)
        saved = lm._agent_store.save.call_args.args[0]
        assert saved["name"] == "sub-agent-"
        assert saved["settings"]["working_dir"] == ""

    def test_none_parent_data_is_tolerated(self):
        store = MagicMock()
        store.get.return_value = None
        lm = _lm(agent_store=store, max_spawn_depth=2)
        lm._instantiate_sub_agent("s1", "t1", "root-agent", 1)
        assert store.save.called

    def test_memory_copied_when_inheriting(self):
        mem = MagicMock()
        mem.get_window.return_value = [{"role": "user", "content": "hi"}]
        mem.get_summary.return_value = SimpleNamespace(summary_text="prior")
        lm = _lm(agent_store=self._store(), memory_svc=mem, max_spawn_depth=2)
        child = lm._instantiate_sub_agent("s1", "t1", "root-agent", 1, inherit_memory=True)
        mem.bulk_write_messages.assert_called_once_with(child, [{"role": "user", "content": "hi"}])
        summary = mem.save_summary.call_args.args[1]
        assert summary.summary_text == "prior"
        assert summary.covered_up_to == 1
        assert summary.agent_id == child

    def test_memory_not_copied_when_not_inheriting(self):
        mem = MagicMock()
        lm = _lm(agent_store=self._store(), memory_svc=mem, max_spawn_depth=2)
        lm._instantiate_sub_agent("s1", "t1", "root-agent", 1, inherit_memory=False)
        assert not mem.bulk_write_messages.called

    def test_no_memory_service_is_fine(self):
        lm = _lm(agent_store=self._store(), memory_svc=None, max_spawn_depth=2)
        assert lm._instantiate_sub_agent("s1", "t1", "root-agent", 1, inherit_memory=True)

    def test_empty_parent_memory_writes_nothing(self):
        mem = MagicMock()
        mem.get_window.return_value = []
        mem.get_summary.return_value = None
        lm = _lm(agent_store=self._store(), memory_svc=mem, max_spawn_depth=2)
        lm._instantiate_sub_agent("s1", "t1", "root-agent", 1, inherit_memory=True)
        assert not mem.bulk_write_messages.called
        assert not mem.save_summary.called


class TestPersistAgentStatus:
    def test_updates_status_and_timestamp(self):
        store = MagicMock()
        store.get.return_value = {"id": "a1", "status": "IDLE", "updated_at": "old"}
        lm = _lm(agent_store=store)
        lm._persist_agent_status("s1", "a1", "RUNNING")
        saved = store.save.call_args.args[0]
        assert saved["status"] == "RUNNING"
        assert saved["updated_at"] != "old"

    def test_missing_agent_is_noop(self):
        store = MagicMock()
        store.get.return_value = None
        lm = _lm(agent_store=store)
        lm._persist_agent_status("s1", "ghost", "RUNNING")
        assert not store.save.called


class TestSetAgentLoop:
    def test_stores_loop(self):
        lm = _lm()
        loop = MagicMock()
        lm.set_agent_loop(loop)
        assert lm._agent_loop is loop
