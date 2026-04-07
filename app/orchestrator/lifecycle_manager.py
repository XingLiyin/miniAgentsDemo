"""Lifecycle Manager：管理 sub-agent 实例化、DAG 调度、并发控制与回收。

职责边界：
- 不调用 LLM
- 不执行 Task 业务逻辑
- 不写 Blackboard 业务内容
- 只基于规则（DAG 依赖、并发计数、重试策略）做决策
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.common.errors import AppError
from app.common.utils import new_agent_id, now_iso
from app.domain.events.event_types import (
    AGENT_FAILED,
    AGENT_FINISHED,
    AGENT_RESUME,
    AGENT_WAITING,
    LIFECYCLE_AGENT_RECYCLED,
    LIFECYCLE_AGENT_SCHEDULED,
    LIFECYCLE_TASK_READY,
    SPAWN_APPROVED,
    SPAWN_REJECTED,
    TASK_FAILED,
    TASK_FINISHED,
)
from app.domain.models.agent import Agent, LoopGuard
from app.domain.services.session_service import SessionService
from app.domain.services.task_service import TaskService
from app.storage.file.agent_store import AgentStore

if TYPE_CHECKING:
    from app.domain.events.event_bus import EventBus
    from app.runtime.sub_agent_runner import SubAgentRunner

logger = logging.getLogger(__name__)


# ── 数据结构 ───────────────────────────────────────────────────────────────


@dataclass
class AgentMeta:
    """agent_registry 中每个存活 agent 的运行时元数据。"""
    agent_id: str
    task_id: str | None          # 本 agent 正在执行的 task（root agent 为 None）
    spawn_depth: int
    status: str                  # RUNNING | WAITING
    waiting_for: set[str] = field(default_factory=set)         # 还未完成的子 task_id 集合
    all_spawned_task_ids: list[str] = field(default_factory=list)  # spawn 时的完整列表（不变）
    resume_hint: str = ""


@dataclass
class SpawnPlanItem:
    """spawn_agents 工具 plan 中的单个条目。"""
    title: str
    description: str
    deps: list[str] = field(default_factory=list)   # 依赖的 title 列表


@dataclass
class SpawnResult:
    """handle_spawn_requested 的返回值。"""
    approved: bool
    reject_reason: str = ""
    task_results: list[dict[str, Any]] = field(default_factory=list)
    resume_hint: str = ""

    def to_content(self) -> str:
        import json
        if not self.approved:
            return f"spawn_agents rejected: {self.reject_reason}"
        return json.dumps(
            {
                "status": "COMPLETED",
                "resume_hint": self.resume_hint,
                "task_results": self.task_results,
            },
            ensure_ascii=False,
        )


@dataclass
class LMState:
    """单个 session 的 Lifecycle Manager 运行时状态。"""
    session_id: str
    max_concurrent_agents: int = 5
    max_concurrent_tasks: int = 10
    failure_threshold: int = 3
    max_retries: int = 1

    concurrent_agents: int = 0
    concurrent_tasks: int = 0
    failure_counter: int = 0

    # agent_id → AgentMeta（包含 root agent）
    agent_registry: dict[str, AgentMeta] = field(default_factory=dict)

    # task_id → 剩余未完成依赖数
    dep_counter: dict[str, int] = field(default_factory=dict)
    # finished_task_id → 下游 task_id 集合（反向依赖索引）
    downstream: dict[str, set[str]] = field(default_factory=dict)
    # 依赖已满足但并发余量不足，等待调度的 task_id 队列
    pending_queue: list[str] = field(default_factory=list)
    # sub-task_id → 所属 WAITING agent_id（用于唤醒检查）
    task_to_waiting_agent: dict[str, str] = field(default_factory=dict)
    # sub-task_id → 当前重试次数
    retry_counts: dict[str, int] = field(default_factory=dict)


# ── LifecycleManager ──────────────────────────────────────────────────────


class LifecycleManager:
    """管理 sub-agent 全生命周期与 DAG 调度。"""

    def __init__(
        self,
        session_svc: SessionService,
        task_svc: TaskService,
        agent_store: AgentStore,
        event_bus: "EventBus",
        sub_agent_runner: "SubAgentRunner",
        max_concurrent_agents: int = 5,
        max_concurrent_tasks: int = 10,
        max_spawn_depth: int = 1,
        max_retries: int = 1,
        spawn_timeout_sec: float = 3600.0,
    ) -> None:
        self._session_svc = session_svc
        self._task_svc = task_svc
        self._agent_store = agent_store
        self._bus = event_bus
        self._runner = sub_agent_runner
        self._max_concurrent_agents = max_concurrent_agents
        self._max_concurrent_tasks = max_concurrent_tasks
        self._max_spawn_depth = max_spawn_depth
        self._max_retries = max_retries
        self._spawn_timeout_sec = spawn_timeout_sec

        # 按 session 分片的状态与锁
        self._states: dict[str, LMState] = {}
        self._locks: dict[str, threading.Lock] = {}

        # WAITING agent_id → threading.Event（唤醒阻塞线程）
        self._resume_events: dict[str, threading.Event] = {}
        # WAITING agent_id → SpawnResult（唤醒前填充）
        self._resume_payloads: dict[str, SpawnResult] = {}

        event_bus.subscribe(AGENT_FINISHED, self._on_agent_finished)
        event_bus.subscribe(AGENT_FAILED, self._on_agent_failed)
        event_bus.subscribe(TASK_FINISHED, self._on_task_finished)
        event_bus.subscribe(TASK_FAILED, self._on_task_failed)

    # ── 对外接口 ──────────────────────────────────────────────────────────

    def init_session(self, session_id: str) -> None:
        """创建 session 时调用，初始化 LMState 与锁。"""
        self._states[session_id] = LMState(
            session_id=session_id,
            max_concurrent_agents=self._max_concurrent_agents,
            max_concurrent_tasks=self._max_concurrent_tasks,
            max_retries=self._max_retries,
        )
        self._locks[session_id] = threading.Lock()
        logger.debug("LM: initialized session %s", session_id)

    def register_root(self, session_id: str, agent_id: str) -> None:
        """注册 root agent 并在后台线程启动其 AgentLoop。

        替代 SessionManager.schedule_loop()。
        """
        state = self._states.get(session_id)
        if state is None:
            logger.error("LM: no state for session %s, cannot register root", session_id)
            return
        with self._locks[session_id]:
            state.agent_registry[agent_id] = AgentMeta(
                agent_id=agent_id,
                task_id=None,
                spawn_depth=0,
                status="RUNNING",
            )
            state.concurrent_agents += 1
        logger.info("LM: registered root agent %s for session %s", agent_id, session_id)
        self._runner.run_root_async(session_id, agent_id)

    def handle_spawn_requested(
        self,
        session_id: str,
        requesting_agent_id: str,
        requesting_task_id: str,
        plan: list[SpawnPlanItem],
        resume_hint: str = "",
    ) -> SpawnResult:
        """spawn_agents 工具同步调用此方法。

        通过权限检查后，挂起请求 task / agent，调度子任务，然后阻塞直到所有
        子任务完成（或超时）。返回 SpawnResult，内含子任务汇总结果。
        REJECTED 时立即返回，agent 在同一轮 Loop 内降级处理。
        """
        state = self._states.get(session_id)
        if state is None:
            return SpawnResult(approved=False, reject_reason="Session not managed by LM")

        with self._locks[session_id]:
            reject = self._check_spawn_permission(state, requesting_agent_id, plan)
            if reject:
                self._bus.publish(SPAWN_REJECTED, {
                    "session_id": session_id,
                    "agent_id": requesting_agent_id,
                    "reason": reject,
                })
                return SpawnResult(approved=False, reject_reason=reject)

            created_task_ids = self._create_sub_tasks(
                session_id, requesting_agent_id, requesting_task_id, plan, state
            )

            # 挂起请求 task
            try:
                self._task_svc.transition(requesting_task_id, "SUSPENDED")
            except AppError:
                logger.warning("LM: could not SUSPEND task %s", requesting_task_id)

            # 更新 agent 元数据 → WAITING
            meta = state.agent_registry[requesting_agent_id]
            meta.status = "WAITING"
            meta.waiting_for = set(created_task_ids)
            meta.all_spawned_task_ids = list(created_task_ids)
            meta.resume_hint = resume_hint

            self._persist_agent_status(requesting_agent_id, "WAITING", created_task_ids)

            # 创建唤醒事件
            resume_event = threading.Event()
            self._resume_events[requesting_agent_id] = resume_event

            self._bus.publish(AGENT_WAITING, {
                "session_id": session_id,
                "agent_id": requesting_agent_id,
                "spawned_task_ids": created_task_ids,
            })
            self._bus.publish(SPAWN_APPROVED, {
                "session_id": session_id,
                "agent_id": requesting_agent_id,
                "task_count": len(created_task_ids),
            })

            # 调度无依赖的子任务
            self._drain_queue_locked(session_id, state)

        # ── 锁已释放 —— 阻塞等待子任务完成 ──────────────────────────────
        logger.info(
            "LM: agent %s waiting for %d sub-tasks",
            requesting_agent_id, len(created_task_ids),
        )
        resumed = resume_event.wait(timeout=self._spawn_timeout_sec)

        with self._locks[session_id]:
            result = self._resume_payloads.pop(requesting_agent_id, None)
            self._resume_events.pop(requesting_agent_id, None)

        if not resumed or result is None:
            logger.warning("LM: spawn timed out for agent %s", requesting_agent_id)
            return SpawnResult(approved=False, reject_reason="Spawn timed out")
        return result

    def cleanup_session(self, session_id: str) -> None:
        """Session 结束后释放 LM 内存状态。"""
        self._states.pop(session_id, None)
        self._locks.pop(session_id, None)

    # ── 事件处理器 ────────────────────────────────────────────────────────

    def _on_task_finished(self, event_type: str, payload: dict) -> None:
        task_id = payload.get("task_id", "")
        session_id = payload.get("session_id", "")
        state = self._states.get(session_id)
        if state is None:
            return
        # 只处理 LM 管理的 sub-task
        if task_id not in state.dep_counter and task_id not in state.task_to_waiting_agent:
            return

        with self._locks[session_id]:
            state.failure_counter = 0

            # DAG 传播：更新下游 task 的剩余依赖计数
            for downstream_id in state.downstream.get(task_id, set()):
                if downstream_id in state.dep_counter:
                    state.dep_counter[downstream_id] -= 1
                    if state.dep_counter[downstream_id] == 0:
                        state.pending_queue.append(downstream_id)
                        self._bus.publish(LIFECYCLE_TASK_READY, {
                            "session_id": session_id,
                            "task_id": downstream_id,
                        })

            state.dep_counter.pop(task_id, None)

            # 检查是否可以唤醒 WAITING agent
            self._try_resume_waiting_agent_locked(session_id, state, task_id)

            self._drain_queue_locked(session_id, state)

    def _on_agent_finished(self, event_type: str, payload: dict) -> None:
        agent_id = payload.get("agent_id", "")
        session_id = payload.get("session_id", "")
        state = self._states.get(session_id)
        if state is None or agent_id not in state.agent_registry:
            return

        with self._locks[session_id]:
            meta = state.agent_registry.pop(agent_id, None)
            if meta is None:
                return
            state.concurrent_agents -= 1
            if meta.task_id is not None:
                state.concurrent_tasks -= 1

            logger.info(
                "LM: agent %s finished (depth=%d), concurrent_agents=%d",
                agent_id, meta.spawn_depth, state.concurrent_agents,
            )
            self._bus.publish(LIFECYCLE_AGENT_RECYCLED, {
                "session_id": session_id,
                "agent_id": agent_id,
            })
            self._drain_queue_locked(session_id, state)

    def _on_task_failed(self, event_type: str, payload: dict) -> None:
        task_id = payload.get("task_id", "")
        session_id = payload.get("session_id", "")
        state = self._states.get(session_id)
        if state is None:
            return
        if task_id not in state.dep_counter and task_id not in state.task_to_waiting_agent:
            return

        with self._locks[session_id]:
            state.failure_counter += 1
            retry_count = state.retry_counts.get(task_id, 0)

            if retry_count < state.max_retries:
                # 重试：重置 task 状态并重新入队
                state.retry_counts[task_id] = retry_count + 1
                state.dep_counter[task_id] = 0
                state.pending_queue.append(task_id)
                try:
                    task = self._task_svc.get(task_id)
                    task.status = "PENDING"
                    task.error = None
                    task.retry_count = retry_count + 1
                    self._task_svc.save(task)
                except Exception:
                    pass
                logger.info("LM: retrying task %s (attempt %d)", task_id, retry_count + 1)
                self._drain_queue_locked(session_id, state)
            else:
                # 重试耗尽 → 告知 WAITING agent 失败
                state.retry_counts.pop(task_id, None)
                state.dep_counter.pop(task_id, None)
                self._fail_waiting_agent_locked(session_id, state, task_id, retry_count + 1)

    def _on_agent_failed(self, event_type: str, payload: dict) -> None:
        agent_id = payload.get("agent_id", "")
        session_id = payload.get("session_id", "")
        state = self._states.get(session_id)
        if state is None or agent_id not in state.agent_registry:
            return

        with self._locks[session_id]:
            meta = state.agent_registry.pop(agent_id, None)
            if meta is None:
                return
            state.concurrent_agents -= 1
            if meta.task_id is not None:
                state.concurrent_tasks -= 1

            logger.warning("LM: agent %s failed", agent_id)
            # task 失败由 SubAgentRunner 调用 task_svc.fail() 触发，
            # 会发布 TASK_FAILED 事件，由 _on_task_failed 处理重试。
            self._drain_queue_locked(session_id, state)

    # ── 私有调度方法 ──────────────────────────────────────────────────────

    def _check_spawn_permission(
        self,
        state: LMState,
        requesting_agent_id: str,
        plan: list[SpawnPlanItem],
    ) -> str:
        """检查 spawn 权限与资源余量。返回 reject_reason 或空串（通过）。"""
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

    def _create_sub_tasks(
        self,
        session_id: str,
        requesting_agent_id: str,
        requesting_task_id: str,
        plan: list[SpawnPlanItem],
        state: LMState,
    ) -> list[str]:
        """创建子 Task，建立 DAG 索引，返回 task_id 列表。锁已持有时调用。"""
        name_to_id: dict[str, str] = {}
        created_ids: list[str] = []

        for item in plan:
            dep_ids = [name_to_id[d] for d in item.deps if d in name_to_id]
            sub_task = self._task_svc.create(
                session_id=session_id,
                agent_id=requesting_agent_id,
                task_type="atomic",
                title=item.title,
                description=item.description,
            )
            # 持久化 DAG 字段
            sub_task.dag_deps = dep_ids
            sub_task.parent_task_id = requesting_task_id
            self._task_svc.save(sub_task)

            name_to_id[item.title] = sub_task.id
            created_ids.append(sub_task.id)

            # DAG 计数器
            state.dep_counter[sub_task.id] = len(dep_ids)
            for dep_id in dep_ids:
                state.downstream.setdefault(dep_id, set()).add(sub_task.id)
            state.task_to_waiting_agent[sub_task.id] = requesting_agent_id
            state.retry_counts[sub_task.id] = 0

        # 无依赖的 task 立即入队
        for tid in created_ids:
            if state.dep_counter[tid] == 0:
                state.pending_queue.append(tid)
                self._bus.publish(LIFECYCLE_TASK_READY, {
                    "session_id": session_id,
                    "task_id": tid,
                })

        return created_ids

    def _drain_queue_locked(self, session_id: str, state: LMState) -> None:
        """在并发余量允许范围内，从 pending_queue 调度子 task。锁已持有时调用。"""
        while state.pending_queue:
            if state.concurrent_agents >= state.max_concurrent_agents:
                break
            if state.concurrent_tasks >= state.max_concurrent_tasks:
                break

            task_id = state.pending_queue.pop(0)

            waiting_agent_id = state.task_to_waiting_agent.get(task_id)
            parent_meta = (
                state.agent_registry.get(waiting_agent_id) if waiting_agent_id else None
            )
            spawn_depth = (parent_meta.spawn_depth + 1) if parent_meta else 1

            try:
                task = self._task_svc.get(task_id)
                agent_id = self._instantiate_sub_agent(
                    session_id=session_id,
                    task_id=task_id,
                    parent_agent_id=task.agent_id,
                    spawn_depth=spawn_depth,
                )
                state.agent_registry[agent_id] = AgentMeta(
                    agent_id=agent_id,
                    task_id=task_id,
                    spawn_depth=spawn_depth,
                    status="RUNNING",
                )
                state.concurrent_agents += 1
                state.concurrent_tasks += 1

                self._bus.publish(LIFECYCLE_AGENT_SCHEDULED, {
                    "session_id": session_id,
                    "agent_id": agent_id,
                    "task_id": task_id,
                })
                # 在锁外启动线程（此处仅登记，稍后统一启动）
                self._runner.run_sub_async(session_id, agent_id, task_id)
            except Exception:
                logger.exception("LM: failed to instantiate agent for task %s", task_id)

    def _try_resume_waiting_agent_locked(
        self, session_id: str, state: LMState, finished_task_id: str
    ) -> None:
        """检查完成的 task 是否让某个 WAITING agent 的 waiting_for 清空。锁已持有时调用。"""
        waiting_agent_id = state.task_to_waiting_agent.get(finished_task_id)
        if waiting_agent_id is None:
            return

        meta = state.agent_registry.get(waiting_agent_id)
        if meta is None or meta.status != "WAITING":
            return

        meta.waiting_for.discard(finished_task_id)
        if meta.waiting_for:
            return  # 还有未完成的子任务

        # 所有子任务完成 —— 收集结果
        task_results: list[dict[str, Any]] = []
        for tid in meta.all_spawned_task_ids:
            state.task_to_waiting_agent.pop(tid, None)
            state.retry_counts.pop(tid, None)
            try:
                t = self._task_svc.get(tid)
                task_results.append({"title": t.title, "result": t.result or ""})
            except Exception:
                task_results.append({"title": tid, "result": ""})

        result = SpawnResult(
            approved=True,
            task_results=task_results,
            resume_hint=meta.resume_hint,
        )
        self._resume_payloads[waiting_agent_id] = result

        # 恢复 agent 元数据
        meta.status = "RUNNING"
        meta.waiting_for = set()
        meta.all_spawned_task_ids = []

        self._persist_agent_status(waiting_agent_id, "RUNNING", [])

        # 恢复 SUSPENDED task → ACTIVE（直接写盘，不经过状态机事件）
        if meta.task_id:
            try:
                requesting_task = self._task_svc.get(meta.task_id)
                requesting_task.status = "ACTIVE"
                self._task_svc.save(requesting_task)
            except Exception:
                pass

        self._bus.publish(AGENT_RESUME, {
            "session_id": session_id,
            "agent_id": waiting_agent_id,
            "task_count": len(task_results),
        })

        event = self._resume_events.get(waiting_agent_id)
        if event:
            event.set()

    def _fail_waiting_agent_locked(
        self,
        session_id: str,
        state: LMState,
        failed_task_id: str,
        attempts: int,
    ) -> None:
        """子任务重试耗尽，通知 WAITING agent 失败。锁已持有时调用。"""
        waiting_agent_id = state.task_to_waiting_agent.pop(failed_task_id, None)
        if waiting_agent_id is None:
            return

        meta = state.agent_registry.get(waiting_agent_id)
        if meta is None or meta.status != "WAITING":
            return

        # 清理其余子任务的追踪状态
        for tid in list(meta.all_spawned_task_ids):
            state.task_to_waiting_agent.pop(tid, None)
            state.retry_counts.pop(tid, None)
            state.dep_counter.pop(tid, None)
        meta.waiting_for.clear()

        result = SpawnResult(
            approved=True,  # 原本已批准，但子任务执行失败
            task_results=[{
                "title": "FAILED",
                "result": f"Sub-task {failed_task_id} failed after {attempts} attempt(s)",
            }],
            resume_hint="Sub-tasks failed; handle the error",
        )
        self._resume_payloads[waiting_agent_id] = result

        meta.status = "RUNNING"
        self._persist_agent_status(waiting_agent_id, "RUNNING", [])

        event = self._resume_events.get(waiting_agent_id)
        if event:
            event.set()

    def _instantiate_sub_agent(
        self,
        session_id: str,
        task_id: str,
        parent_agent_id: str,
        spawn_depth: int,
    ) -> str:
        """创建并持久化 sub-agent 实例，返回新 agent_id。"""
        from app.config.settings import get_settings
        settings = get_settings()

        parent_data = self._agent_store.get(parent_agent_id) or {}
        now = now_iso()
        agent_id = new_agent_id()

        agent = Agent(
            id=agent_id,
            session_id=session_id,
            template_id=parent_data.get("template_id"),
            name=f"sub-agent-d{spawn_depth}",
            status="IDLE",
            system_prompt=parent_data.get("system_prompt", settings.agent_default_system_prompt),
            tool_list=parent_data.get("tool_list", []),
            skill_list=parent_data.get("skill_list", []),
            soul_path=parent_data.get("soul_path"),
            loop_guard=LoopGuard(
                turns_used=0,
                max_turns=10,
                actor_max_tool_rounds=5,
            ),
            llm_name=parent_data.get("llm_name", settings.agent_default_llm_name),
            has_spawn_permission=False,  # V1: 不允许嵌套 spawn
            spawn_depth=spawn_depth,
            parent_task_id=task_id,
            created_at=now,
            updated_at=now,
        )
        self._agent_store.save(agent.to_dict())
        logger.debug("LM: instantiated sub-agent %s for task %s", agent_id, task_id)
        return agent_id

    def _persist_agent_status(
        self, agent_id: str, status: str, spawned_task_ids: list[str]
    ) -> None:
        """将 agent status 与 spawned_task_ids 写盘。"""
        data = self._agent_store.get(agent_id)
        if data is None:
            return
        data["status"] = status
        data["spawned_task_ids"] = spawned_task_ids
        data["updated_at"] = now_iso()
        self._agent_store.save(data)
