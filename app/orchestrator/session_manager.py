"""Session 生命周期编排器（Phase 1）。

职责：创建 Session → 创建 root Agent → 异步触发 AgentLoop。
Guard 检查也在此处（token_budget）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.api.v1.schemas.session import InitialTaskConfig
from app.common.errors import AppError
from app.common.utils import extract_text, new_agent_id, now_iso
from app.config.settings import get_settings
from app.domain.events.event_bus import EventBus
from app.domain.models.agent import Agent, LoopGuard
from app.domain.models.session import Session
from app.domain.services.memory_service import MemoryService
from app.domain.services.session_service import SessionService
from app.domain.services.task_service import TaskService
from app.storage.file.agent_store import AgentStore
from app.storage.file.blackboard_store import BlackboardStore
from app.storage.file.task_store import TaskStore
from app.storage.file.tool_call_store import ToolCallStore

if TYPE_CHECKING:
    from app.agent_template.definition import AgentDefDetails
    from app.agent_template.loader import AgentLoader
    from app.agent_template.syncer import AgentTemplateSyncer
    from app.orchestrator.lifecycle_manager import LifecycleManager
    from app.orchestrator.task_manager import TaskManager

logger = logging.getLogger(__name__)


def _apply_template_to_agent(agent: Agent, details: "AgentDefDetails | None") -> None:
    from app.domain.models.agent import AgentCapability
    agent.actor = AgentCapability(
        instruction_md=details.actor_soul if details else "",
        tools=details.actor_capability.effective_tools() if details else [],
        mcp_servers=details.actor_capability.required_mcp_servers if details else [],
    )
    agent.observer = AgentCapability(
        instruction_md=details.observer_role if details else "",
        tools=details.observer_capability.effective_tools() if details else [],
        mcp_servers=details.observer_capability.required_mcp_servers if details else [],
    )


class SessionManager:
    """会话生命周期编排。"""

    def __init__(
        self,
        session_svc: SessionService,
        agent_store: AgentStore,
        event_bus: EventBus,
        task_svc: TaskService | None = None,
        memory_svc: MemoryService | None = None,
        task_store: TaskStore | None = None,
        tool_call_store: ToolCallStore | None = None,
        blackboard_store: BlackboardStore | None = None,
        template_loader: "AgentLoader | None" = None,
        template_syncer: "AgentTemplateSyncer | None" = None,
    ) -> None:
        self._session_svc = session_svc
        self._agent_store = agent_store
        self._bus = event_bus
        self._task_svc = task_svc
        self._memory_svc = memory_svc
        self._task_store = task_store
        self._tool_call_store = tool_call_store
        self._blackboard_store = blackboard_store
        self._template_loader = template_loader
        self._template_syncer = template_syncer
        self._lifecycle_manager: "LifecycleManager | None" = None
        self._task_manager: "TaskManager | None" = None

    def set_lifecycle_manager(self, lm: "LifecycleManager") -> None:
        self._lifecycle_manager = lm

    def set_task_manager(self, tm: "TaskManager") -> None:
        self._task_manager = tm

    def create_session(
        self,
        user_prompt: str,
        template_id: str | None = None,
        token_budget: int | None = None,
        llm_provider: str | None = None,
        llm_model: str | None = None,
        working_dir: str | None = None,
        initial_task: InitialTaskConfig | None = None,
    ) -> tuple[Session, str]:
        """创建 Session + root Agent，返回 (Session, root_agent_id)。
        由调用方决定是否/如何启动 AgentLoop（sync or async）。
        """
        settings = get_settings()
        resolved_llm_name = llm_provider or settings.default_llm_provider
        wd = working_dir or ""

        session = self._session_svc.create(
            user_prompt=user_prompt,
            template_id=template_id,
            token_budget=token_budget or settings.default_token_budget,
            llm_provider=resolved_llm_name,
            llm_model=llm_model or "",
            working_dir=wd,
        )

        # workspace 模板同步
        if wd and self._template_syncer:
            self._template_syncer.sync_workspace(wd)
            self._template_syncer.register_workspace(wd)

        # 查找模板：按名称（workspace > global） > 默认模板
        resolved_name = template_id or settings.default_agent_template_name
        details = self._template_loader.get_details(resolved_name, wd) if self._template_loader else None
        if details is None and template_id:
            details = self._template_loader.get_details(settings.default_agent_template_name, wd) if self._template_loader else None
        if details is None:
            logger.warning("No template found for '%s', creating agent with empty config", resolved_name)

        now = now_iso()
        agent = Agent(
            id=new_agent_id(),
            session_id=session.id,
            template_id=resolved_name if details else "",
            name="root",
            status="IDLE",
            loop_guard=LoopGuard(),
            has_spawn_permission=True,
            spawn_depth=0,
            settings={"working_dir": wd or settings.bash_exec_cwd},
            created_at=now,
            updated_at=now,
        )
        _apply_template_to_agent(agent, details)
        self._agent_store.save(agent.to_dict())
        self._session_svc.set_root_agent(session.id, agent.id)

        # Push SSE user message event
        try:
            from app.common.sse_bus import get_sse_bus
            get_sse_bus().push(session.id, {
                "type": "message",
                "role": "user",
                "content": user_prompt,
                "created_at": now_iso(),
            })
        except Exception:
            pass

        # 初始化 LifecycleManager session 状态，并注册 root agent
        if self._lifecycle_manager is not None:
            self._lifecycle_manager.init_session(session.id)
            self._lifecycle_manager.register_root_agent(session.id, agent.id)
        # 初始化 TaskQueue（必须在 task 创建前）
        if self._task_manager is not None:
            self._task_manager.init_session(session.id)

        # 创建初始 task（默认由 root 直接执行，不创建 sub-agent）
        if self._task_svc is not None:
            self._create_initial_task(
                session_id=session.id,
                creator_agent_id=agent.id,
                user_prompt=user_prompt,
                cfg=initial_task,
            )

        return session, agent.id

    def schedule_loop(self, session_id: str, agent_id: str) -> None:
        """通知 TaskManager session 可以开始运行，由 TM 负责找到并调度第一个 task。"""
        if self._task_manager is None:
            logger.warning("TaskManager not set, session %s will not run", session_id)
            return
        self._task_manager.start_session(session_id, agent_id)

    def _create_initial_task(
        self,
        session_id: str,
        creator_agent_id: str,
        user_prompt: str | list,
        cfg: InitialTaskConfig | None,
    ) -> None:
        """根据 InitialTaskConfig 创建第一个 task。
        cfg=None 时默认 use_subagent=False，由 root agent 直接执行。
        """
        assert self._task_svc is not None
        use_subagent = cfg.use_subagent if cfg else False
        title = (cfg.title if cfg else None) or ""
        description = (cfg.description if cfg else None) or ""
        inputs: dict = {"use_subagent": use_subagent, "inherit_memory": True}
        if use_subagent:
            settings = get_settings()
            subagent_tpl = (cfg.subagent_template if cfg else None) or settings.default_planner_template_name
            inputs["subagent_template"] = subagent_tpl
        task = self._task_svc.create(
            session_id=session_id,
            creator_agent_id=creator_agent_id,
            user_prompt=user_prompt,
            title=title,
            description=description,
            inputs=inputs,
        )
        if not title or not description:
            if self._task_manager is not None:
                self._task_manager.spawn_metadata_filler(session_id, creator_agent_id, user_prompt, task.id)

    def continue_session(self, session_id: str, user_message: str | list, *, initial_task: InitialTaskConfig | None = None, llm_provider: str | None = None, llm_model: str | None = None) -> Session:
        """Append a user message and re-start the agent loop if the session has ended."""

        # 提取纯文本用于 session/task 的 str 字段显示
        text_prompt = extract_text(user_message)

        session = self._session_svc.get(session_id)

        session.user_prompt = text_prompt
        self._session_svc.save(session)

        if session.status == "CANCELED":
            raise AppError("SESSION_CANCELED", f"Session {session_id} is canceled and cannot be continued")

        # Push SSE user message event（多模态内容透传给前端）
        try:
            from app.common.sse_bus import get_sse_bus
            get_sse_bus().push(session_id, {
                "type": "message",
                "role": "user",
                "content": user_message,
                "created_at": now_iso(),
            })
        except Exception:
            pass

        # Loop still active — reject, caller must wait for session to finish
        if session.status in ("QUEUED", "RUNNING"):
            raise AppError("SESSION_BUSY", f"Session {session_id} is still running (status={session.status})")

        # Session ended — reuse the existing root agent, reset its state
        if not session.root_agent_id:
            raise AppError("AGENT_NOT_FOUND", f"Session {session_id} has no root agent")
        agent_data = self._agent_store.get(session_id, session.root_agent_id)
        if agent_data is None:
            raise AppError("AGENT_NOT_FOUND", f"Root agent {session.root_agent_id} not found")
        agent = Agent.from_dict(agent_data)
        agent.status = "IDLE"
        agent.updated_at = now_iso()
        self._agent_store.save(agent.to_dict())
        if llm_provider:
            session.llm_provider = llm_provider
            session.llm_model = llm_model or ""
            self._session_svc.save(session)
        self._session_svc.transition(session_id, "QUEUED")
        # 重新初始化 LM 状态（旧 session 的状态已过期），并重新注册 root agent
        if self._lifecycle_manager is not None:
            self._lifecycle_manager.init_session(session_id)
            self._lifecycle_manager.register_root_agent(session_id, agent.id)
        # 重新初始化 TaskQueue（必须在 task 创建前）
        if self._task_manager is not None:
            self._task_manager.init_session(session_id)

        # 创建新 task，携带完整多模态内容
        if self._task_svc is not None:
            self._create_initial_task(
                session_id=session_id,
                creator_agent_id=agent.id,
                user_prompt=user_message,
                cfg=initial_task,
            )
        self.schedule_loop(session_id, agent.id)

        return self._session_svc.get(session_id)

    def answer_input(self, session_id: str, content: str) -> Session:
        """Submit user answer for a WAITING_INPUT session and unblock the agent thread."""
        from app.storage.file.hitl_store import get_hitl_store

        session = self._session_svc.get(session_id)
        if session.status != "WAITING_INPUT":
            raise AppError(
                "INVALID_STATE",
                f"Session {session_id} is not waiting for input (status={session.status})",
            )

        # 注入答案并唤醒阻塞的工作线程
        get_hitl_store().submit(session_id, content)

        # 推送用户回答气泡
        try:
            from app.common.utils import now_iso
            from app.common.sse_bus import get_sse_bus
            get_sse_bus().push(session_id, {
                "type": "message",
                "role": "user",
                "content": content,
                "created_at": now_iso(),
            })
        except Exception:
            pass

        # 工作线程自行将 session 转回 RUNNING，此处无需重启 loop
        return self._session_svc.get(session_id)

    def cancel_session(self, session_id: str) -> Session:
        """取消 Session。"""
        return self._session_svc.transition(session_id, "CANCELED")

    def delete_session(self, session_id: str) -> None:
        """级联删除 Session 及其所有关联数据。运行中的会话直接强制删除。"""
        session = self._session_svc.get(session_id)

        # 先拿到 agent_ids（删 memory 时需要），此时目录还未被删除
        agent_ids = self._agent_store.list_by_session(session_id)

        # 删除工具调用日志（单个 JSONL 文件）
        if self._tool_call_store is not None:
            self._tool_call_store.delete(session_id)

        # 删除 blackboard 目录
        if self._blackboard_store is not None:
            self._blackboard_store.delete_session(session_id)

        # 删除该 session 下所有 agent 的记忆（memory 以 agent_id 为 key）
        if self._memory_svc is not None:
            all_agent_ids = set(agent_ids)
            if session.root_agent_id:
                all_agent_ids.add(session.root_agent_id)
            for aid in all_agent_ids:
                self._memory_svc.delete_agent(aid)

        # 一次 rmtree 删除所有 agent 文件
        self._agent_store.delete_session(session_id)

        # 一次 rmtree 删除所有 task 文件
        if self._task_store is not None:
            self._task_store.delete_session(session_id)

        # 删除事件日志（SSE 历史回放）
        try:
            from app.storage.file.event_store import get_event_store
            get_event_store().delete(session_id)
        except Exception:
            pass

        # 释放 in-memory 状态（_states/_locks/_session_locks/_sessions）
        if self._lifecycle_manager is not None:
            self._lifecycle_manager.cleanup_session(session_id)
        if self._task_manager is not None:
            self._task_manager.cleanup_session(session_id)

        # 清除 workspace 模板记录并停止监控（仅当无其他 session 共用该目录时）
        if session.working_dir and self._template_syncer:
            if not self._session_svc.is_working_dir_in_use(session.working_dir, session_id):
                self._template_syncer.purge_workspace(session.working_dir)
                self._template_syncer.unregister_workspace(session.working_dir)

        # 最后删除 session 文件
        self._session_svc.delete(session_id)

    def check_token_budget(self, session: Session) -> None:
        """Guard 硬检查：输出超出 token_budget 立即抛出 AppError。"""
        if session.token_budget > 0 and session.output_tokens_used >= session.token_budget:
            raise AppError(
                "TOKEN_BUDGET_EXCEEDED",
                f"Session {session.id} output token budget exhausted ({session.output_tokens_used}/{session.token_budget})",
            )
