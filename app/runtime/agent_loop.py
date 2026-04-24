"""Single-task agent execution loop (v1.6).

每次调用 run() 执行恰好一个 task：
  - task.type == "plan"   → Actor._act_as_planner（内部调用 Planner）
  - task.type == "atomic" → Actor._act_as_executor（多轮 tool use）

task 完成判定由 Observer 负责，返回 task_outcome（success / failed / needs_user_input）。
AgentLoop 依据 task_outcome 写入 task 状态；needs_user_input 时触发 HITL。
调度逻辑（下一个 task、session SUCCEEDED/重试）由 LifecycleManager 负责。
"""

from __future__ import annotations

import logging

from app.common.errors import AppError
from app.domain.models.agent import Agent
from app.domain.services.blackboard_service import BlackboardService
from app.domain.services.memory_service import MemoryService
from app.domain.services.session_service import SessionService
from app.domain.services.task_service import TaskService
from app.llm.base import BaseChatClient
from app.runtime.actor import Actor
from app.runtime.observer import Observer
from app.runtime.reasoner import Reasoner
from app.storage.file.agent_store import AgentStore

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.runtime.compaction import CompactionStrategy

logger = logging.getLogger(__name__)


class AgentLoop:
    """Execute exactly one task per run() call."""

    def __init__(
        self,
        session_svc: SessionService,
        task_svc: TaskService,
        memory_svc: MemoryService,
        blackboard_svc: BlackboardService,
        agent_store: AgentStore,
        llm_client: BaseChatClient,
        reasoner: Reasoner,
        actor: Actor,
        observer: Observer,
        compaction_strategy: "CompactionStrategy | None" = None,
    ) -> None:
        self._session_svc = session_svc
        self._task_svc = task_svc
        self._memory_svc = memory_svc
        self._bb_svc = blackboard_svc
        self._agent_store = agent_store
        self._llm_client = llm_client
        self._reasoner = reasoner
        self._actor = actor
        self._observer = observer
        self._compaction_strategy = compaction_strategy

    def run(self, session_id: str, agent_id: str, task_id: str) -> None:
        """Execute one task synchronously inside a worker thread.

        Observer decides task_outcome and AgentLoop writes task state.
        Transitions session to SUCCEEDED when observer reports done=True.
        Raises AppError on budget/turn exceeded or task failure — caller handles session FAILED.
        """
        agent = self._load_agent(agent_id)
        agent.status = "RUNNING"
        self._agent_store.save(agent.to_dict())

        try:
            session = self._session_svc.get(session_id)

            if session.token_used >= session.token_budget:
                raise AppError(
                    "TOKEN_BUDGET_EXCEEDED",
                    f"Session {session_id} token budget exhausted "
                    f"({session.token_used}/{session.token_budget})",
                )

            guard = agent.loop_guard
            guard.turns_used += 1
            self._agent_store.save(agent.to_dict())

            if guard.turns_used > guard.max_turns:
                raise AppError(
                    "MAX_TURNS_EXCEEDED",
                    f"Agent {agent_id} reached max_turns={guard.max_turns}",
                )

            task = self._task_svc.get(task_id)

            ctx = self._reasoner.reason(session, agent, task)
            result = self._actor.act(task, ctx, agent)

            task = self._task_svc.get(task_id)

            if task.status == "SUSPENDED":
                return

            if task.status not in ("TO_BE_OBSERVED", "FINISHED", "FAILED", "CANCELED"):
                self._task_svc.to_be_observed(task_id)
                task.status = "TO_BE_OBSERVED"

            task_list = self._task_svc.list_by_agent(session_id, agent_id)
            verdict = self._observer.observe(session, result, ctx, task, task_list, agent)

            # ── task 状态由 ControlToolProvider handler 写入，此处只检查结果 ──
            task = self._task_svc.get(task_id)

            # ── memory 写入在状态判断之前，确保 active 路径也写入 ─────────────
            if task.user_prompt:
                self._memory_svc.append_message(
                    agent_id=agent_id,
                    role="user",
                    content=task.user_prompt,
                    session_id=session_id,
                    task_id=task_id,
                )
            if verdict.summary or (result.conversation_turns and result.conversation_turns[-1].images):
                last_images = result.conversation_turns[-1].images if result.conversation_turns else []
                if last_images:
                    mem_content: str | list = [
                        {"type": "image", "data": img.data, "media_type": img.media_type, "source_type": img.source_type}
                        for img in last_images
                    ]
                    if verdict.summary:
                        mem_content.append({"type": "text", "text": verdict.summary})
                else:
                    mem_content = verdict.summary
                self._memory_svc.append_message(
                    agent_id=agent_id,
                    role="assistant",
                    content=mem_content,
                    session_id=session_id,
                    task_id=task_id,
                )

            if task.status == "FAILED":
                raise AppError("TASK_FAILED_BY_OBSERVER", task.result or "")

            if task.status == "PENDING":
                # observer 判定 active：任务重新入队，actor 获得新一轮机会
                return

            if task.status == "FINISHED" and task.result:
                self._bb_svc.publish(session_id, task.id, agent_id, task.result)

            for result_turn in result.conversation_turns:
                self._bb_svc.publish(session_id, "_root", "agent_id_" + agent_id, result_turn.llm_text)
                for tool_call in result_turn.tool_calls:
                    self._bb_svc.publish(session_id, "_root", "agent_id_" + agent_id, f"Tool call: {tool_call.tool_name}({tool_call.arguments}) -> {tool_call.result} (error={tool_call.is_error})")

            if self._memory_svc.should_summarize(agent_id):
                self._do_summarize(session_id, agent_id, verdict.summary)

        except AppError as e:
            logger.error(
                "AgentLoop error: session=%s code=%s msg=%s",
                session_id, e.code, e.message,
            )
            raise

        finally:
            agent = self._load_agent(agent_id)
            if agent.status == "RUNNING":
                agent.status = "FINISHED"
                self._agent_store.save(agent.to_dict())

    def _load_agent(self, agent_id: str) -> Agent:
        data = self._agent_store.get(agent_id)
        if data is None:
            raise AppError("AGENT_NOT_FOUND", f"Agent {agent_id} not found")
        return Agent.from_dict(data)

    def _do_summarize(
        self, session_id: str, agent_id: str, latest_summary: str
    ) -> None:
        from app.common.utils import now_iso
        from app.domain.models.memory import MemorySummary

        summary_text = latest_summary

        if self._compaction_strategy is not None:
            messages = self._memory_svc.get_window(agent_id, 10000)
            _, compacted_summary = self._compaction_strategy.compact(messages)
            if compacted_summary:
                summary_text = compacted_summary

        count = len(self._memory_svc.get_window(agent_id, 10000))
        summary = MemorySummary(
            session_id=session_id,
            agent_id=agent_id,
            summary_text=summary_text,
            covered_up_to=count,
            created_at=now_iso(),
        )
        self._memory_svc.save_summary(agent_id, summary)
