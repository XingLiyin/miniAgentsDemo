"""Lifecycle Manager：Agent 生命周期管理。

职责：
- 维护 session 下的 agent tree（registry 含 parent_id）
- 对外暴露两个高层操作供 TaskManager 调用：
    prepare_executor  —— 给定执行参数，返回装配好的 agent_id（reuse or spawn）
    release           —— session 无更多 task，清理 agent tree
- 执行层：run_agent 起线程，spawn_daemon_agent 旁路启动
- 实例化 sub-agent（含 memory 继承）

不做：task 状态转换、session 状态转换、调度决策、任务属性读取
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.common.utils import new_agent_id, now_iso
from app.domain.events.event_types import (
    LIFECYCLE_AGENT_RECYCLED,
    LIFECYCLE_AGENT_SCHEDULED,
    SPAWN_REJECTED,
    TASK_EXECUTION_FAILED,
    TASK_EXECUTION_FINISHED,
)
from app.domain.models.agent import Agent, LoopGuard
from app.domain.services.session_service import SessionService
from app.storage.file.agent_store import AgentStore

if TYPE_CHECKING:
    from app.domain.events.event_bus import EventBus
    from app.domain.services.agent_template_service import AgentTemplateService
    from app.domain.services.memory_service import MemoryService
    from app.domain.models.task import Task
    from app.runtime.agent_loop import AgentLoop

logger = logging.getLogger(__name__)


@dataclass
class AgentMeta:
    """In-memory execution metadata for an active agent."""

    agent_id: str
    parent_id: str | None       # None for root agent
    task_id: str | None
    spawn_depth: int
    status: str



@dataclass
class LMState:
    """Per-session runtime state held by the lifecycle manager."""

    session_id: str
    root_agent_id: str = ""
    max_concurrent_agents: int = 5
    max_concurrent_tasks: int = 10

    concurrent_agents: int = 0
    concurrent_tasks: int = 0

    agent_registry: dict[str, AgentMeta] = field(default_factory=dict)


class LifecycleManager:
    """Manage the agent tree for a session.

    Task scheduling decisions are made by TaskManager. LM assembles and
    tracks agents; TM calls prepare_executor / release / run_agent.
    """

    def __init__(
        self,
        session_svc: SessionService,
        agent_store: AgentStore,
        event_bus: "EventBus",
        max_concurrent_agents: int = 5,
        max_concurrent_tasks: int = 10,
        max_spawn_depth: int = 1,
        template_svc: "AgentTemplateService | None" = None,
        memory_svc: "MemoryService | None" = None,
        template_registry=None,
    ) -> None:
        self._session_svc = session_svc
        self._agent_store = agent_store
        self._bus = event_bus
        self._max_concurrent_agents = max_concurrent_agents
        self._max_concurrent_tasks = max_concurrent_tasks
        self._max_spawn_depth = max_spawn_depth
        self._template_svc = template_svc
        self._memory_svc = memory_svc
        self._template_registry = template_registry

        self._agent_loop: "AgentLoop | None" = None
        self._states: dict[str, LMState] = {}
        self._locks: dict[str, threading.Lock] = {}

    def set_agent_loop(self, loop: "AgentLoop") -> None:
        self._agent_loop = loop

    # ── Session lifecycle ─────────────────────────────────────────────────────

    def init_session(self, session_id: str) -> None:
        self._states[session_id] = LMState(
            session_id=session_id,
            max_concurrent_agents=self._max_concurrent_agents,
            max_concurrent_tasks=self._max_concurrent_tasks,
        )
        self._locks[session_id] = threading.Lock()
        logger.debug("LM: initialized session %s", session_id)

    def cleanup_session(self, session_id: str) -> None:
        self._states.pop(session_id, None)
        self._locks.pop(session_id, None)

    # ── Agent registration ────────────────────────────────────────────────────

    def register_root_agent(self, session_id: str, agent_id: str) -> None:
        """Register root agent as the tree root. Called by SessionManager on session start."""
        state = self._states.get(session_id)
        if state is None:
            logger.error("LM: register_root_agent: no state for session %s", session_id)
            return
        with self._locks[session_id]:
            state.root_agent_id = agent_id
            if agent_id not in state.agent_registry:
                state.agent_registry[agent_id] = AgentMeta(
                    agent_id=agent_id,
                    parent_id=None,
                    task_id=None,
                    spawn_depth=0,
                    status="RUNNING",
                )
                state.concurrent_agents += 1
            else:
                state.agent_registry[agent_id].status = "RUNNING"

    # ── High-level operations (called by TaskManager) ────────────────────────

    def prepare_executor(
        self,
        session_id: str,
        finished_agent_id: str,
        task_id: str,
        use_subagent: bool,
        template_id: str,
        inherit_memory: bool,
    ) -> str | None:
        """Return a fully assembled agent_id ready to execute the next task.

        Settle the finished agent, then determine the executor:
        - WAITING base: if next task is the agent's own suspended task → resume directly;
          otherwise → force-spawn a child (the WAITING agent is the tree parent).
        - Normal base: reuse inline (use_subagent=False) or spawn child (use_subagent=True).
        Falls back to inline execution if spawn is denied.
        Returns None if the agent tree is in an unexpected state.
        """
        lock = self._locks.get(session_id)
        if lock is None:
            return None

        sub_agent_id: str | None = None

        with lock:
            state = self._states.get(session_id)
            if state is None:
                return None

            base_executor = self._settle_executor(state, session_id, finished_agent_id)
            if base_executor is None:
                return None

            base_meta = state.agent_registry.get(base_executor)
            if base_meta is None:
                return None

            # WAITING agent: has a suspended task waiting for its children to finish.
            if base_meta.status == "WAITING":
                if base_meta.task_id == task_id:
                    # The suspended task is being resumed — agent runs it directly.
                    base_meta.status = "RUNNING"
                    return base_executor
                else:
                    # Child task dispatched to this waiting agent — must spawn a child.
                    use_subagent = True

            if not use_subagent:
                return base_executor

            reject = self._check_spawn_permission(state, base_executor)
            if reject:
                logger.warning("LM: spawn rejected for executor %s (%s), falling back to inline", base_executor, reject)
                self._bus.publish(
                    SPAWN_REJECTED,
                    {"session_id": session_id, "agent_id": base_executor, "reason": reject},
                )
                return base_executor

            spawn_depth = base_meta.spawn_depth + 1

            try:
                sub_agent_id = self._instantiate_sub_agent(
                    session_id=session_id,
                    task_id=task_id,
                    parent_agent_id=base_executor,
                    spawn_depth=spawn_depth,
                    inherit_memory=inherit_memory,
                    template_id=template_id,
                )
            except Exception:
                logger.exception("LM: prepare_executor: failed to instantiate sub-agent")
                return base_executor  # fallback

            state.agent_registry[sub_agent_id] = AgentMeta(
                agent_id=sub_agent_id,
                parent_id=base_executor,
                task_id=None,
                spawn_depth=spawn_depth,
                status="RUNNING",
            )
            state.concurrent_agents += 1
            state.concurrent_tasks += 1
            self._bus.publish(
                LIFECYCLE_AGENT_SCHEDULED,
                {"session_id": session_id, "agent_id": sub_agent_id},
            )

        logger.info("LM: prepared sub-agent %s (depth=%d, parent=%s)", sub_agent_id, spawn_depth, base_executor)
        return sub_agent_id

    def release(self, session_id: str, finished_agent_id: str) -> None:
        """Session has no more tasks. Recycle all remaining agents in the tree."""
        lock = self._locks.get(session_id)
        if lock is None:
            return
        with lock:
            state = self._states.get(session_id)
            if state is None:
                return
            for agent_id in list(state.agent_registry.keys()):
                self._recycle(state, session_id, agent_id)
        self.cleanup_session(session_id)

    def run_agent(self, session_id: str, agent_id: str, task_id: str) -> None:
        """Start a daemon thread to run agent_loop for (agent, task)."""
        with self._locks[session_id]:
            state = self._states.get(session_id)
            if state and agent_id in state.agent_registry:
                state.agent_registry[agent_id].task_id = task_id
        thread = threading.Thread(
            target=self._execute,
            args=(session_id, agent_id, task_id),
            name=f"agent-{agent_id[:8]}-{task_id[:8]}",
            daemon=True,
        )
        thread.start()

    def spawn_daemon_agent(
        self,
        session_id: str,
        parent_agent_id: str,
        task_id: str,
        template_id: str,
        inherit_memory: bool,
    ) -> None:
        """Spawn a daemon sub-agent. Not registered in registry — completion is ignored."""
        try:
            sub_agent_id = self._instantiate_sub_agent(
                session_id=session_id,
                task_id=task_id,
                parent_agent_id=parent_agent_id,
                spawn_depth=1,
                inherit_memory=inherit_memory,
                template_id=template_id,
            )
        except Exception:
            logger.exception("LM: spawn_daemon_agent: failed to create agent for task %s", task_id)
            return

        logger.info("LM: spawning daemon agent %s for task %s", sub_agent_id, task_id)
        self.run_agent(session_id, sub_agent_id, task_id)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _settle_executor(self, state: LMState, session_id: str, finished_agent_id: str) -> str | None:
        """Determine the base executor for the next task.

        - Root agent (depth=0): never recycled mid-session, always returned as-is.
        - WAITING sub-agent: has a suspended task awaiting children; keep alive, return as-is.
        - FINISHED sub-agent: recycle it and return its direct parent (not always root).

        Must be called under self._locks[session_id].
        """
        meta = state.agent_registry.get(finished_agent_id)
        if meta is None:
            return None
        if meta.spawn_depth == 0:
            return finished_agent_id
        if meta.status == "WAITING":
            return finished_agent_id
        parent_id = meta.parent_id or state.root_agent_id
        self._recycle(state, session_id, finished_agent_id)
        return parent_id

    def _recycle(self, state: LMState, session_id: str, agent_id: str) -> None:
        """Remove agent from registry and publish RECYCLED. Must be called under lock."""
        meta = state.agent_registry.pop(agent_id, None)
        if meta is None:
            return
        state.concurrent_agents -= 1
        if meta.task_id is not None and meta.spawn_depth > 0:
            state.concurrent_tasks -= 1
        self._bus.publish(LIFECYCLE_AGENT_RECYCLED, {"session_id": session_id, "agent_id": agent_id})

    # ── Internal execution ────────────────────────────────────────────────────

    def _execute(self, session_id: str, agent_id: str, task_id: str) -> None:
        """Run agent_loop and publish completion event. No task/session state changes.

        Only publishes events for tracked agents (in registry). Daemon agents are
        not in the registry and complete silently — TM is never notified.
        """
        if self._agent_loop is None:
            logger.error("LM: AgentLoop not set, cannot run task %s", task_id)
            return
        try:
            self._agent_loop.run(session_id, agent_id, task_id)
            self._sync_meta_status(session_id, agent_id)
            if self._is_tracked(session_id, agent_id):
                self._bus.publish(
                    TASK_EXECUTION_FINISHED,
                    {"session_id": session_id, "agent_id": agent_id, "task_id": task_id},
                )
        except Exception as e:
            logger.exception("LM: agent %s failed on task %s: %s", agent_id, task_id, e)
            if self._is_tracked(session_id, agent_id):
                self._bus.publish(
                    TASK_EXECUTION_FAILED,
                    {"session_id": session_id, "agent_id": agent_id, "task_id": task_id, "error": str(e)},
                )

    def _sync_meta_status(self, session_id: str, agent_id: str) -> None:
        """Sync AgentMeta.status from agent_store after a loop run completes."""
        lock = self._locks.get(session_id)
        if not lock:
            return
        with lock:
            state = self._states.get(session_id)
            if not state or agent_id not in state.agent_registry:
                return
            agent_data = self._agent_store.get(session_id, agent_id)
            if agent_data:
                state.agent_registry[agent_id].status = agent_data.get("status", "FINISHED")

    def _is_tracked(self, session_id: str, agent_id: str) -> bool:
        state = self._states.get(session_id)
        return state is not None and agent_id in state.agent_registry

    def _check_spawn_permission(self, state: LMState, requesting_agent_id: str) -> str:
        """Return "" if spawning one child agent is allowed, else a reason string."""
        meta = state.agent_registry.get(requesting_agent_id)
        if meta is None:
            return "Requesting agent not in registry"
        if meta.spawn_depth >= self._max_spawn_depth:
            return f"Max spawn depth {self._max_spawn_depth} reached"
        if state.concurrent_agents + 1 > state.max_concurrent_agents:
            return f"concurrent_agents limit ({state.max_concurrent_agents}) would be exceeded"
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
        template_id: str = "",
    ) -> str:
        from app.config.settings import get_settings

        settings = get_settings()
        parent_data = self._agent_store.get(session_id, parent_agent_id) or {}

        if template_id and self._template_svc is not None:
            try:
                tpl = self._template_svc.get(template_id)
                act_tool_list = tpl.act_tool_list
                observe_tool_list = tpl.observe_tool_list
                agent_name = f"sub-agent-{tpl.name}"
                content = self._template_registry.load_content_by_id(template_id) if self._template_registry else None
                soul_md = content.soul_md if content else ""
                role_md = content.role_md if content else ""
            except Exception:
                logger.warning("LM: template id '%s' not found, falling back to parent config", template_id)
                soul_md = parent_data.get("soul_md", "")
                role_md = parent_data.get("role_md", "")
                act_tool_list = parent_data.get("act_tool_list", [])
                observe_tool_list = parent_data.get("observe_tool_list", [])
                template_id = parent_data.get("template_id") or ""
                agent_name = f"sub-agent-d{spawn_depth}"
        else:
            soul_md = parent_data.get("soul_md", "")
            role_md = parent_data.get("role_md", "")
            act_tool_list = parent_data.get("act_tool_list", [])
            observe_tool_list = parent_data.get("observe_tool_list", [])
            template_id = parent_data.get("template_id") or ""
            agent_name = f"sub-agent-d{spawn_depth}"

        now = now_iso()
        agent_id = new_agent_id()
        parent_settings = parent_data.get("settings") or {}
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
            skill_list=parent_data.get("skill_list", []),
            soul_path=parent_data.get("soul_path"),
            loop_guard=LoopGuard(actor_max_tool_rounds=50),
            inherit_memory=inherit_memory,
            has_spawn_permission=(spawn_depth < self._max_spawn_depth),
            spawn_depth=spawn_depth,
            settings={"working_dir": parent_settings.get("working_dir", "")},
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

    def _copy_memory(self, src_agent_id: str, dst_agent_id: str, session_id: str) -> None:
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

    def _persist_agent_status(self, session_id: str, agent_id: str, status: str) -> None:
        data = self._agent_store.get(session_id, agent_id)
        if data is None:
            return
        data["status"] = status
        data["updated_at"] = now_iso()
        self._agent_store.save(data)
