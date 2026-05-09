"""工具注册表（全局单例）。

职责：
- _tools          — 存储 builtin 工具定义（静态）
- _mcp_providers  — 存储 MCP server 连接（server_name → provider）
- MCP 工具列表每次从 provider.list_definitions() 实时读取（内存快照，无网络调用）
- to_llm_tools()  — builtin 优先，MCP 实时补充，按 names 过滤
- get()           — 先查 builtin，再实时查 MCP
- shutdown()      — 统一停止所有 MCP Provider
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from app.common.errors import AppError
from app.llm.types import LLMTool
from app.tools.control_tools import get_control_tools
from app.tools.types import ToolDefinition

if TYPE_CHECKING:
    from app.tools.mcp_base import _MCPProviderBase

logger = logging.getLogger(__name__)


class ToolRegistry:
    """工具注册表（内存单例）。"""

    _CONNECT_COOLDOWN = 30.0  # 连接失败后的最小重试间隔（秒）

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}             # builtin only
        self._mcp_providers: dict[str, _MCPProviderBase] = {}   # server_name → provider（懒连接）
        self._last_connect_attempt: dict[str, float] = {}       # server_name → monotonic timestamp

    # ── Builtin 工具注册 ──────────────────────────────────────────────────────

    def register_tools(self, definitions: list[ToolDefinition], name: str = "builtin") -> None:
        """批量注册内置工具。"""
        for tool_def in definitions:
            self._tools[tool_def.name] = tool_def
            logger.debug("ToolRegistry: registered tool '%s' from %s", tool_def.name, name)

    def register_control_tools(self, task_svc, session_svc) -> None:
        """注入服务并批量注册控制工具。"""
        from app.tools.control_tools import get_control_tools
        for tool_def in get_control_tools(task_svc, session_svc):
            self._tools[tool_def.name] = tool_def
            logger.debug("ToolRegistry: registered tool '%s' from control tools", tool_def.name)

    # ── MCP Server 注册 ───────────────────────────────────────────────────────

    def register_mcp_stdio(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """注册 MCP stdio Server（懒连接：首次使用时才建立连接）。"""
        from app.tools.mcp_provider import MCPStdioProvider
        provider = MCPStdioProvider(name=name, command=command, args=args, env=env)
        self._mcp_providers[name] = provider
        logger.info("ToolRegistry: registered MCP stdio server '%s' (lazy)", name)

    def register_mcp_http(
        self,
        name: str,
        url: str,
        *,
        timeout: int = 30,
    ) -> None:
        """注册 MCP Streamable HTTP Server（懒连接：首次使用时才建立连接）。"""
        from app.tools.mcp_http_provider import MCPStreamableHTTPProvider
        provider = MCPStreamableHTTPProvider(name=name, url=url, timeout=timeout)
        self._mcp_providers[name] = provider
        logger.info("ToolRegistry: registered MCP http server '%s' (lazy)", name)

    def shutdown_one(self, name: str) -> None:
        """停止并移除指定 MCP Server。"""
        provider = self._mcp_providers.pop(name, None)
        self._last_connect_attempt.pop(name, None)
        if provider is None:
            logger.warning("ToolRegistry.shutdown_one: server '%s' not found", name)
            return
        if provider._initialized:
            try:
                provider.stop()
            except Exception:
                logger.exception("Error stopping MCP provider '%s'", name)
        logger.info("ToolRegistry: removed MCP server '%s'", name)

    def refresh_mcp(self, name: str) -> None:
        """重新从 MCP Server 拉取工具列表；未连接时主动发起连接。"""
        provider = self._mcp_providers.get(name)
        if provider is None:
            raise AppError("MCP_NOT_FOUND", f"MCP server '{name}' not found in registry")
        if not provider._initialized:
            self._try_connect(name, provider)
        else:
            provider.reload_tools()
        logger.info("ToolRegistry.refresh_mcp: '%s' reloaded", name)

    def shutdown(self) -> None:
        """停止所有 MCP Provider（应用退出时调用）。"""
        for name, provider in list(self._mcp_providers.items()):
            try:
                provider.stop()
            except Exception:
                logger.exception("Error stopping MCP provider '%s'", name)
        self._mcp_providers.clear()

    # ── 查询 ─────────────────────────────────────────────────────────────────

    def get(self, name: str) -> ToolDefinition:
        """获取工具定义：先查 builtin，再实时查 MCP providers。"""
        if name in self._tools:
            return self._tools[name]
        for td in self._live_mcp_definitions():
            if td.name == name:
                return td
        raise AppError("TOOL_NOT_FOUND", f"Tool '{name}' is not registered")

    def is_registered(self, name: str) -> bool:
        if name in self._tools:
            return True
        return any(td.name == name for td in self._live_mcp_definitions())

    def get_server_tool_names(self, server_name: str) -> list[str]:
        """返回指定 MCP server 的工具名列表（实时读取，未连接时触发懒连接）。"""
        provider = self._mcp_providers.get(server_name)
        if provider is None:
            return []
        if not provider._initialized:
            if not self._try_connect(server_name, provider):
                return []
        try:
            return [td.name for td in provider.list_definitions()]
        except Exception as e:
            logger.warning("ToolRegistry.get_server_tool_names '%s' failed: %s", server_name, e)
            return []

    def get_control_tool_names(self) -> frozenset[str]:
        return frozenset(self._control_tool_names)

    def list_names(self) -> list[str]:
        mcp_names = [td.name for td in self._live_mcp_definitions()]
        return list(self._tools.keys()) + [n for n in mcp_names if n not in self._tools]

    def list_all_definitions(self) -> list[ToolDefinition]:
        mcp = {td.name: td for td in self._live_mcp_definitions()}
        return list({**mcp, **self._tools}.values())  # builtin 覆盖同名 MCP

    def to_llm_tools(self, names: list[str]) -> list[LLMTool]:
        """将工具名列表转为 LLMTool[]；builtin 优先，MCP 实时补充。"""
        mcp_map = {td.name: td for td in self._live_mcp_definitions()}
        combined = {**mcp_map, **self._tools}  # builtin 优先级高
        result = []
        for name in names:
            if name in combined:
                result.append(combined[name].to_llm_tool())
            else:
                logger.warning("ToolRegistry.to_llm_tools: unknown tool '%s', skipped", name)
        return result

    # ── 内部 ─────────────────────────────────────────────────────────────────

    def _live_mcp_definitions(self) -> list[ToolDefinition]:
        """从所有 MCP providers 读取工具定义；未连接的按冷却时间懒启动。"""
        result: list[ToolDefinition] = []
        for name, provider in self._mcp_providers.items():
            if not provider._initialized:
                if not self._try_connect(name, provider):
                    continue
            try:
                defs = provider.list_definitions()
                for td in defs:
                    if td.name in self._tools:
                        logger.warning(
                            "ToolRegistry: MCP tool '%s' from server '%s' conflicts with a builtin tool; builtin takes precedence",
                            td.name, name,
                        )
                result.extend(defs)
            except Exception as e:
                logger.warning("ToolRegistry: list_definitions failed for '%s': %s", name, e)
        return result

    def _try_connect(self, name: str, provider: _MCPProviderBase) -> bool:
        """尝试启动未连接的 provider，冷却期内跳过。返回是否连接成功。"""
        now = time.monotonic()
        if now - self._last_connect_attempt.get(name, 0) < self._CONNECT_COOLDOWN:
            return False
        self._last_connect_attempt[name] = now
        try:
            provider.start()
            logger.info("ToolRegistry: connected MCP server '%s'", name)
            return True
        except Exception as e:
            logger.warning("ToolRegistry: failed to connect '%s': %s", name, e)
            return False


