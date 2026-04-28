"""工具白名单校验（Phase 1）。"""

from __future__ import annotations

from app.common.errors import AppError
from app.domain.models.agent import Agent
from app.tools.registry import ToolRegistry


class PolicyEngine:
    """基于 ToolRegistry + Agent 工具白名单的双层校验。

    有效 act 工具 = act_tool_list（显式列表）∪ mcp_act_servers 订阅的所有工具。
    """

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self._registry = tool_registry

    def authorize(self, agent: Agent, tool_name: str) -> None:
        """校验 agent 是否有权限调用 tool_name。

        第一层：tool_name 必须在 ToolRegistry 中已注册（工具存在性）
        第二层：tool_name 必须在 agent 的 act_tool_list 中，或在 agent 订阅的 mcp_act_servers 中（工具授权）
        """
        if not self._registry.is_registered(tool_name):
            raise AppError(
                "TOOL_NOT_FOUND",
                f"Tool '{tool_name}' is not a registered tool",
            )
        if tool_name in agent.act_tool_list or tool_name in agent.observe_tool_list:
            return
        for server_name in (agent.mcp_act_servers or []):
            if tool_name in self._registry.get_server_tool_names(server_name):
                return
        for server_name in (agent.mcp_observe_servers or []):
            if tool_name in self._registry.get_server_tool_names(server_name):
                return
        raise AppError(
            "TOOL_NOT_AUTHORIZED",
            f"Agent '{agent.id}' is not authorized to use tool '{tool_name}'",
        )
