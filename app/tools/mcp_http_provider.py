"""MCP (Model Context Protocol) Streamable HTTP 工具提供者。

使用 agent_framework.MCPStreamableHTTPTool 作为底层实现。
通过后台事件循环线程将 AF 的全 async API 桥接为同步 ToolProvider 接口。

使用示例：
    provider = MCPStreamableHTTPProvider(
        name="my-server",
        url="http://localhost:3000/mcp",
    )
    provider.start()
    registry.register_provider(provider)
    # ... 使用工具 ...
    provider.stop()
"""

from __future__ import annotations

import logging

from agent_framework import MCPStreamableHTTPTool

from app.tools.mcp_base import _MCPProviderBase

logger = logging.getLogger(__name__)


class MCPStreamableHTTPProvider(_MCPProviderBase):
    """MCP Server Streamable HTTP 工具提供者（agent-framework MCPStreamableHTTPTool 同步封装）。

    start() 启动后台事件循环线程并完成 MCP 握手；
    stop() 关闭连接并终止线程。
    """

    def __init__(
        self,
        name: str,
        url: str,
        *,
        timeout: int = 30,
    ) -> None:
        """
        Args:
            name: MCP server 标识名（用于日志和 tool_name_prefix）
            url:  MCP Server 的 HTTP 端点，如 "http://localhost:3000/mcp"
            timeout: 请求超时秒数（默认 30）
        """
        af_tool = MCPStreamableHTTPTool(
            name=name,
            url=url,
            load_tools=True,
            request_timeout=timeout,
        )
        super().__init__(af_tool, thread_name="mcp-http-loop")

    def start(self) -> None:
        """启动后台事件循环，连接 MCP Server 并加载工具列表。"""
        self._start_loop()
        self._run_sync(self._af_tool.connect())
        self._initialized = True
        logger.info(
            "MCPStreamableHTTPProvider started, %d tools loaded",
            len(self._af_tool.functions),
        )
