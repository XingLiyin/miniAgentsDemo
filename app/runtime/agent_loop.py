"""Main runtime loop for a session agent (Agent Loop v2)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.common.errors import AppError
from app.domain.models.agent import Agent
from app.domain.models.session import Session
from app.domain.services.blackboard_service import BlackboardService
from app.domain.services.memory_service import MemoryService
from app.domain.services.session_service import SessionService
from app.domain.services.task_service import TaskService
from app.llm.base import BaseChatClient
from app.runtime.actor import Actor
from app.runtime.observer import Observer
from app.runtime.planner import Planner
from app.runtime.reasoner import Reasoner
from app.runtime.types import ActorResult, ReasoningContext, TaskPlan
from app.storage.file.agent_store import AgentStore

if TYPE_CHECKING:
    from app.runtime.compaction import CompactionStrategy

logger = logging.getLogger(__name__)


class AgentLoop:
    """Drive an agent through Reason → Plan → Act → Observe until done."""

    def __init__(
        self,
        session_svc: SessionService,
        task_svc: TaskService,
        memory_svc: MemoryService,
        blackboard_svc: BlackboardService,
        agent_store: AgentStore,
        llm_client: BaseChatClient,
        reasoner: Reasoner,
        planner: Planner,
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
        self._planner = planner
        self._actor = actor
        self._observer = observer
        self._compaction_strategy = compaction_strategy

    def run(self, session_id: str, agent_id: str) -> None:
        """Run the loop synchronously inside a worker thread."""
        agent = self._load_agent(agent_id)
        agent.status = "RUNNING"
        self._agent_store.save(agent.to_dict())

        try:
            while True:
                session = self._session_svc.get(session_id)
                self._check_guard(session, agent)

                # Phase 1: Reason
                ctx = self._reasoner.reason(session, agent)

                # Phase 2: Plan
                plan = self._planner.plan(ctx, agent)
                agent.loop_guard.turns_used += 1
                self._agent_store.save(agent.to_dict())

                if not plan.tasks:
                    logger.info(
                        "Session %s: planner returned empty task list, treating as done",
                        session_id,
                    )
                    self._session_svc.transition(session_id, "SUCCEEDED")
                    break

                # Phase 3: Act
                results, hitl_triggered = self._act_all(plan, ctx, agent, session_id)
                if hitl_triggered:
                    logger.info("Session %s: paused waiting for user input", session_id)
                    return

                # Phase 4: Observe
                verdict = self._observer.observe(session, results, ctx)
                logger.debug(
                    "Session %s observer reasoning: %s", session_id, verdict.reasoning
                )

                # 写摘要到 Memory + Blackboard
                if verdict.summary:
                    self._memory_svc.append_message(
                        session_id=session_id,
                        agent_id=agent_id,
                        role="assistant",
                        content=verdict.summary,
                    )
                    self._bb_svc.publish(session_id, "_root", agent_id, verdict.summary)

                if self._memory_svc.should_summarize(session_id):
                    self._do_summarize(session_id, agent_id, verdict.summary)

                if verdict.done:
                    logger.info(
                        "Session %s: agent %s reports done", session_id, agent_id
                    )
                    self._session_svc.transition(session_id, "SUCCEEDED")
                    break

        except AppError as e:
            logger.error(
                "AgentLoop terminated: session=%s code=%s msg=%s",
                session_id,
                e.code,
                e.message,
            )
            try:
                self._session_svc.transition(session_id, "FAILED")
            except Exception:
                pass
            raise

        finally:
            agent = self._load_agent(agent_id)
            if agent.status == "RUNNING":
                agent.status = "FINISHED"
                self._agent_store.save(agent.to_dict())

    # ── Phase 3 helpers ───────────────────────────────────────────────────────

    def _act_all(
        self,
        plan: TaskPlan,
        ctx: ReasoningContext,
        agent: Agent,
        session_id: str,
    ) -> tuple[list[ActorResult], bool]:
        """Execute all planned tasks sequentially; return (results, hitl_triggered)."""
        results: list[ActorResult] = []

        for planned in plan.tasks:
            # 创建 DB task，skill_name 存入 inputs
            inputs = {"skill_name": planned.skill_name} if planned.skill_name else {}
            db_task = self._task_svc.create(
                session_id=session_id,
                agent_id=agent.id,
                task_type="atomic",
                title=planned.title,
                description=planned.description,
                inputs=inputs,
            )

            result = self._actor.act(db_task, ctx, agent)
            results.append(result)

            if result.hitl_task_id:
                # Actor triggered HITL internally (via request_human_input tool)
                return results, True

            if not result.success:
                logger.warning(
                    "Session %s: task %s failed: %s",
                    session_id,
                    db_task.id,
                    result.error,
                )
                # LLM 明确标记失败（mark_task_complete(success=False)），结束本轮循环
                break

        return results, False

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _check_guard(self, session: Session, agent: Agent) -> None:
        if session.token_used >= session.token_budget:
            raise AppError(
                "TOKEN_BUDGET_EXCEEDED",
                f"Session {session.id} token budget exhausted "
                f"({session.token_used}/{session.token_budget})",
            )
        guard = agent.loop_guard
        if guard.turns_used >= guard.max_turns:
            logger.warning(
                "Session %s: agent %s reached max_turns=%d. Forcing done.",
                session.id,
                agent.id,
                guard.max_turns,
            )
            raise AppError(
                "MAX_TURNS_EXCEEDED",
                f"Agent {agent.id} reached max_turns={guard.max_turns}",
            )

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
            messages = self._memory_svc.get_window(session_id, 10000)
            _, compacted_summary = self._compaction_strategy.compact(messages)
            if compacted_summary:
                summary_text = compacted_summary

        count = len(self._memory_svc.get_window(session_id, 10000))
        summary = MemorySummary(
            session_id=session_id,
            agent_id=agent_id,
            summary_text=summary_text,
            covered_up_to=count,
            created_at=now_iso(),
        )
        self._memory_svc.save_summary(session_id, summary)
