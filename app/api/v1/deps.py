"""依赖注入：构建并返回 Service 实例（Phase 1 全局单例）。"""

from __future__ import annotations

from functools import lru_cache

from app.domain.events.event_bus import get_event_bus
from app.domain.services.agent_template_service import AgentTemplateService
from app.domain.services.blackboard_service import BlackboardService
from app.domain.services.memory_service import MemoryService
from app.domain.services.session_service import SessionService
from app.domain.services.task_service import TaskService
from app.domain.state_machine import SessionStateMachine, TaskStateMachine
from app.llm.registry import get_llm_registry
from app.orchestrator.session_manager import SessionManager
from app.orchestrator.task_manager import TaskManager
from app.runtime.agent_loop import AgentLoop
from app.runtime.policy_engine import PolicyEngine
from app.runtime.skill_router import SkillRouter
from app.runtime.task_executor import TaskExecutor
from app.runtime.tool_gateway import ToolGateway
from app.skills.registry import get_skill_registry
from app.tools.registry import get_tool_registry
from app.storage.file.agent_store import AgentStore
from app.storage.file.agent_template_store import AgentTemplateStore
from app.storage.file.blackboard_store import BlackboardStore
from app.storage.file.memory_store import MemoryStore
from app.storage.file.session_store import SessionStore
from app.storage.file.task_store import TaskStore
from app.storage.file.tool_call_store import ToolCallStore


@lru_cache
def get_session_service() -> SessionService:
    return SessionService(
        store=SessionStore(),
        state_machine=SessionStateMachine(),
        event_bus=get_event_bus(),
    )


@lru_cache
def get_task_service() -> TaskService:
    return TaskService(
        store=TaskStore(),
        state_machine=TaskStateMachine(),
        event_bus=get_event_bus(),
    )


@lru_cache
def get_memory_service() -> MemoryService:
    return MemoryService(store=MemoryStore())


@lru_cache
def get_blackboard_service() -> BlackboardService:
    return BlackboardService(store=BlackboardStore())


@lru_cache
def get_agent_template_service() -> AgentTemplateService:
    return AgentTemplateService(store=AgentTemplateStore())


@lru_cache
def get_task_manager() -> TaskManager:
    return TaskManager(task_svc=get_task_service(), session_svc=get_session_service())


@lru_cache
def get_tool_gateway() -> ToolGateway:
    registry = get_tool_registry()
    return ToolGateway(
        policy=PolicyEngine(tool_registry=registry),
        tool_registry=registry,
        tool_call_store=ToolCallStore(),
    )


@lru_cache
def get_skill_router() -> SkillRouter:
    return SkillRouter(skill_registry=get_skill_registry())


@lru_cache
def get_task_executor() -> TaskExecutor:
    registry = get_llm_registry()
    from app.config.settings import get_settings
    llm_name = get_settings().agent_default_llm_name
    try:
        llm_client = registry.get_client(llm_name)
    except Exception:
        from app.llm.mock_adapter import MockAdapter
        from app.llm.llm_base import LLMClient
        llm_client = LLMClient(adapter=MockAdapter(), model="mock")
    return TaskExecutor(
        task_manager=get_task_manager(),
        tool_gateway=get_tool_gateway(),
        llm_client=llm_client,
        skill_router=get_skill_router(),
    )


@lru_cache
def get_agent_loop() -> AgentLoop:
    registry = get_llm_registry()
    from app.config.settings import get_settings
    llm_name = get_settings().agent_default_llm_name
    try:
        llm_client = registry.get_client(llm_name)
    except Exception:
        from app.llm.mock_adapter import MockAdapter
        from app.llm.llm_base import LLMClient
        llm_client = LLMClient(adapter=MockAdapter(), model="mock")
    return AgentLoop(
        session_svc=get_session_service(),
        task_svc=get_task_service(),
        memory_svc=get_memory_service(),
        blackboard_svc=get_blackboard_service(),
        task_executor=get_task_executor(),
        agent_store=AgentStore(),
        llm_client=llm_client,
        tool_registry=get_tool_registry(),
        skill_registry=get_skill_registry(),
        skill_router=get_skill_router(),
    )


@lru_cache
def get_session_manager() -> SessionManager:
    mgr = SessionManager(
        session_svc=get_session_service(),
        template_svc=get_agent_template_service(),
        agent_store=AgentStore(),
        event_bus=get_event_bus(),
    )
    mgr.set_agent_loop(get_agent_loop())
    return mgr
