"""MCP (Model Context Protocol) stdio 工具提供者。

使用 agent_framework._mcp.MCPStdioTool 作为底层实现。
通过后台事件循环线程将 AF 的全 async API 桥接为同步 ToolProvider 接口。

使用示例：
    provider = MCPStdioProvider(
        name="filesystem",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "."],
    )
    provider.start()
    registry.register_provider(provider)
    # ... 使用工具 ...
    provider.stop()
"""

from __future__ import annotations

import logging
from typing import Any

from agent_framework._mcp import MCPStdioTool

from app.tools.mcp_base import _MCPProviderBase, _MetaInjectingMixin, _parse_mcp_tool_result

logger = logging.getLogger(__name__)


class MCPStdioProvider(_MCPProviderBase):
    """MCP Server stdio 工具提供者（agent-framework MCPStdioTool 同步封装）。

    start() 启动后台事件循环线程并完成 MCP 握手；
    stop() 关闭连接并终止线程。
    """

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """
        Args:
            name:    MCP server 标识名（用于日志）
            command: 可执行文件路径，如 "npx" 或 "python"
            args:    命令参数列表，如 ["-y", "@mcp/server-fs", "."]
            env:     额外的环境变量
        """
        class _Tool(_MetaInjectingMixin, MCPStdioTool):
            pass

        af_tool = _Tool(
            name=name,
            command=command,
            args=args or [],
            env=env,
            parse_tool_results=_parse_mcp_tool_result,
        )
        super().__init__(af_tool, thread_name="mcp-stdio-loop")

    def start(self) -> None:
        """启动后台事件循环，连接 MCP Server 并加载工具列表。"""
        self._start_loop()
        self._run_sync(self._af_tool.connect())
        self._run_sync(self._af_tool.load_tools())
        self._initialized = True
        logger.info("MCPStdioProvider started, %d tools loaded", len(self._af_tool.functions))
