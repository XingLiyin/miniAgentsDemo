"""Agent 领域模型。"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class MemoryConfig:
    """Agent 记忆配置。"""

    recent_message_window: int = 20
    summary_threshold: int = 20
    retrieval_top_k: int = 8


class Agent:
    """Agent 领域对象。"""

    def __init__(
        self,
        agent_id: str,
        name: str,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        memory_item: Optional[Dict[str, Any]] = None,
        tool_list: Optional[List[str]] = None,
        skill_list: Optional[List[str]] = None,
        active_task: Optional[str] = None,
        session_id: Optional[str] = None,
        memory_config: Optional[MemoryConfig] = None,
        memory_service: Optional[Any] = None,
        blackboard_service: Optional[Any] = None,
        artifact_store: Optional[Any] = None,
        skill_router: Optional[Any] = None,
        tool_gateway: Optional[Any] = None,
        policy_engine: Optional[Any] = None,
        llm_client: Optional[Any] = None,
        task_manager: Optional[Any] = None,
        event_bus: Optional[Any] = None,
        audit_logger: Optional[Any] = None,
    ) -> None:
        """创建 Agent 对象。"""
        self.id = agent_id
        self.name = name
        self.model = model
        self.system_prompt = system_prompt
        self.memory_item = memory_item
        self.tool_list = tool_list or []
        self.skill_list = skill_list or []
        self.active_task = active_task
        self.session_id = session_id
        self.memory_config = memory_config or MemoryConfig()
        self.memory_service = memory_service
        self.blackboard_service = blackboard_service
        self.artifact_store = artifact_store
        self.skill_router = skill_router
        self.tool_gateway = tool_gateway
        self.policy_engine = policy_engine
        self.llm_client = llm_client
        self.task_manager = task_manager
        self.event_bus = event_bus
        self.audit_logger = audit_logger

    def build_context(self, session_id: str, task_id: str) -> Dict[str, Any]:
        """组装 prompt 上下文（TODO：读取 memory/blackboard/artifact）。"""
        raise NotImplementedError('Agent.build_context 未实现')

    def select_skills(self, context: Dict[str, Any]) -> List[str]:
        """基于上下文匹配技能（TODO：接入 SkillRouter）。"""
        raise NotImplementedError('Agent.select_skills 未实现')

    def plan(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """输出执行计划/任务列表（TODO：接入 LLM）。"""
        raise NotImplementedError('Agent.plan 未实现')

    def call_llm(self, request: Any) -> Any:
        """调用 LLM 并返回响应（TODO：接入 LLMClient）。"""
        raise NotImplementedError('Agent.call_llm 未实现')

    def parse_response(self, response: Any) -> Dict[str, Any]:
        """解析 LLM 返回（TODO：解析 tool_calls/结构化结果）。"""
        raise NotImplementedError('Agent.parse_response 未实现')

    def run_tools(self, calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """执行工具调用（TODO：权限校验与审计）。"""
        raise NotImplementedError('Agent.run_tools 未实现')

    def write_back(self, result: Dict[str, Any]) -> None:
        """回写结果到 memory/blackboard/events（TODO：落库与事件）。"""
        raise NotImplementedError('Agent.write_back 未实现')

    def run_once(self, session_id: str, task_id: str) -> None:
        """执行一次 Agent Loop（TODO：完整流程编排）。"""
        raise NotImplementedError('Agent.run_once 未实现')

    def run_until_done(self, session_id: str, task_id: str) -> None:
        """执行直到会话完成（TODO：循环与退出条件）。"""
        raise NotImplementedError('Agent.run_until_done 未实现')
