"""Session 生命周期编排器（Phase 1）。

职责：创建 Session → 创建 root Agent → 异步触发 AgentLoop。
Guard 检查也在此处（token_budget）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.api.v1.schemas.session import InitialTaskConfig
from app.common.errors import AppError
from app.common.utils import new_agent_id, now_iso
from app.config.settings import get_settings
from app.domain.events.event_bus import EventBus
from app.domain.models.agent import Agent, LoopGuard
from app.domain.models.session import Session
from app.domain.services.agent_template_service import AgentTemplateService
from app.domain.services.memory_service import MemoryService
from app.domain.services.session_service import SessionService
from app.domain.services.task_service import TaskService
from app.storage.file.agent_store import AgentStore
from app.storage.file.blackboard_store import BlackboardStore
from app.storage.file.task_store import TaskStore
from app.storage.file.tool_call_store import ToolCallStore

if TYPE_CHECKING:
    from app.agent_template.definition import AgentDefContent
    from app.orchestrator.lifecycle_manager import LifecycleManager

logger = logging.getLogger(__name__)


def _extract_text(content: str | list) -> str:
    """从 str 或 list[ContentPart dict] 中提取纯文本，用于 str-only 字段。"""
    if isinstance(content, str):
        return content
    return '\n'.join(p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') == 'text')


def _apply_template_to_agent(tpl: "AgentTemplate", content: "AgentDefContent | None", agent: Agent) -> None:  # type: ignore[name-defined]
    agent.soul_md = content.soul_md if content else ""
    agent.role_md = content.role_md if content else ""
    agent.act_tool_list = tpl.act_tool_list
    agent.observe_tool_list = tpl.observe_tool_list
    agent.mcp_act_servers = tpl.mcp_act_servers
    agent.mcp_observe_servers = tpl.mcp_observe_servers


class SessionManager:
    """会话生命周期编排。"""

    def __init__(
        self,
        session_svc: SessionService,
        template_svc: AgentTemplateService,
        agent_store: AgentStore,
        event_bus: EventBus,
        task_svc: TaskService | None = None,
        memory_svc: MemoryService | None = None,
        task_store: TaskStore | None = None,
        tool_call_store: ToolCallStore | None = None,
        blackboard_store: BlackboardStore | None = None,
        template_registry=None,
    ) -> None:
        self._session_svc = session_svc
        self._template_svc = template_svc
        self._agent_store = agent_store
        self._bus = event_bus
        self._task_svc = task_svc
        self._memory_svc = memory_svc
        self._task_store = task_store
        self._tool_call_store = tool_call_store
        self._blackboard_store = blackboard_store
        self._template_registry = template_registry
        self._lifecycle_manager: "LifecycleManager | None" = None

    def set_lifecycle_manager(self, lm: "LifecycleManager") -> None:
        """注入 LifecycleManager（避免循环导入）。"""
        self._lifecycle_manager = lm

    def create_session(
        self,
        user_prompt: str,
        template_id: str | None = None,
        token_budget: int | None = None,
        root_max_turns: int | None = None,
        llm_provider: str | None = None,
        llm_model: str | None = None,
        initial_task: InitialTaskConfig | None = None,
    ) -> tuple[Session, str]:
        """创建 Session + root Agent，返回 (Session, root_agent_id)。
        由调用方决定是否/如何启动 AgentLoop（sync or async）。
        """
        settings = get_settings()
        session = self._session_svc.create(
            user_prompt=user_prompt,
            template_id=template_id,
            token_budget=token_budget or settings.default_token_budget,
            root_max_turns=root_max_turns or settings.default_root_max_turns,
        )

        # 构建 root Agent
        tpl = None
        if template_id:
            try:
                tpl = self._template_svc.get(template_id)
            except AppError:
                logger.warning("Template %s not found, falling back to default", template_id)
        if tpl is None:
            tpl = self._template_svc.get_by_name(settings.default_agent_template_name)
            if tpl is None:
                logger.warning(
                    "Default template '%s' not found, using settings fallback",
                    settings.default_agent_template_name,
                )

        now = now_iso()
        agent = Agent(
            id=new_agent_id(),
            session_id=session.id,
            template_id=template_id,
            name="root",
            status="IDLE",
            act_tool_list=tpl.act_tool_list if tpl else [],
            observe_tool_list=tpl.observe_tool_list if tpl else [],
            soul_path=tpl.source_dir or None if tpl else None,
            loop_guard=LoopGuard(turns_used=0, max_turns=session.root_max_turns),
            llm_provider=llm_provider or settings.default_llm_provider,
            llm_model=llm_model or "",
            has_spawn_permission=True,
            spawn_depth=0,
            created_at=now,
            updated_at=now,
        )
        if tpl is not None:
            content = self._template_registry.load_content(tpl.name) if self._template_registry else None
            _apply_template_to_agent(tpl, content, agent)
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

        # 初始化 LifecycleManager session 状态
        if self._lifecycle_manager is not None:
            self._lifecycle_manager.init_session(session.id)

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
        """找到 session 的第一个 PENDING task，交由 LifecycleManager 启动。"""
        if self._lifecycle_manager is None:
            logger.warning("LifecycleManager not set, session %s will not run", session_id)
            return
        if self._task_svc is None:
            logger.error("task_svc not set, cannot schedule session %s", session_id)
            return
        tasks = self._task_svc.list_pending(session_id)
        if not tasks:
            logger.error("No pending task found for session %s", session_id)
            return
        self._lifecycle_manager.schedule_initial_task(session_id, agent_id, tasks[0].id)

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
        if (not title or not description) and self._lifecycle_manager is not None:
            meta_task = self._task_svc.create(
                session_id=session_id,
                creator_agent_id=creator_agent_id,
                user_prompt=user_prompt,
                title="Update task meta data details",
                description=f"Summarize the user prompt ({_extract_text(user_prompt)}) and fill in the task title and description accordingly",
                inputs={
                    "subagent_template": "metadata_filler",
                    "target_task_id": task.id,
                    "_daemon": True,
                },
            )
            self._lifecycle_manager.spawn_daemon_task(session_id, creator_agent_id, meta_task.id)

    def continue_session(self, session_id: str, user_message: str | list, *, initial_task: InitialTaskConfig | None = None) -> Session:
        """Append a user message and re-start the agent loop if the session has ended."""
        from app.domain.services.memory_service import MemoryService
        from app.storage.file.memory_store import MemoryStore

        # 提取纯文本用于 session/task 的 str 字段显示
        text_prompt = _extract_text(user_message)

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

        # Loop still active — message will be picked up automatically
        if session.status in ("QUEUED", "RUNNING"):
            return session

        # Session ended — reuse the existing root agent, reset its state
        if not session.root_agent_id:
            raise AppError("AGENT_NOT_FOUND", f"Session {session_id} has no root agent")
        agent_data = self._agent_store.get(session.root_agent_id)
        if agent_data is None:
            raise AppError("AGENT_NOT_FOUND", f"Root agent {session.root_agent_id} not found")
        agent = Agent.from_dict(agent_data)
        agent.status = "IDLE"
        agent.loop_guard.turns_used = 0
        agent.updated_at = now_iso()
        self._agent_store.save(agent.to_dict())
        self._session_svc.transition(session_id, "QUEUED")
        # 重新初始化 LM 状态（旧 session 的状态已过期）
        if self._lifecycle_manager is not None:
            self._lifecycle_manager.init_session(session_id)

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
        from app.runtime.hitl_store import get_hitl_store

        session = self._session_svc.get(session_id)
        if session.status != "WAITING_INPUT":
            raise AppError(
                "INVALID_STATE",
                f"Session {session_id} is not waiting for input (status={session.status})",
            )

        # 注入答案并唤醒阻塞的工作线程
        get_hitl_store().submit(session_id, content)

        # # 写入记忆（由 entry 携带的 agent_id 确定归属）
        # if entry is not None and self._memory_svc is not None:
        #     self._memory_svc.append_message(
        #         agent_id=entry.agent_id,
        #         role="user",
        #         content=content,
        #         session_id=session_id,
        #     )

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

        # 删除该 session 下的所有 Task 文件
        if self._task_svc is not None and self._task_store is not None:
            tasks = self._task_svc.list_by_session(session_id)
            for task in tasks:
                self._task_store.delete(task.id)

        # 删除工具调用日志（单个 JSONL 文件）
        if self._tool_call_store is not None:
            self._tool_call_store.delete(session_id)

        # 删除该 session 下所有 agent 的记忆（memory 以 agent_id 为 key）
        if self._memory_svc is not None:
            for aid in self._agent_store.list_by_session(session_id):
                self._memory_svc.delete_agent(aid)
            # root agent 可能已在上方遍历到，but delete_agent 是幂等的
            if session.root_agent_id:
                self._memory_svc.delete_agent(session.root_agent_id)

        # 删除 blackboard 目录
        if self._blackboard_store is not None:
            self._blackboard_store.delete_session(session_id)

        # 删除 agent 文件
        if session.root_agent_id:
            self._agent_store.delete(session.root_agent_id)

        # 删除事件日志（SSE 历史回放）
        try:
            from app.storage.file.event_store import get_event_store
            get_event_store().delete(session_id)
        except Exception:
            pass

        # 最后删除 session 文件
        self._session_svc.delete(session_id)

    def check_token_budget(self, session: Session) -> None:
        """Guard 硬检查：超出 token_budget 立即抛出 AppError。"""
        if session.token_used >= session.token_budget:
            raise AppError(
                "TOKEN_BUDGET_EXCEEDED",
                f"Session {session.id} token budget exhausted ({session.token_used}/{session.token_budget})",
            )
