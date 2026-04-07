"""SubAgentRunner：在后台线程中运行 root AgentLoop 或 sub-agent 单任务执行。"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from app.common.errors import AppError
from app.domain.events.event_types import AGENT_FAILED, AGENT_FINISHED
from app.domain.models.agent import Agent
from app.domain.services.memory_service import MemoryService
from app.domain.services.session_service import SessionService
from app.domain.services.task_service import TaskService
from app.runtime.actor import Actor
from app.runtime.reasoner import Reasoner
from app.storage.file.agent_store import AgentStore

if TYPE_CHECKING:
    from app.domain.events.event_bus import EventBus
    from app.runtime.agent_loop import AgentLoop

logger = logging.getLogger(__name__)


class SubAgentRunner:
    """统一入口：既能启动 root AgentLoop，也能运行 sub-agent 单任务。

    所有执行均在 daemon 线程中进行，不阻塞调用方。
    """

    def __init__(
        self,
        actor: Actor,
        reasoner: Reasoner,
        task_svc: TaskService,
        session_svc: SessionService,
        memory_svc: MemoryService,
        agent_store: AgentStore,
        event_bus: "EventBus",
        agent_loop: "AgentLoop | None" = None,
    ) -> None:
        self._actor = actor
        self._reasoner = reasoner
        self._task_svc = task_svc
        self._session_svc = session_svc
        self._memory_svc = memory_svc
        self._agent_store = agent_store
        self._bus = event_bus
        self._agent_loop = agent_loop

    def set_agent_loop(self, loop: "AgentLoop") -> None:
        self._agent_loop = loop

    # ── 公开接口 ──────────────────────────────────────────────────────────

    def run_root_async(self, session_id: str, agent_id: str) -> None:
        """在 daemon 线程中启动 root AgentLoop。"""
        if self._agent_loop is None:
            logger.error("SubAgentRunner: AgentLoop not set, cannot run root agent")
            return
        thread = threading.Thread(
            target=self._run_root_safe,
            args=(session_id, agent_id),
            name=f"root-{agent_id[:8]}",
            daemon=True,
        )
        thread.start()

    def run_sub_async(self, session_id: str, agent_id: str, task_id: str) -> None:
        """在 daemon 线程中启动 sub-agent 单任务执行。"""
        thread = threading.Thread(
            target=self._run_sub_safe,
            args=(session_id, agent_id, task_id),
            name=f"sub-{agent_id[:8]}",
            daemon=True,
        )
        thread.start()

    # ── 私有执行体 ────────────────────────────────────────────────────────

    def _run_root_safe(self, session_id: str, agent_id: str) -> None:
        """运行 root AgentLoop，完成后发布 AGENT_FINISHED / AGENT_FAILED 事件。"""
        assert self._agent_loop is not None
        try:
            self._session_svc.transition(session_id, "RUNNING")
            self._agent_loop.run(session_id, agent_id)
        except AppError as e:
            # AgentLoop 内部已将 session 转为 FAILED
            logger.error(
                "Root agent %s terminated: %s %s", agent_id, e.code, e.message
            )
            self._bus.publish(AGENT_FAILED, {
                "session_id": session_id,
                "agent_id": agent_id,
                "task_id": None,
                "error": e.message,
            })
        except Exception as e:
            logger.exception("Root agent %s unexpected error", agent_id)
            try:
                self._session_svc.transition(session_id, "FAILED")
            except Exception:
                pass
            self._bus.publish(AGENT_FAILED, {
                "session_id": session_id,
                "agent_id": agent_id,
                "task_id": None,
                "error": str(e),
            })
        else:
            # AgentLoop 正常退出（已将 session 转为 SUCCEEDED / WAITING_INPUT）
            self._bus.publish(AGENT_FINISHED, {
                "session_id": session_id,
                "agent_id": agent_id,
                "task_id": None,
                "success": True,
            })

    def _run_sub_safe(self, session_id: str, agent_id: str, task_id: str) -> None:
        """运行 sub-agent 单任务：Reasoner 构建上下文 → Actor 执行。"""
        try:
            agent_data = self._agent_store.get(agent_id)
            if agent_data is None:
                raise AppError("AGENT_NOT_FOUND", f"Sub-agent {agent_id} not found")
            agent = Agent.from_dict(agent_data)
            agent.status = "RUNNING"
            self._agent_store.save(agent.to_dict())

            session = self._session_svc.get(session_id)
            task = self._task_svc.get(task_id)

            # 使用与 root agent 相同的 Reasoner 构建上下文
            ctx = self._reasoner.reason(session, agent)

            result = self._actor.act(task, ctx, agent)

            # 写入 sub-agent 完成状态
            agent_data = self._agent_store.get(agent_id)
            if agent_data:
                agent_data["status"] = "FINISHED"
                self._agent_store.save(agent_data)

            self._bus.publish(AGENT_FINISHED, {
                "session_id": session_id,
                "agent_id": agent_id,
                "task_id": task_id,
                "success": result.success,
                "output": result.output,
            })

        except Exception as e:
            logger.exception("Sub-agent %s failed for task %s", agent_id, task_id)

            agent_data = self._agent_store.get(agent_id)
            if agent_data:
                agent_data["status"] = "FAILED"
                self._agent_store.save(agent_data)

            try:
                self._task_svc.fail(task_id, error=str(e))
            except Exception:
                pass

            self._bus.publish(AGENT_FAILED, {
                "session_id": session_id,
                "agent_id": agent_id,
                "task_id": task_id,
                "error": str(e),
            })
