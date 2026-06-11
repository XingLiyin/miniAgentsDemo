"""Single-task agent execution loop (v1.6).

每次调用 run() 执行恰好一个 task：
  - task.type == "plan"   → Actor._act_as_planner（内部调用 Planner）
  - task.type == "atomic" → Actor._act_as_executor（多轮 tool use）

task 完成判定由 Observer 负责，返回 task_outcome（success / failed / ask_human）。
AgentLoop 依据 task_outcome 写入 task 状态；ask_human 时暂停求助用户，回答后 task 保持 active 重新入队。
调度逻辑（下一个 task、session SUCCEEDED/重试）由 LifecycleManager 负责。
"""

from __future__ import annotations

import logging

from app.common.errors import AppError
from app.common.interrupt import AgentInterruptedError, InterruptContext, InterruptRegistry
from app.common.utils import now_iso
from app.runtime.execution_rounds import DELEGATION_TOOLS, make_round_record
from app.runtime.task_result import full_process_report, task_label, task_result_content
from app.domain.models.agent import Agent
from app.domain.models.task import Task
from app.domain.services.blackboard_service import BlackboardService
from app.domain.services.memory_service import MemoryService
from app.domain.services.session_service import SessionService
from app.domain.services.task_service import TaskService
from app.runtime.actor import Actor
from app.runtime.observer import Observer
from app.runtime.reasoner import Reasoner
from app.runtime.types import ActorResult, ObserverVerdict
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
        reasoner: Reasoner,
        actor: Actor,
        observer: Observer,
    ) -> None:
        self._session_svc = session_svc
        self._task_svc = task_svc
        self._memory_svc = memory_svc
        self._bb_svc = blackboard_svc
        self._agent_store = agent_store
        self._reasoner = reasoner
        self._actor = actor
        self._observer = observer

    def run(self, session_id: str, agent_id: str, task_id: str) -> None:
        """Execute one task synchronously inside a worker thread.

        Observer decides task_outcome and AgentLoop writes task state.
        Transitions session to SUCCEEDED when observer reports done=True.
        Raises AppError on budget/turn exceeded or task failure — caller handles session FAILED.
        """
        interrupt_flag = InterruptRegistry.get_or_create(session_id)

        agent = self._load_agent(session_id, agent_id)
        agent.status = "RUNNING"
        self._agent_store.save(agent.to_dict())

        task: Task | None = None
        try:
            session = self._session_svc.get(session_id)

            if session.token_budget > 0 and session.output_tokens_used >= session.token_budget:
                raise AppError(
                    "TOKEN_BUDGET_EXCEEDED",
                    f"Session {session_id} output token budget exhausted "
                    f"({session.output_tokens_used}/{session.token_budget})",
                )

            task = self._task_svc.get(task_id, session_id)

            # 重新开始任务时清空上一轮可能残留的 HITL 回答暂存，避免在本轮 write_execution_memory 误追加。
            if task.pending_user_answer is not None:
                task.pending_user_answer = None
                self._task_svc.save(task)

            # 立即持久化 user_prompt（包装格式），确保任务无论以何种方式结束都在 memory 里。
            if task.user_prompt and not task.user_prompt_in_memory and not (task.settings or {}).get("_daemon"):
                wrapped = self._actor._prompt_builder.build_initial_user_content(task)
                self._memory_svc.append_message(
                    agent_id=agent_id,
                    role="user",
                    content=wrapped,
                    session_id=session_id,
                    task_id=task_id,
                )
                task.user_prompt_in_memory = True
                self._task_svc.save(task)

            # 交付被跟踪（非自己提交、非自己执行）的已终结 task 结果：每次 run 以 user 消息注入一次。
            if not (task.settings or {}).get("_daemon"):
                self._inject_tracking_updates(session_id, agent_id, task_id)

            ctx = self._reasoner.reason(session, agent, task)

            result, task = self._run_actor(session, agent, task, ctx, session_id, agent_id,
                                           interrupt_flag=interrupt_flag)

            if task.status == "SUSPENDED":
                # 先记录本回合（含 submit 前的真实工具调用），再写 submit tool_call；
                # 顺序保证 round 的 mem_index 锚在 submit memory 之前。
                self._append_execution_round(agent_id, task, result, "", session_id, task_id)
                self._write_suspension_memory(agent_id, task, result, session_id, task_id)
                return

            verdict, task = self._run_observer(session, agent, ctx, task, result, session_id, agent_id, task_id)

            self._append_execution_round(agent_id, task, result, verdict.summary or "", session_id, task_id)
            self._write_execution_memory(agent_id, task, verdict, session_id, task_id)

            if task.status == "FAILED":
                raise AppError("TASK_FAILED_BY_OBSERVER", task.process_report or "")

            if task.status == "PENDING":
                return

            self._publish_blackboard(session_id, agent_id, task, result)

        except AgentInterruptedError as exc:
            logger.info("AgentLoop: interrupted by user, session=%s task=%s", session_id, task_id)
            self._handle_interrupt(session_id, agent_id, task_id, task, exc.context)
            return  # 正常退出：让 LifecycleManager 走 FINISHED 路径，TaskManager 检查 session 状态后跳过调度

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

    # ── Phase helpers ─────────────────────────────────────────────────────────

    def _run_actor(self, session, agent: Agent, task, ctx, session_id: str, agent_id: str,
                   interrupt_flag=None):
        """Run the actor, update context tokens, reload task from store."""
        # Daemon tasks run to completion regardless of interrupt signal
        effective_flag = None if (task.settings or {}).get("_daemon") else interrupt_flag
        if effective_flag and effective_flag.is_set():
            raise AgentInterruptedError("interrupted before actor start")
        # 清空上一轮残留的 outputs：本轮没有最终产出时不应再被旧值掩盖（影响 observer 的空产出护栏）。
        if task.outputs:
            task.outputs = ""
            self._task_svc.save(task)
        result = self._actor.act(task, ctx, agent, session, interrupt_flag=effective_flag)

        if result.context_tokens:
            agent.loop_guard.context_tokens = result.context_tokens
            agent.loop_guard.context_message_count = self._memory_svc.count_messages(agent_id)
            self._agent_store.save(agent.to_dict())

        task = self._task_svc.get(task.id, session_id)

        if result.exit_reason == "normal" and (result.output or (result.conversation_turns and result.conversation_turns[-1].images)):
            last_images = result.conversation_turns[-1].images if result.conversation_turns else []
            if last_images:
                task.outputs = [
                    {"type": "image", "data": img.data, "media_type": img.media_type, "source_type": img.source_type}
                    for img in last_images
                ]
                if result.output:
                    task.outputs.append({"type": "text", "text": result.output})
            else:
                task.outputs = result.output
            self._task_svc.save(task)

        return result, task

    def _run_observer(self, session, agent: Agent, ctx, task, result: ActorResult, session_id: str, agent_id: str, task_id: str):
        """Transition task to TO_BE_OBSERVED, run observer, update tokens, reload task."""
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

        task = self._task_svc.get(task_id, session_id)
        return verdict, task

    def _write_suspension_memory(self, agent_id: str, task: Task, result: ActorResult, session_id: str, task_id: str) -> None:
        """挂起时把 submit_task/submit_plan 调用写成 assistant tool_call，并回填子任务的 parent_tool_call_id。"""
        submit_call = next(
            (tc for tc in result.tool_calls_made if tc.tool_name in DELEGATION_TOOLS),
            None,
        )
        if submit_call is None:
            # 理论上挂起必有 submit_* 调用；防御性兜底：至少保留本轮文本。
            if result.output:
                self._memory_svc.append_message(
                    agent_id=agent_id, role="assistant", content=result.output,
                    session_id=session_id, task_id=task_id,
                )
            return

        self._memory_svc.append_message(
            agent_id=agent_id,
            role="assistant",
            content=result.output or "",
            session_id=session_id,
            task_id=task_id,
            tool_calls=[{"id": submit_call.tool_call_id, "name": submit_call.tool_name,
                         "input": submit_call.arguments}],
        )
        # 回填本批新建子任务的 parent_tool_call_id（已链接的不覆盖，兼容多次提交）。
        try:
            for child in self._task_svc.list_children(task.id, session_id):
                if not child.parent_tool_call_id:
                    child.parent_tool_call_id = submit_call.tool_call_id
                    self._task_svc.save(child)
        except Exception:
            logger.warning("AgentLoop: failed to backfill parent_tool_call_id for children of %s", task.id)

    def _append_execution_round(self, agent_id: str, task: Task, result: ActorResult,
                                process_report: str, session_id: str, task_id: str) -> None:
        """把本次 act() 调用的逐轮记录追加进 task.execution_rounds 并持久化。

        mem_index = 此刻 agent memory 中属于当前 task 的消息数——即该 round 应插入在第
        mem_index 条当前-task 消息之前（紧接其后写入的 output/submit memory 就是第 mem_index 条）。
        """
        mem_index = sum(
            1 for m in self._memory_svc.get_all_messages(agent_id)
            if m.get("task_id") == task_id
        )
        record = make_round_record(
            result.conversation_turns,
            process_report=process_report,
            output=task.outputs,
            ts=now_iso(),
            mem_index=mem_index,
        )
        task.execution_rounds.append(record)
        self._task_svc.save(task)

    def _inject_tracking_updates(self, session_id: str, agent_id: str, task_id: str) -> None:
        """把被跟踪（非自己提交、非自己执行）的已终结 task 结果作为 user 消息注入 memory，
        并从 tracking 中清除（恰好投递一次）。这是 tracker/sibling 结果的唯一交付路径。

        自己提交的（有 parent_tool_call_id）走 submit_task 的 tool_result；
        自己执行的已在自身 memory：均不写，但仍清理 tracking。
        """
        data = self._agent_store.get(session_id, agent_id) or {}
        tracking = list(data.get("tracking_tasks", []))
        if not tracking:
            return
        delivered: list[str] = []
        for tid in tracking:
            try:
                t = self._task_svc.get(tid, session_id)
            except Exception:
                continue
            if t.status not in ("FINISHED", "FAILED", "CANCELED"):
                continue
            if t.parent_tool_call_id:          # 自己提交的走 tool_result
                continue
            if t.assigned_agent_id == agent_id:  # 自己执行的已在自身 memory
                delivered.append(tid)
                continue
            outcome = "completed" if t.status == "FINISHED" else "failed"
            content = task_result_content(
                f"Tracked {task_label(t, outcome)}", t.outputs, full_process_report(t), t.error,
            )
            self._memory_svc.append_message(
                agent_id=agent_id, role="user", content=content,
                session_id=session_id, task_id=task_id,
            )
            delivered.append(tid)
        if not delivered:
            return
        data["tracking_tasks"] = [tid for tid in data.get("tracking_tasks", []) if tid not in delivered]
        self._agent_store.save(data)
        for tid in delivered:
            try:
                t = self._task_svc.get(tid, session_id)
                if agent_id in t.trackers:
                    t.trackers.remove(agent_id)
                    self._task_svc.save(t)
            except Exception:
                pass

    def _write_execution_memory(self, agent_id: str, task: Task, verdict: ObserverVerdict, session_id: str, task_id: str) -> None:
        """把任务的最终 output 写入 agent memory（不含 process report）。"""
        if task.outputs:
            if isinstance(task.outputs, list):
                output_images = [p for p in task.outputs if p.get("type") == "image"]
                output_text = next((p.get("text", "") for p in task.outputs if p.get("type") == "text"), "")
                if output_images:
                    mem_content: str | list = list(output_images)
                    if output_text:
                        mem_content.append({"type": "text", "text": output_text})
                else:
                    mem_content = output_text
            else:
                mem_content = task.outputs
            if mem_content:
                self._memory_svc.append_message(
                    agent_id=agent_id,
                    role="assistant",
                    content=mem_content,
                    session_id=session_id,
                    task_id=task_id,
                )

        # HITL 确认回答：先写完上面的输出，再补一条 user 消息，保证时序为「输出 → 人类回答」。
        if task.pending_user_answer:
            self._memory_svc.append_message(
                agent_id=agent_id,
                role="user",
                content=task.pending_user_answer,
                session_id=session_id,
                task_id=task_id,
            )
            task.pending_user_answer = None
            self._task_svc.save(task)

    def _publish_blackboard(self, session_id: str, agent_id: str, task: Task, result: ActorResult) -> None:
        """Publish task result and conversation turns to the blackboard."""
        if task.process_report:
            self._bb_svc.publish(session_id, task.id, agent_id, task.process_report)
        if task.outputs:
            self._bb_svc.publish(session_id, task.id, agent_id, task.outputs)
        for result_turn in result.conversation_turns:
            self._bb_svc.publish(session_id, "_root", "agent_id_" + agent_id, result_turn.llm_text)
            for tool_call in result_turn.tool_calls:
                self._bb_svc.publish(
                    session_id, "_root", "agent_id_" + agent_id,
                    f"Tool call: {tool_call.tool_name}({tool_call.arguments}) -> {tool_call.result} (error={tool_call.is_error})",
                )

    # ── Interrupt handling ────────────────────────────────────────────────────

    def _handle_interrupt(self, session_id: str, agent_id: str, task_id: str,
                          task: "Task | None", context: "InterruptContext | None" = None) -> None:
        """打断后收尾：写快照到 memory，取消当前 task。"""
        snapshot = self._build_interrupt_snapshot(session_id, task, context)
        root_agent_id = self._get_root_agent_id(session_id) or agent_id
        try:
            self._memory_svc.append_message(
                agent_id=root_agent_id,
                role="assistant",
                content=snapshot,
                session_id=session_id,
                task_id=task_id,
            )
        except Exception:
            logger.warning("AgentLoop: failed to write interrupt snapshot to memory, session=%s", session_id)

        if task is not None and not (task.settings or {}).get("_daemon"):
            try:
                current = self._task_svc.get(task_id, session_id)
                if current.status in ("ACTIVE", "PENDING"):
                    self._task_svc.transition(task_id, "CANCELED", session_id)
            except Exception:
                logger.warning("AgentLoop: failed to cancel interrupted task %s", task_id)

    def _build_interrupt_snapshot(self, session_id: str, current_task: "Task | None",
                                  context: "InterruptContext | None" = None) -> str:
        from app.common.utils import extract_text
        lines = ["[Session interrupted by user]"]

        if current_task and not (current_task.settings or {}).get("_daemon"):
            # 用户的原始指令
            if current_task.user_prompt:
                prompt_text = (
                    extract_text(current_task.user_prompt)
                    if isinstance(current_task.user_prompt, list)
                    else current_task.user_prompt
                )
                if prompt_text:
                    truncated = prompt_text[:500] + ("…" if len(prompt_text) > 500 else "")
                    lines.append(f"Task prompt: {truncated}")
            # task 标题
            if current_task.title:
                lines.append(f"Task title: {current_task.title}")

        # 已完成的工具调用
        if context and context.tool_calls:
            lines.append(f"Tools called ({len(context.tool_calls)}):")
            for tc in context.tool_calls:
                status = "✗" if tc.is_error else "✓"
                lines.append(f"  {status} {tc.tool_name}")

        # 被截断的 LLM 输出
        if context and context.partial_text and context.partial_text.strip():
            text = context.partial_text[:800]
            if len(context.partial_text) > 800:
                text += "… [truncated]"
            lines.append(f"Partial output:\n{text}")

        # 任务执行流现场：使用 interrupt_session 预先记录的精确 ID 列表
        try:
            session = self._session_svc.get(session_id)
            interrupted_ids = set(session.metadata.get("_interrupted_task_ids", []))
            if interrupted_ids:
                all_tasks = self._task_svc.list_by_session(session_id)
                non_daemon = [t for t in all_tasks if not (t.settings or {}).get("_daemon")]
                by_id = {t.id: t for t in non_daemon}

                # 有哪些 task 的 parent_task_id 指向某个 task → 该 task 曾是 SUSPENDED
                child_parent_ids = {t.parent_task_id for t in non_daemon if t.parent_task_id}

                was_suspended = [
                    by_id[tid] for tid in interrupted_ids
                    if tid in by_id and tid in child_parent_ids
                ]
                was_pending = [
                    by_id[tid] for tid in interrupted_ids
                    if tid in by_id and tid not in child_parent_ids
                ]

                if was_suspended or was_pending:
                    lines.append("Task execution flow at interrupt:")
                    for t in was_suspended:
                        desc = t.description or t.title or t.id
                        children = [c for c in non_daemon if c.parent_task_id == t.id]
                        child_labels = ", ".join(c.title or c.id for c in children)
                        lines.append(f"  [Suspended] {desc}")
                        if child_labels:
                            lines.append(f"    └─ waiting for: {child_labels}")
                    for t in was_pending:
                        desc = t.title or t.description or t.id
                        lines.append(f"  [Planned, not started] {desc}")
        except Exception:
            pass

        lines.append("Awaiting new instructions.")
        return "\n".join(lines)

    def _get_root_agent_id(self, session_id: str) -> "str | None":
        try:
            return self._session_svc.get(session_id).root_agent_id
        except Exception:
            return None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load_agent(self, session_id: str, agent_id: str) -> Agent:
        data = self._agent_store.get(session_id, agent_id)
        if data is None:
            raise AppError("AGENT_NOT_FOUND", f"Agent {agent_id} not found")
        return Agent.from_dict(data)
