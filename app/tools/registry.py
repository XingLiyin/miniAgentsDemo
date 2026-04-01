"""工具注册表（全局单例）。

职责：
- 存储并索引所有 ToolDefinition（跨 Provider）
- to_llm_tools()  — 生成 LLMTool[]，供 LLM 感知可用工具
- get() / is_registered() — 供 ToolGateway 和 PolicyEngine 使用
- register_provider() — 批量接入 ToolProvider（内置或外部 MCP）
- register_mcp_stdio() / register_mcp_http() — 一步启动并注册 MCP Server
- shutdown() — 统一停止所有 MCP Provider（在应用退出时调用）
- refresh_mcp() — 重新拉取 MCP 工具列表并同步变更到外部 DB
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.common.errors import AppError
from app.llm.types import LLMTool
from app.tools.definition import ToolDefinition
from app.tools.provider import ToolProvider

if TYPE_CHECKING:
    from app.tools.mcp_base import _MCPProviderBase

logger = logging.getLogger(__name__)


class ToolRegistry:
    """工具注册表（内存单例）。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._mcp_providers: list[_MCPProviderBase] = []
        # provider name → 该 provider 贡献的工具名列表（用于按名删除）
        self._provider_tool_names: dict[str, list[str]] = {}

    def register(self, tool_def: ToolDefinition) -> None:
        """注册单个工具定义（重复注册会覆盖）。"""
        self._tools[tool_def.name] = tool_def
        logger.debug("ToolRegistry: registered tool '%s'", tool_def.name)

    def register_provider(self, provider: ToolProvider, name: str = "builtin") -> None:
        """批量注册 Provider 提供的所有工具定义，并同步到外部工具存储。

        这是接入外部 MCP Server 的扩展点：
        只需传入已 start() 的 MCPProvider 实例即可接入远端工具，
        无需修改 ToolRegistry 或 ToolGateway 的任何代码。

        Args:
            provider: 工具提供者
            name:     provider 标识名（用于外部 DB 同步，默认 "builtin"）
        """
        tool_defs = list(provider.list_definitions())
        for tool_def in tool_defs:
            self.register(tool_def)
        self._provider_tool_names[name] = [td.name for td in tool_defs]
        self._sync_added(tool_defs, provider=name)

    def register_mcp_stdio(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> list[str]:
        """启动 MCP stdio Server 并将其所有工具注册到 Registry。

        Registry 持有 provider 引用，shutdown() 时自动调用 stop()。

        Args:
            name:    MCP server 标识名（用于日志）
            command: 可执行文件路径，如 "npx" 或 "python"
            args:    命令参数列表，如 ["-y", "@mcp/server-fs", "."]
            env:     额外的环境变量

        Returns:
            注册成功的工具名列表
        """
        from app.tools.mcp_provider import MCPStdioProvider

        provider = MCPStdioProvider(name=name, command=command, args=args, env=env)
        provider.start()
        self._mcp_providers.append(provider)
        tool_names = self._register_provider_with_tracking(name, provider)
        return tool_names

    def register_mcp_http(
        self,
        name: str,
        url: str,
        *,
        timeout: int = 30,
    ) -> list[str]:
        """启动 MCP Streamable HTTP Server 并将其所有工具注册到 Registry。

        Registry 持有 provider 引用，shutdown() 时自动调用 stop()。

        Args:
            name:    MCP server 标识名（用于日志）
            url:     MCP Server 的 HTTP 端点，如 "http://my-server/mcp"
            timeout: 请求超时秒数（默认 30）

        Returns:
            注册成功的工具名列表
        """
        from app.tools.mcp_http_provider import MCPStreamableHTTPProvider

        provider = MCPStreamableHTTPProvider(name=name, url=url, timeout=timeout)
        provider.start()
        self._mcp_providers.append(provider)
        tool_names = self._register_provider_with_tracking(name, provider)
        return tool_names

    def shutdown_one(self, name: str) -> None:
        """停止并移除指定名称的 MCP Provider 及其工具。"""
        provider = next((p for p in self._mcp_providers if getattr(p._af_tool, "name", None) == name), None)
        if provider is None:
            logger.warning("ToolRegistry.shutdown_one: provider '%s' not found", name)
            return
        try:
            provider.stop()
        except Exception:
            logger.exception("Error stopping MCP provider '%s'", name)
        self._mcp_providers.remove(provider)
        # 删除该 provider 贡献的工具，同步到外部 DB
        removed_names = self._provider_tool_names.pop(name, [])
        for tool_name in removed_names:
            self._tools.pop(tool_name, None)
        self._sync_removed(removed_names)
        logger.debug("ToolRegistry: removed provider '%s'", name)

    def refresh_mcp(self, name: str) -> tuple[list[str], list[str]]:
        """重新从 MCP Server 拉取工具列表，diff 并同步变更到外部 DB。

        Returns:
            (added_names, removed_names) — 本次新增和删除的工具名列表
        """
        provider = next((p for p in self._mcp_providers if getattr(p._af_tool, "name", None) == name), None)
        if provider is None:
            raise AppError("MCP_NOT_FOUND", f"MCP provider '{name}' not found in registry")

        new_defs = provider.reload_tools()
        old_names = set(self._provider_tool_names.get(name, []))
        new_names = {td.name for td in new_defs}

        added_names = sorted(new_names - old_names)
        removed_names = sorted(old_names - new_names)

        # 更新内存
        for tool_name in removed_names:
            self._tools.pop(tool_name, None)
        added_defs = []
        for td in new_defs:
            self._tools[td.name] = td
            if td.name in added_names:
                added_defs.append(td)
        self._provider_tool_names[name] = sorted(new_names)

        # 同步到外部 DB
        if added_defs:
            self._sync_added(added_defs, provider=name)
        if removed_names:
            self._sync_removed(removed_names)

        logger.info(
            "ToolRegistry.refresh_mcp '%s': +%d -%d",
            name, len(added_names), len(removed_names),
        )
        return added_names, removed_names

    def _register_provider_with_tracking(self, name: str, provider: ToolProvider) -> list[str]:
        """注册 provider 的工具，记录 name → tool_names 映射，并同步到外部 DB。"""
        tool_defs = list(provider.list_definitions())
        tool_names = []
        for tool_def in tool_defs:
            self.register(tool_def)
            tool_names.append(tool_def.name)
        self._provider_tool_names[name] = tool_names
        self._sync_added(tool_defs, provider=name)
        return tool_names

    def shutdown(self) -> None:
        """停止所有 MCP Provider（在应用退出时调用）。"""
        for provider in self._mcp_providers:
            try:
                provider.stop()
            except Exception:
                logger.exception("Error stopping MCP provider %r", provider)
        self._mcp_providers.clear()

    def get(self, name: str) -> ToolDefinition:
        """获取工具定义；不存在则抛 AppError。"""
        if name not in self._tools:
            raise AppError("TOOL_NOT_FOUND", f"Tool '{name}' is not registered")
        return self._tools[name]

    def is_registered(self, name: str) -> bool:
        return name in self._tools

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def list_all_definitions(self) -> list[ToolDefinition]:
        """返回所有已注册工具的定义列表。"""
        return list(self._tools.values())

    def to_llm_tools(self, names: list[str]) -> list[LLMTool]:
        """将工具名称列表转为 LLMTool[]（名称不存在的静默跳过）。"""
        result = []
        for name in names:
            if name in self._tools:
                result.append(self._tools[name].to_llm_tool())
            else:
                logger.warning("ToolRegistry.to_llm_tools: unknown tool '%s', skipped", name)
        return result

    # ── 内部同步helpers ───────────────────────────────────────────────────

    def _sync_added(self, tool_defs: list[ToolDefinition], provider: str) -> None:
        try:
            from app.tools.tool_sync import get_tool_sync_service
            get_tool_sync_service().on_tools_added(tool_defs, provider=provider)
        except Exception:
            logger.warning("ToolRegistry: sync-added failed for provider '%s'", provider, exc_info=True)

    def _sync_removed(self, names: list[str]) -> None:
        try:
            from app.tools.tool_sync import get_tool_sync_service
            get_tool_sync_service().on_tools_removed(names)
        except Exception:
            logger.warning("ToolRegistry: sync-removed failed for %s", names, exc_info=True)


# ── 全局单例 ──────────────────────────────────────────────────────────────

_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """获取全局工具注册表（首次调用时注册所有内置工具）。"""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        from app.tools.builtins import get_builtin_provider
        _registry.register_provider(get_builtin_provider(), name="builtin")
    return _registry
