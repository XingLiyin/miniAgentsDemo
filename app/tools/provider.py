"""ToolProvider：工具来源的抽象协议。

当前实现：
  - BuiltinToolProvider  —— 进程内注册的本地工具（bash_exec、http_request 等）

TODO: MCPServerProvider
  对接外部 MCP Server（Model Context Protocol）：
  - 通过 stdio / SSE 与外部进程通信
  - 调用 tools/list 获取远端工具定义，映射为 ToolDefinition
  - 调用 tools/call 执行远端工具，封装返回值为 ToolResult
  - 需处理连接维持、超时、重连、版本协商
  参考规范：https://spec.modelcontextprotocol.io
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.tools.definition import ToolDefinition, ToolResult


@runtime_checkable
class ToolProvider(Protocol):
    """工具提供者协议。

    ToolRegistry 通过此协议接入不同来源的工具，
    实现进程内工具与外部 MCP Server 的统一调度。
    """

    def list_definitions(self) -> list[ToolDefinition]:
        """返回该 Provider 提供的所有工具定义。"""
        ...

    def call(self, tool_name: str, arguments: dict) -> ToolResult:
        """执行指定工具并返回结果。

        工具不属于本 Provider 时应抛出 AppError("TOOL_NOT_FOUND", ...)。
        """
        ...


class BuiltinToolProvider:
    """进程内本地工具提供者。

    由 ToolRegistry 使用，封装已注册的本地 ToolDefinition。
    外部代码通常不直接实例化此类——通过 ToolRegistry.register() 注册即可。
    """

    def __init__(self, definitions: list[ToolDefinition]) -> None:
        self._defs: dict[str, ToolDefinition] = {d.name: d for d in definitions}

    def list_definitions(self) -> list[ToolDefinition]:
        return list(self._defs.values())

    def call(self, tool_name: str, arguments: dict) -> ToolResult:
        from app.common.errors import AppError

        if tool_name not in self._defs:
            raise AppError("TOOL_NOT_FOUND", f"Tool '{tool_name}' not in BuiltinToolProvider")
        return self._defs[tool_name].handler(arguments)
