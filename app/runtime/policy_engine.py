"""工具白名单校验（Phase 1）。"""

from __future__ import annotations

from app.common.errors import AppError
from app.domain.models.agent import Agent
from app.tools.registry import ToolRegistry


class PolicyEngine:
    """基于 ToolRegistry + Agent.tool_list 的双层白名单校验。"""

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self._registry = tool_registry

    def authorize(self, agent: Agent, tool_name: str) -> None:
        """校验 agent 是否有权限调用 tool_name。

        后续可添加更复杂的授权逻辑，如基于角色的访问控制（RBAC）。

        第一层：tool_name 必须在 ToolRegistry 中已注册（工具存在性）
        第二层：tool_name 必须在 agent.tool_list 中（模板级授权）
        """
        if not self._registry.is_registered(tool_name):
            raise AppError(
                "TOOL_NOT_FOUND",
                f"Tool '{tool_name}' is not a registered tool",
            )
        if tool_name not in agent.tool_list:
            raise AppError(
                "TOOL_NOT_AUTHORIZED",
                f"Agent '{agent.id}' is not authorized to use tool '{tool_name}'",
            )
