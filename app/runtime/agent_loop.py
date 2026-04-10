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
            task_list = self._task_svc.list_by_session(session_id)
            verdict = self._observer.observe(session, result, ctx, task, task_list, agent)

            logger.debug("Session %s observer outcome: %s", session_id, verdict.task_outcome)

            # ── task 状态写入（由 Observer 判定，AgentLoop 执行）──────────────
            if verdict.task_outcome == "needs_user_input":
                confirmed, feedback = self._ask_user_for_task_confirmation(
                    task, result.output
                )
                if confirmed:
                    self._task_svc.finish(task_id, result=result.output)
                else:
                    error = feedback or "用户确认任务未完成"
                    self._task_svc.fail(task_id, error=error)
                    raise AppError("TASK_NOT_CONFIRMED", error)
            elif verdict.task_outcome == "success":
                outputs = result.task_outputs if result.task_outputs else None
                self._task_svc.finish(task_id, result=verdict.task_result, outputs=outputs)
            else:
                self._task_svc.fail(task_id, error=verdict.task_result)
                raise AppError("TASK_FAILED_BY_OBSERVER", verdict.task_result)

            # ── task_reviews：复核 FINISHED，提前完成 PENDING ────────────────
            for review in verdict.task_reviews:
                if review.task_id == task_id:
                    continue  # 当前 task 由上方逻辑处理，跳过
                try:
                    if review.review_status == "reopen":
                        self._task_svc.reopen(review.task_id)
                        logger.info("Task %s reopened by observer: %s", review.task_id, review.reasoning)
                    elif review.review_status == "skip":
                        self._task_svc.finish(review.task_id, result=review.reasoning or "Completed indirectly per observer.")
                        logger.info("Task %s skipped by observer: %s", review.task_id, review.reasoning)
                    # "confirmed" → 无操作
                except Exception as e:
                    logger.warning("Failed to apply task review for %s: %s", review.task_id, e)

            # ── memory / blackboard ───────────────────────────────────────────
            if verdict.summary:
                self._memory_svc.append_message(
                    agent_id=agent_id,
                    role="assistant",
                    content=verdict.summary,
                    session_id=session_id,
                )

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

    # ── HITL ──────────────────────────────────────────────────────────────────

    def _ask_user_for_task_confirmation(
        self,
        task: object,
        llm_output: str,
    ) -> tuple[bool, str | None]:
        """Observer 不确定时，暂停并等待用户确认任务状态（单次，不重试）。

        返回 (confirmed, feedback)：
          - True, None  → 用户确认完成
          - False, str  → 用户表示未完成，feedback 作为 fail error
        """
        prompt = (
            f"任务「{task.title}」已执行，但系统无法自动判定完成状态。\n\n"
            f"执行结果：\n{llm_output or '（无输出）'}\n\n"
            "请确认任务是否完成，或补充说明以便 Agent 重新规划。"
        )
        answer = self._block_for_user_input(
            task,
            title="请确认任务完成状态",
            inputs={
                "prompt": prompt,
                "type": "task_completion_confirm",
                "task_title": task.title,
                "task_output": llm_output,
                "original_task_id": task.id,
                "inline": True,
            },
        )
        confirmed = answer.startswith("用户已确认任务完成")
        feedback: str | None = None
        if not confirmed:
            prefix = "用户表示任务未完成，请重试。用户补充说明："
            feedback = answer[len(prefix):] if answer.startswith(prefix) else answer
        return confirmed, feedback

    def _block_for_user_input(self, task: object, title: str, inputs: dict) -> str:
        """阻塞等待用户回答后返回内容（最多 1 小时）。

        不创建 user_input task；通过 HitlStore + threading.Event 阻塞当前工作线程。
        答案由 POST /sessions/{id}/input 注入，唤醒后继续执行。
        """
        from app.runtime.hitl_store import get_hitl_store
        from app.runtime.sse_bus import get_sse_bus

        session_id = getattr(task, "session_id", "")
        agent_id = getattr(task, "assigned_agent_id", "")
        prompt = inputs.get("prompt", "")
        input_type = inputs.get("type", "user_input")

        self._session_svc.transition(session_id, "WAITING_INPUT")
        try:
            from app.common.utils import now_iso
            get_sse_bus().push(session_id, {
                "type": "message",
                "role": "assistant",
                "content": prompt,
                "created_at": now_iso(),
            })
            get_sse_bus().push(session_id, {
                "type": "waiting_input",
                "prompt": prompt,
                "input_type": input_type,
                "task_title": title,
            })
        except Exception:
            pass

        answer = get_hitl_store().wait(session_id, agent_id, prompt, input_type)
        self._session_svc.transition(session_id, "RUNNING")
        return answer

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
