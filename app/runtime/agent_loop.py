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

    def run(self, session_id: str, agent_id: str, task_id: str) -> None:
        """Execute one task synchronously inside a worker thread.

        Observer decides task_outcome and AgentLoop writes task state.
        Transitions session to SUCCEEDED when observer reports done=True.
        Raises AppError on budget/turn exceeded or task failure — caller handles session FAILED.
        """
        agent = self._load_agent(session_id, agent_id)
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

            task = self._task_svc.get(task_id, session_id)

            ctx = self._reasoner.reason(session, agent, task)
            result = self._actor.act(task, ctx, agent)

            if result.context_tokens:
                agent.loop_guard.context_tokens = result.context_tokens
                self._agent_store.save(agent.to_dict())

            task = self._task_svc.get(task_id, session_id)

            if task.status == "SUSPENDED":
                if task.user_prompt:
                    self._memory_svc.append_message(
                        agent_id=agent_id,
                        role="user",
                        content=task.user_prompt,
                        session_id=session_id,
                        task_id=task_id,
                    )
                spawn_note = _summarize_spawn(result)
                self._memory_svc.append_message(
                    agent_id=agent_id,
                    role="assistant",
                    content=spawn_note,
                    session_id=session_id,
                    task_id=task_id,
                )
                return

            if task.status not in ("TO_BE_OBSERVED", "FINISHED", "FAILED", "CANCELED"):
                self._task_svc.to_be_observed(task_id, session_id)
                task.status = "TO_BE_OBSERVED"

            task_list = self._task_svc.list_by_agent(session_id, agent_id)
            verdict = self._observer.observe(session, result, ctx, task, task_list, agent)

            if verdict.context_tokens:
                agent = self._load_agent(session_id, agent_id)
                agent.loop_guard.context_tokens = max(
                    agent.loop_guard.context_tokens, verdict.context_tokens
                )
                self._agent_store.save(agent.to_dict())

            # ── task 状态由 ControlToolProvider handler 写入，此处只检查结果 ──
            task = self._task_svc.get(task_id, session_id)

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

        except AppError as e:
            logger.error(
                "AgentLoop error: session=%s code=%s msg=%s",
                session_id, e.code, e.message,
            )
            raise

        finally:
            agent = self._load_agent(session_id, agent_id)
            if agent.status == "RUNNING":
                try:
                    task = self._task_svc.get(task_id, session_id)
                    agent.status = "WAITING" if task.status == "SUSPENDED" else "FINISHED"
                except Exception:
                    agent.status = "FINISHED"
                self._agent_store.save(agent.to_dict())

    def _load_agent(self, session_id: str, agent_id: str) -> Agent:
        data = self._agent_store.get(session_id, agent_id)
        if data is None:
            raise AppError("AGENT_NOT_FOUND", f"Agent {agent_id} not found")
        return Agent.from_dict(data)


def _summarize_spawn(result: "ActorResult") -> str:
    """从 actor result 的 tool_calls_made 里提取 submit_task / submit_plan 调用，生成挂起摘要。"""
    from app.runtime.types import ActorResult  # noqa: F401  (TYPE_CHECKING 外的运行时引用)
    titles: list[str] = []
    for tc in result.tool_calls_made:
        if not isinstance(tc.arguments, dict):
            continue
        if tc.tool_name == "submit_task":
            title = tc.arguments.get("title", "")
            if title:
                titles.append(title)
        elif tc.tool_name == "submit_plan":
            for spec in tc.arguments.get("tasks", []):
                if isinstance(spec, dict):
                    title = spec.get("title", "")
                    if title:
                        titles.append(title)
    if titles:
        listed = ", ".join(f"'{t}'" for t in titles)
        return f"Delegated to sub-task(s): {listed}. Awaiting completion."
    return "Delegated to sub-task(s). Awaiting completion."
