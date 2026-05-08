"""MCP (Model Context Protocol) stdio 工具提供者。

使用 mcp.client.stdio

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

from mcp.client.stdio import StdioServerParameters, stdio_client

from app.tools.mcp_base import _MCPProviderBase

logger = logging.getLogger(__name__)


class MCPStdioProvider(_MCPProviderBase):
    """MCP Server stdio 工具提供者。"""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        super().__init__(thread_name="mcp-stdio-loop")
        self._name = name
        self._command = command
        self._args = args or []
        self._env = env

    def get_mcp_client(self) -> Any:
        return stdio_client(
            server=StdioServerParameters(
                command=self._command,
                args=self._args,
                env=self._env,
            )
        )

    def start(self) -> None:
        self._start_loop()
        self._run_sync(self._connect())
        self._initialized = True
        logger.info(
            "MCPStdioProvider '%s' started, %d tools loaded",
            self._name,
            len(self._tools),
        )
