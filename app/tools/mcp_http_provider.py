"""MCP (Model Context Protocol) Streamable HTTP 工具提供者。

使用 mcp.client.streamable_http

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
from typing import Any

import httpx
from mcp.client.streamable_http import streamable_http_client

from app.tools.mcp_base import _MCPProviderBase

logger = logging.getLogger(__name__)


class MCPStreamableHTTPProvider(_MCPProviderBase):
    """MCP Server Streamable HTTP 工具提供者。"""

    def __init__(
        self,
        name: str,
        url: str,
        *,
        timeout: int = 30,
        connect_timeout: int = 5,
    ) -> None:
        super().__init__(thread_name="mcp-http-loop", request_timeout=timeout, connect_timeout=connect_timeout)
        self._name = name
        self._url = url

    def get_mcp_client(self) -> Any:
        http_client = httpx.AsyncClient(trust_env=False, timeout=self._request_timeout)
        return streamable_http_client(url=self._url, terminate_on_close=True, http_client=http_client)

    def start(self) -> None:
        self._start_loop()
        self._start_connect()
        self._finish_start()
        logger.info(
            "MCPStreamableHTTPProvider '%s' started, %d tools loaded",
            self._name,
            len(self._tools),
        )
