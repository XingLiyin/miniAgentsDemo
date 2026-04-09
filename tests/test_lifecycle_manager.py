from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.domain.events.event_bus import EventBus
from app.domain.models.task import Task
from app.orchestrator.lifecycle_manager import LifecycleManager


def _make_task(
    task_id: str,
    *,
    task_type: str = "plan",
    status: str = "PENDING",
    inputs: dict | None = None,
) -> Task:
    return Task(
        id=task_id,
        session_id="session-1",
        creator_agent_id="root-agent",
        assigned_agent_id="root-agent",
        type=task_type,
        title="Test task",
        status=status,
        description="desc",
        inputs=inputs or {},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


def test_auto_spawn_uses_task_subagent_template() -> None:
    session_svc = MagicMock()
    task_svc = MagicMock()
    agent_store = MagicMock()
    task_manager = MagicMock()
    event_bus = EventBus()

    lm = LifecycleManager(
        session_svc=session_svc,
        task_svc=task_svc,
        agent_store=agent_store,
        event_bus=event_bus,
        task_manager=task_manager,
    )
    lm.init_session("session-1")

    task = _make_task(
        "task-1",
        inputs={
            "use_subagent": True,
            "inherit_memory": True,
            "subagent_template": "planner",
        },
    )
    task_svc.get.return_value = task

    captured: dict[str, object] = {}

    def _capture_subagent(**kwargs):
        captured.update(kwargs)
        return "sub-agent-1"

    lm._instantiate_sub_agent = _capture_subagent  # type: ignore[method-assign]
    lm.schedule_task = MagicMock()  # type: ignore[method-assign]

    lm._auto_spawn_for_task("session-1", "root-agent", "task-1")

    assert captured["template_name"] == "planner"
    assert task.assigned_agent_id == "sub-agent-1"
    lm.schedule_task.assert_called_once_with("session-1", "sub-agent-1", "task-1")


def test_run_task_safe_activates_task_before_agent_loop_and_starts_session() -> None:
    session_svc = MagicMock()
    task_svc = MagicMock()
    agent_store = MagicMock()
    task_manager = MagicMock()
    event_bus = EventBus()

    lm = LifecycleManager(
        session_svc=session_svc,
        task_svc=task_svc,
        agent_store=agent_store,
        event_bus=event_bus,
        task_manager=task_manager,
    )

    task = _make_task("task-1", task_type="atomic", status="PENDING")
    task_svc.get.return_value = task
    session_svc.get.return_value = SimpleNamespace(status="QUEUED")

    order: list[str] = []

    def _transition_task(task_id: str, to_status: str):
        order.append(f"task:{to_status}")
        task.status = to_status
        return task

    def _run(*args):
        order.append("loop:run")
        raise RuntimeError("boom")

    def _fail(*args, **kwargs):
        order.append("task:FAILED")
        task.status = "FAILED"
        return task

    task_svc.transition.side_effect = _transition_task
    task_svc.fail.side_effect = _fail
    session_svc.transition.side_effect = lambda *args: order.append("session:RUNNING")
    lm.set_agent_loop(SimpleNamespace(run=_run))

    lm._run_task_safe("session-1", "sub-agent-1", "task-1")

    assert order[:2] == ["session:RUNNING", "task:ACTIVE"]
    assert "loop:run" in order
    assert "task:FAILED" in order
    session_svc.transition.assert_called_with("session-1", "RUNNING")
    task_svc.transition.assert_called_once_with("task-1", "ACTIVE")
    task_manager.record_failure.assert_called_once_with("session-1")
