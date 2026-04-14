"""Lifecycle Manager：统一调度中枢。

职责：
- 管理 agent 注册、并发计数、plan-time sub-agent 调度
- 启动 task 对应的 agent 线程（root 和 sub 走同一路径）
- Root agent：每个 task 完成后自动调度下一个，直到 session 结束
- Sub-agent：执行一个 plan-time spawned task 后退出，触发 root agent 继续
- Session RUNNING/FAILED 状态转换（SUCCEEDED 由 AgentLoop 内部处理）

不做：调用 LLM、执行 Task 业务逻辑、写 Blackboard 业务内容
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.common.utils import new_agent_id, now_iso
from app.domain.events.event_types import (
    AGENT_FAILED,
    AGENT_FINISHED,
    LIFECYCLE_AGENT_RECYCLED,
    LIFECYCLE_AGENT_SCHEDULED,
    SPAWN_REJECTED,
)
from app.domain.models.agent import Agent, LoopGuard
from app.domain.services.session_service import SessionService
from app.domain.services.task_service import TaskService
from app.orchestrator.task_manager import TaskManager
from app.storage.file.agent_store import AgentStore

if TYPE_CHECKING:
    from app.domain.events.event_bus import EventBus
    from app.domain.services.agent_template_service import AgentTemplateService
    from app.domain.services.memory_service import MemoryService
    from app.runtime.agent_loop import AgentLoop

logger = logging.getLogger(__name__)


@dataclass
class AgentMeta:
    """In-memory execution metadata for an active agent."""

    agent_id: str
    task_id: str | None
    spawn_depth: int
    status: str


@dataclass
class SpawnPlanItem:
    """Minimal spawn plan item used for guard checks."""

    title: str
    description: str


@dataclass
class LMState:
    """Per-session runtime state held by the lifecycle manager."""

    session_id: str
    root_agent_id: str = ""
    max_concurrent_agents: int = 5
    max_concurrent_tasks: int = 10
    max_retries: int = 1

    concurrent_agents: int = 0
    concurrent_tasks: int = 0

    agent_registry: dict[str, AgentMeta] = field(default_factory=dict)
    retry_counts: dict[str, int] = field(default_factory=dict)


class LifecycleManager:
    """Coordinate agent lifecycles and task scheduling for a session."""

    def __init__(
        self,
        session_svc: SessionService,
        task_svc: TaskService,
        agent_store: AgentStore,
        event_bus: "EventBus",
        task_manager: TaskManager,
        max_concurrent_agents: int = 5,
        max_concurrent_tasks: int = 10,
        max_spawn_depth: int = 1,
        max_retries: int = 1,
        template_svc: "AgentTemplateService | None" = None,
        memory_svc: "MemoryService | None" = None,
    ) -> None:
        self._session_svc = session_svc
        self._task_svc = task_svc
        self._agent_store = agent_store
        self._bus = event_bus
        self._task_manager = task_manager
        self._max_concurrent_agents = max_concurrent_agents
        self._max_concurrent_tasks = max_concurrent_tasks
        self._max_spawn_depth = max_spawn_depth
        self._max_retries = max_retries
        self._template_svc = template_svc
        self._memory_svc = memory_svc

        self._agent_loop: "AgentLoop | None" = None

        self._states: dict[str, LMState] = {}
        self._locks: dict[str, threading.Lock] = {}
        # Queue auto-spawn work until after the session lock is released.
        self._pending_auto_spawns: dict[str, list[tuple[str, str]]] = {}

        event_bus.subscribe(AGENT_FINISHED, self._on_agent_finished)
        event_bus.subscribe(AGENT_FAILED, self._on_agent_failed)

    def set_agent_loop(self, loop: "AgentLoop") -> None:
        """Late-inject AgentLoop to avoid a circular dependency."""

        self._agent_loop = loop

    def init_session(self, session_id: str) -> None:
        """Initialize per-session scheduler state."""

        self._states[session_id] = LMState(
            session_id=session_id,
            max_concurrent_agents=self._max_concurrent_agents,
            max_concurrent_tasks=self._max_concurrent_tasks,
            max_retries=self._max_retries,
        )
        self._locks[session_id] = threading.Lock()
        self._pending_auto_spawns.pop(session_id, None)
        logger.debug("LM: initialized session %s", session_id)

    def schedule_initial_task(
        self, session_id: str, agent_id: str, task_id: str
    ) -> None:
        """Register the root agent and schedule the first task."""

        state = self._states.get(session_id)
        if state is None:
            logger.error("LM: no state for session %s, cannot schedule", session_id)
            return

        try:
            task = self._task_svc.get(task_id)
        except Exception:
            logger.exception("LM: cannot load initial task %s", task_id)
            return

        with self._locks[session_id]:
            state.root_agent_id = agent_id
            if agent_id not in state.agent_registry:
                state.agent_registry[agent_id] = AgentMeta(
                    agent_id=agent_id,
                    task_id=None if task.settings.get("use_subagent") else task_id,
                    spawn_depth=0,
                    status="RUNNING",
                )
                state.concurrent_agents += 1
            else:
                state.agent_registry[agent_id].task_id = (
                    None if task.settings.get("use_subagent") else task_id
                )
                state.agent_registry[agent_id].status = "RUNNING"

        logger.info(
            "LM: scheduling initial task %s for root agent %s (session %s)",
            task_id,
            agent_id,
            session_id,
        )
        if task.settings.get("use_subagent"):
            self._auto_spawn_for_task(session_id, agent_id, task_id)
        else:
            self.schedule_task(session_id, agent_id, task_id)

    def schedule_task(
        self, session_id: str, agent_id: str, task_id: str
    ) -> None:
        """Run one task asynchronously in a daemon worker thread."""

        thread = threading.Thread(
            target=self._run_task_safe,
            args=(session_id, agent_id, task_id),
            name=f"agent-{agent_id[:8]}-{task_id[:8]}",
            daemon=True,
        )
        thread.start()

    def spawn_daemon_task(
        self, session_id: str, parent_agent_id: str, task_id: str
    ) -> None:
        """Spawn a daemon sub-agent for a hidden task, outside the normal lifecycle.

        The daemon agent is NOT registered in state.agent_registry, so its
        completion does not trigger root-agent continuation or session transitions.
        The task is pre-transitioned to ACTIVE to keep it out of list_pending.
        """
        try:
            task = self._task_svc.get(task_id)
        except Exception:
            logger.exception("LM: spawn_daemon_task: cannot load task %s", task_id)
            return

        template_name = str(task.settings.get("subagent_template", ""))
        try:
            sub_agent_id = self._instantiate_sub_agent(
                session_id=session_id,
                task_id=task_id,
                parent_agent_id=parent_agent_id,
                spawn_depth=1,
                inherit_memory=False,
                template_name=template_name,
            )
        except Exception:
            logger.exception("LM: spawn_daemon_task: failed to create sub-agent for task %s", task_id)
            return

        task.assigned_agent_id = sub_agent_id
        self._task_svc.save(task)
        # Pre-activate so list_pending never returns this task to the root agent.
        try:
            self._task_svc.transition(task_id, "ACTIVE")
        except Exception:
            logger.warning("LM: spawn_daemon_task: could not pre-activate task %s", task_id)

        logger.info("LM: spawning daemon sub-agent %s for task %s", sub_agent_id, task_id)
        self.schedule_task(session_id, sub_agent_id, task_id)

    def cleanup_session(self, session_id: str) -> None:
        """Release in-memory scheduler state for a finished session."""

        self._states.pop(session_id, None)
        self._locks.pop(session_id, None)
        self._pending_auto_spawns.pop(session_id, None)

    def _run_task_safe(
        self, session_id: str, agent_id: str, task_id: str
    ) -> None:
        """Execute one task and convert loop outcomes into lifecycle events."""

        if self._agent_loop is None:
            logger.error("LM: AgentLoop not set, cannot run task %s", task_id)
            return

        try:
            session = self._session_svc.get(session_id)
            if session.status == "QUEUED":
                self._session_svc.transition(session_id, "RUNNING")
        except Exception:
            # Best effort. A concurrent transition to RUNNING is harmless.
            pass

        try:
            task = self._task_svc.get(task_id)
            if task.status == "PENDING":
                self._task_svc.transition(task_id, "ACTIVE")
        except Exception:
            logger.exception("LM: failed to activate task %s before execution", task_id)
            return

        try:
            self._agent_loop.run(session_id, agent_id, task_id)
            self._task_manager.record_success(session_id)
            self._bus.publish(
                AGENT_FINISHED,
                {
                    "session_id": session_id,
                    "agent_id": agent_id,
                    "task_id": task_id,
                    "success": True,
                },
            )
        except Exception as e:
            logger.exception(
                "LM: agent %s failed on task %s: %s", agent_id, task_id, e
            )
            self._task_manager.record_failure(session_id)
            try:
                self._task_svc.fail(task_id, error=str(e))
            except Exception:
                pass
            self._bus.publish(
                AGENT_FAILED,
                {
                    "session_id": session_id,
                    "agent_id": agent_id,
                    "task_id": task_id,
                    "error": str(e),
                },
            )

    def _on_agent_finished(self, event_type: str, payload: dict) -> None:
        agent_id = payload.get("agent_id", "")
        session_id = payload.get("session_id", "")
        state = self._states.get(session_id)
        if state is None or agent_id not in state.agent_registry:
            return

        next_task_to_schedule = None
        root_resume_agent_id: str | None = None

        with self._locks[session_id]:
            meta = state.agent_registry.get(agent_id)
            if meta is None:
                return

            if meta.spawn_depth == 0:
                try:
                    session = self._session_svc.get(session_id)
                    session_status = session.status
                except Exception:
                    session_status = "FAILED"

                if session_status != "RUNNING":
                    state.agent_registry.pop(agent_id, None)
                    state.concurrent_agents -= 1
                    self._bus.publish(
                        LIFECYCLE_AGENT_RECYCLED,
                        {
                            "session_id": session_id,
                            "agent_id": agent_id,
                        },
                    )
                else:
                    pending = self._task_svc.list_pending(session_id)
                    if not pending:
                        # Plan A: task succeeded + no pending tasks → session complete
                        logger.info("Session %s: no pending tasks, marking SUCCEEDED", session_id)
                        try:
                            self._session_svc.transition(session_id, "SUCCEEDED")
                        except Exception:
                            logger.exception("LM: failed to transition session %s to SUCCEEDED", session_id)
                        state.agent_registry.pop(agent_id, None)
                        state.concurrent_agents -= 1
                        self._bus.publish(
                            LIFECYCLE_AGENT_RECYCLED,
                            {"session_id": session_id, "agent_id": agent_id},
                        )
                    else:
                        next_task_to_schedule = pending[0]

                    if next_task_to_schedule:
                        meta.task_id = next_task_to_schedule.id
                        if next_task_to_schedule.settings.get("use_subagent"):
                            reject = self._check_spawn_permission(
                                state,
                                agent_id,
                                [
                                    SpawnPlanItem(
                                        title=next_task_to_schedule.title,
                                        description=next_task_to_schedule.description,
                                    )
                                ],
                            )
                            if reject:
                                self._bus.publish(
                                    SPAWN_REJECTED,
                                    {
                                        "session_id": session_id,
                                        "agent_id": agent_id,
                                        "task_id": next_task_to_schedule.id,
                                        "reason": reject,
                                    },
                                )
                                logger.warning(
                                    "LM: auto-spawn rejected for task %s (%s); "
                                    "falling back to inline execution",
                                    next_task_to_schedule.id,
                                    reject,
                                )
                            else:
                                self._pending_auto_spawns.setdefault(
                                    session_id, []
                                ).append((agent_id, next_task_to_schedule.id))
                                next_task_to_schedule = None
                                meta.task_id = None
            else:
                finished_task_id = meta.task_id
                state.agent_registry.pop(agent_id, None)
                state.concurrent_agents -= 1
                if finished_task_id is not None:
                    state.concurrent_tasks -= 1

                logger.info(
                    "LM: sub-agent %s finished (task=%s)",
                    agent_id,
                    finished_task_id,
                )
                self._bus.publish(
                    LIFECYCLE_AGENT_RECYCLED,
                    {
                        "session_id": session_id,
                        "agent_id": agent_id,
                    },
                )

                root_id = state.root_agent_id
                root_meta = state.agent_registry.get(root_id)
                if root_meta is not None:
                    pending = self._task_svc.list_pending(session_id)
                    if pending:
                        root_meta.task_id = pending[0].id
                        next_task_to_schedule = pending[0]
                        root_resume_agent_id = root_id
                    else:
                        # Plan A: sub-agent finished + no pending → session complete
                        logger.info("Session %s: no pending tasks after sub-agent, marking SUCCEEDED", session_id)
                        try:
                            self._session_svc.transition(session_id, "SUCCEEDED")
                        except Exception:
                            logger.exception("LM: failed to transition session %s to SUCCEEDED", session_id)

        if next_task_to_schedule:
            sched_agent_id = root_resume_agent_id or agent_id
            self.schedule_task(session_id, sched_agent_id, next_task_to_schedule.id)

        auto_spawns = self._pending_auto_spawns.pop(session_id, [])
        for root_agent_id, task_id in auto_spawns:
            self._auto_spawn_for_task(session_id, root_agent_id, task_id)

    def _on_agent_failed(self, event_type: str, payload: dict) -> None:
        agent_id = payload.get("agent_id", "")
        session_id = payload.get("session_id", "")
        state = self._states.get(session_id)
        if state is None or agent_id not in state.agent_registry:
            return

        resume_root_agent_id: str | None = None
        resume_root_task: object = None

        with self._locks[session_id]:
            meta = state.agent_registry.pop(agent_id, None)
            if meta is None:
                return

            state.concurrent_agents -= 1
            if meta.task_id is not None:
                state.concurrent_tasks -= 1

            logger.warning(
                "LM: agent %s failed (spawn_depth=%d)", agent_id, meta.spawn_depth
            )

            if meta.spawn_depth == 0:
                failed_task_id = meta.task_id
                retry_count = state.retry_counts.get(failed_task_id, 0) if failed_task_id else state.max_retries
                if failed_task_id and retry_count < state.max_retries:
                    state.retry_counts[failed_task_id] = retry_count + 1
                    logger.info(
                        "LM: retrying task %s (attempt %d/%d) for session %s",
                        failed_task_id, retry_count + 1, state.max_retries, session_id,
                    )
                    try:
                        self._task_svc.retry(failed_task_id)
                    except Exception:
                        logger.exception("LM: failed to reset task %s for retry", failed_task_id)
                        try:
                            self._session_svc.transition(session_id, "FAILED")
                        except Exception:
                            pass
                    else:
                        # reschedule outside lock
                        resume_root_agent_id = agent_id
                        resume_root_task = self._task_svc.get(failed_task_id)
                        state.agent_registry[agent_id] = AgentMeta(
                            agent_id=agent_id,
                            task_id=failed_task_id,
                            spawn_depth=0,
                            status="RUNNING",
                        )
                        state.concurrent_agents += 1
                else:
                    try:
                        self._session_svc.transition(session_id, "FAILED")
                    except Exception:
                        pass
            else:
                root_id = state.root_agent_id
                root_meta = state.agent_registry.get(root_id)
                if root_meta is not None:
                    pending = self._task_svc.list_pending(session_id)
                    if not pending:
                        logger.warning(
                            "LM: sub-agent %s failed with no pending tasks, marking session %s FAILED",
                            agent_id, session_id,
                        )
                        try:
                            self._session_svc.transition(session_id, "FAILED")
                        except Exception:
                            pass
                    else:
                        next_task = pending[0]
                        root_meta.task_id = next_task.id
                        resume_root_agent_id = root_id
                        resume_root_task = next_task

                        if next_task.settings.get("use_subagent"):
                            reject = self._check_spawn_permission(
                                state,
                                root_id,
                                [SpawnPlanItem(title=next_task.title, description=next_task.description)],
                            )
                            if reject:
                                logger.warning(
                                    "LM: auto-spawn rejected for task %s (%s); "
                                    "falling back to inline execution",
                                    next_task.id,
                                    reject,
                                )
                            else:
                                self._pending_auto_spawns.setdefault(session_id, []).append(
                                    (root_id, next_task.id)
                                )
                                resume_root_agent_id = None
                                resume_root_task = None
                                root_meta.task_id = None

        if resume_root_agent_id and resume_root_task:
            self.schedule_task(session_id, resume_root_agent_id, resume_root_task.id)

        auto_spawns = self._pending_auto_spawns.pop(session_id, [])
        for spawning_agent_id, task_id in auto_spawns:
            self._auto_spawn_for_task(session_id, spawning_agent_id, task_id)

    def _auto_spawn_for_task(
        self,
        session_id: str,
        root_agent_id: str,
        task_id: str,
    ) -> None:
        """Route a use_subagent task to a dedicated child agent."""

        state = self._states.get(session_id)
        if state is None:
            logger.error("LM: _auto_spawn_for_task: no state for session %s", session_id)
            return

        with self._locks[session_id]:
            try:
                task = self._task_svc.get(task_id)
            except Exception:
                logger.exception("LM: _auto_spawn_for_task: cannot load task %s", task_id)
                return

            inherit_memory = task.settings.get("inherit_memory", True)
            template_name = str(task.settings.get("subagent_template", ""))
            spawn_depth = 1

            try:
                sub_agent_id = self._instantiate_sub_agent(
                    session_id=session_id,
                    task_id=task_id,
                    parent_agent_id=root_agent_id,
                    spawn_depth=spawn_depth,
                    inherit_memory=inherit_memory,
                    template_name=template_name,
                )
            except Exception:
                logger.exception(
                    "LM: _auto_spawn_for_task: failed to instantiate sub-agent for task %s",
                    task_id,
                )
                return

            task.assigned_agent_id = sub_agent_id
            self._task_svc.save(task)

            state.agent_registry[sub_agent_id] = AgentMeta(
                agent_id=sub_agent_id,
                task_id=task_id,
                spawn_depth=spawn_depth,
                status="RUNNING",
            )
            state.concurrent_agents += 1
            state.concurrent_tasks += 1

            self._bus.publish(
                LIFECYCLE_AGENT_SCHEDULED,
                {
                    "session_id": session_id,
                    "agent_id": sub_agent_id,
                    "task_id": task_id,
                },
            )

        logger.info(
            "LM: auto-spawned sub-agent %s for task %s",
            sub_agent_id,
            task_id,
        )
        self.schedule_task(session_id, sub_agent_id, task_id)

    def _check_spawn_permission(
        self,
        state: LMState,
        requesting_agent_id: str,
        plan: list[SpawnPlanItem],
    ) -> str:
        if not plan:
            return "Empty spawn plan"
        meta = state.agent_registry.get(requesting_agent_id)
        if meta is None:
            return "Requesting agent not in registry"
        if meta.spawn_depth >= self._max_spawn_depth:
            return f"Max spawn depth {self._max_spawn_depth} reached"
        if state.concurrent_agents + len(plan) > state.max_concurrent_agents:
            return (
                f"concurrent_agents limit ({state.max_concurrent_agents}) "
                f"would be exceeded by {len(plan)} new agents"
            )
        try:
            session = self._session_svc.get(state.session_id)
            if session.token_used > session.token_budget * 0.9:
                return "Token budget nearly exhausted (>90%)"
        except Exception:
            pass
        return ""

    def _instantiate_sub_agent(
        self,
        session_id: str,
        task_id: str,
        parent_agent_id: str,
        spawn_depth: int,
        inherit_memory: bool = True,
        template_name: str = "",
    ) -> str:
        """Create and persist a sub-agent instance."""

        from app.config.settings import get_settings

        settings = get_settings()
        parent_data = self._agent_store.get(parent_agent_id) or {}

        if template_name and self._template_svc is not None:
            tpl = self._template_svc.get_by_name(template_name)
            if tpl is not None:
                system_prompt = tpl.system_prompt
                soul_md = tpl.soul_md
                role_md = tpl.role_md
                act_tool_list = tpl.act_tool_list
                observe_tool_list = tpl.observe_tool_list
                skill_list = tpl.skill_list
                template_id = tpl.id
                agent_name = f"sub-agent-{template_name}"
                logger.debug(
                    "LM: sub-agent will use template '%s'", template_name
                )
            else:
                logger.warning(
                    "LM: template '%s' not found, falling back to parent config",
                    template_name,
                )
                system_prompt = parent_data.get(
                    "system_prompt", settings.agent_default_system_prompt
                )
                soul_md = parent_data.get("soul_md", "")
                role_md = parent_data.get("role_md", "")
                act_tool_list = parent_data.get("act_tool_list", [])
                observe_tool_list = parent_data.get("observe_tool_list", [])
                skill_list = parent_data.get("skill_list", [])
                template_id = parent_data.get("template_id")
                agent_name = f"sub-agent-{template_name}"
        else:
            system_prompt = parent_data.get(
                "system_prompt", settings.agent_default_system_prompt
            )
            soul_md = parent_data.get("soul_md", "")
            role_md = parent_data.get("role_md", "")
            act_tool_list = parent_data.get("act_tool_list", [])
            observe_tool_list = parent_data.get("observe_tool_list", [])
            skill_list = parent_data.get("skill_list", [])
            template_id = parent_data.get("template_id")
            agent_name = f"sub-agent-d{spawn_depth}"

        now = now_iso()
        agent_id = new_agent_id()
        agent = Agent(
            id=agent_id,
            session_id=session_id,
            template_id=template_id,
            name=agent_name,
            status="IDLE",
            soul_md=soul_md,
            role_md=role_md,
            act_tool_list=act_tool_list,
            observe_tool_list=observe_tool_list,
            skill_list=skill_list,
            soul_path=parent_data.get("soul_path"),
            loop_guard=LoopGuard(
                turns_used=0,
                max_turns=10,
                actor_max_tool_rounds=5,
            ),
            inherit_memory=inherit_memory,
            llm_name=parent_data.get(
                "llm_name", settings.agent_default_llm_name
            ),
            has_spawn_permission=False,
            spawn_depth=spawn_depth,
            parent_task_id=task_id,
            created_at=now,
            updated_at=now,
        )
        self._agent_store.save(agent.to_dict())

        if inherit_memory and self._memory_svc is not None:
            self._copy_memory(
                src_agent_id=parent_agent_id,
                dst_agent_id=agent_id,
                session_id=session_id,
            )

        logger.debug("LM: instantiated sub-agent %s for task %s", agent_id, task_id)
        return agent_id

    def _copy_memory(
        self,
        src_agent_id: str,
        dst_agent_id: str,
        session_id: str,
    ) -> None:
        """Copy the parent agent's memory snapshot into a child agent."""

        assert self._memory_svc is not None
        from app.domain.models.memory import MemorySummary

        parent_messages = self._memory_svc.get_window(src_agent_id, n=10000)
        for msg in parent_messages:
            self._memory_svc.append_message(
                agent_id=dst_agent_id,
                role=msg["role"],
                content=msg["content"],
                session_id=session_id,
            )

        parent_summary = self._memory_svc.get_summary(src_agent_id)
        if parent_summary:
            child_summary = MemorySummary(
                session_id=session_id,
                agent_id=dst_agent_id,
                summary_text=parent_summary.summary_text,
                covered_up_to=len(parent_messages),
                created_at=now_iso(),
            )
            self._memory_svc.save_summary(dst_agent_id, child_summary)

        logger.debug(
            "LM: copied %d messages from agent %s to sub-agent %s",
            len(parent_messages),
            src_agent_id,
            dst_agent_id,
        )

    def _persist_agent_status(self, agent_id: str, status: str) -> None:
        data = self._agent_store.get(agent_id)
        if data is None:
            return
        data["status"] = status
        data["updated_at"] = now_iso()
        self._agent_store.save(data)
