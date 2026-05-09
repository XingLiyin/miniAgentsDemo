"""依赖注入：构建并返回 Service 实例（Agent Loop v2）。"""

from __future__ import annotations

from functools import lru_cache

from app.config.settings import get_settings
from app.domain.events.event_bus import get_event_bus
from app.domain.services.agent_template_service import AgentTemplateService
from app.domain.services.blackboard_service import BlackboardService
from app.domain.services.memory_service import MemoryService
from app.domain.services.mcp_service import MCPService
from app.domain.services.session_service import SessionService
from app.domain.services.task_service import TaskService
from app.domain.state_machine import SessionStateMachine, TaskStateMachine
from app.llm.registry import get_llm_registry
from app.orchestrator.lifecycle_manager import LifecycleManager
from app.orchestrator.session_manager import SessionManager
from app.orchestrator.task_manager import TaskManager
from app.orchestrator.task_queue import TaskQueue
from app.runtime.actor import Actor
from app.runtime.agent_loop import AgentLoop
from app.runtime.observer import Observer
from app.runtime.policy_engine import PolicyEngine
from app.runtime.policy_rule import BashExecGuardRule, WhitelistRule
from app.runtime.reasoner import Reasoner
from app.runtime.tool_gateway import ToolGateway
from app.skills.registry import get_skill_registry
from app.domain.services.skill_source_service import RemoteSkillSourceService
from app.storage.file.remote_skill_source_store import RemoteSkillSourceStore
from app.tools.registry import ToolRegistry
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
def get_tool_registry() -> ToolRegistry:
    from app.tools.builtins import get_builtin_tools
    registry = ToolRegistry()
    registry.register_tools(get_builtin_tools(), name="builtin")
    return registry


@lru_cache
def get_mcp_service() -> MCPService:
    from app.storage.file.mcp_config_store import MCPConfigStore
    return MCPService(
        tool_registry=get_tool_registry(),
        store=MCPConfigStore(),
    )


@lru_cache
def get_remote_skill_source_service() -> RemoteSkillSourceService:
    return RemoteSkillSourceService(
        registry=get_skill_registry(),
        store=RemoteSkillSourceStore(),
    )


@lru_cache
def get_task_queue() -> TaskQueue:
    return TaskQueue(task_svc=get_task_service())


@lru_cache
def get_task_manager() -> TaskManager:
    settings = get_settings()
    return TaskManager(
        task_svc=get_task_service(),
        session_svc=get_session_service(),
        task_queue=get_task_queue(),
        lifecycle_manager=get_lifecycle_manager(),
        event_bus=get_event_bus(),
        max_task_retries=settings.max_task_retries,
        memory_svc=get_memory_service(),
        template_svc=get_agent_template_service(),
    )


@lru_cache
def get_tool_gateway() -> ToolGateway:
    registry = get_tool_registry()
    registry.register_control_tools(get_task_service(), get_session_service())
    return ToolGateway(
        policy=PolicyEngine([
            WhitelistRule(registry),
            BashExecGuardRule(get_session_service()),
        ]),
        tool_registry=registry,
        tool_call_store=ToolCallStore(),
    )


@lru_cache
def get_agent_template_registry():
    """获取全局 AgentTemplateRegistry（首次调用时从 settings.agents_dir 扫描）。"""
    from app.agent_template.registry import AgentTemplateRegistry
    registry = AgentTemplateRegistry(template_service=get_agent_template_service())
    registry.load_from_dir(get_settings().agents_dir)
    return registry


def _get_llm_client():
    """获取 LLM 客户端，注册表中无匹配时降级为 MockChatClient。"""
    from app.config.settings import get_settings
    llm_name = get_settings().default_llm_provider
    try:
        return get_llm_registry().get_client(llm_name)
    except Exception:
        from app.llm.mock_client import MockChatClient
        return MockChatClient()


@lru_cache
def get_reasoner() -> Reasoner:
    return Reasoner(
        memory_svc=get_memory_service(),
        blackboard_svc=get_blackboard_service(),
        tool_registry=get_tool_registry(),
        skill_registry=get_skill_registry(),
        task_svc=get_task_service(),
        agent_template_registry=get_agent_template_registry(),
        compaction_agent=get_compaction_agent(),
        agent_store=AgentStore(),
    )


@lru_cache
def get_actor() -> Actor:
    return Actor(
        tool_gateway=get_tool_gateway(),
        task_svc=get_task_service(),
        session_svc=get_session_service(),
    )


@lru_cache
def get_compaction_agent():
    from app.runtime.memory_compaction import MemoryCompactionAgent

    agents_dir = get_settings().agents_dir
    soul_path = agents_dir / "memory-compactor" / "SOUL.md"
    soul = ""
    tool_allowlist: list[str] = []
    try:
        from app.agent_template.loader import _parse_agent_md
        raw = soul_path.read_text(encoding="utf-8")
        frontmatter, soul = _parse_agent_md(raw)
        tools = frontmatter.get("tools") or {}
        if isinstance(tools, dict):
            tool_allowlist = tools.get("required") or []
        elif isinstance(tools, list):
            tool_allowlist = tools
    except Exception:
        import logging
        logging.getLogger(__name__).warning("deps: failed to load memory-compactor SOUL.md from %s", soul_path)

    return MemoryCompactionAgent(
        llm_client=_get_llm_client(),
        tool_registry=get_tool_registry(),
        soul=soul,
        tool_allowlist=tool_allowlist,
        keep_last=get_settings().compaction_keep_last,
    )


@lru_cache
def get_agent_loop() -> AgentLoop:
    return AgentLoop(
        session_svc=get_session_service(),
        task_svc=get_task_service(),
        memory_svc=get_memory_service(),
        blackboard_svc=get_blackboard_service(),
        agent_store=AgentStore(),
        reasoner=get_reasoner(),
        actor=get_actor(),
        observer=Observer(tool_gateway=get_tool_gateway(), task_svc=get_task_service(), session_svc=get_session_service()),
    )


@lru_cache
def get_lifecycle_manager() -> LifecycleManager:
    settings = get_settings()
    lm = LifecycleManager(
        session_svc=get_session_service(),
        agent_store=AgentStore(),
        event_bus=get_event_bus(),
        max_concurrent_agents=settings.max_concurrent_agents,
        max_concurrent_tasks=settings.max_concurrent_tasks,
        max_spawn_depth=settings.max_spawn_depth,
        template_svc=get_agent_template_service(),
        memory_svc=get_memory_service(),
        template_registry=get_agent_template_registry(),
    )
    lm.set_agent_loop(get_agent_loop())
    return lm


@lru_cache
def get_session_manager() -> SessionManager:
    mgr = SessionManager(
        session_svc=get_session_service(),
        template_svc=get_agent_template_service(),
        agent_store=AgentStore(),
        event_bus=get_event_bus(),
        task_svc=get_task_service(),
        memory_svc=get_memory_service(),
        task_store=TaskStore(),
        tool_call_store=ToolCallStore(),
        blackboard_store=BlackboardStore(),
        template_registry=get_agent_template_registry(),
    )
    mgr.set_lifecycle_manager(get_lifecycle_manager())
    mgr.set_task_manager(get_task_manager())
    return mgr
