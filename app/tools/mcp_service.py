"""MCP Server 管理服务。

职责：
- 注册 stdio / http MCP Server（注册到 ToolRegistry + 持久化配置）
- 删除 MCP Server（停止 Provider + 清除工具 + 删除持久化）
- 列出所有已持久化的 MCP Server 配置
- restore_all()：应用启动时从持久化配置恢复所有 MCP Server
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.common.errors import AppError
from app.storage.file.mcp_config_store import MCPConfigStore
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class MCPServerInfo:
    """MCP Server 的概要信息（用于 API 响应）。"""
    name: str
    type: str           # "stdio" | "http"
    tools: list[str]
    # stdio 字段
    command: str | None = None
    args: list[str] | None = None
    # http 字段
    url: str | None = None
    timeout: int | None = None


class MCPService:
    """MCP Server 注册与生命周期管理。"""

    def __init__(self, tool_registry: ToolRegistry, store: MCPConfigStore) -> None:
        self._registry = tool_registry
        self._store = store

    # ── 注册 ──────────────────────────────────────────────────────────────

    def register_stdio(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> MCPServerInfo:
        """注册 stdio MCP Server，并持久化配置。"""
        if self._store.get(name) is not None:
            raise AppError("MCP_ALREADY_EXISTS", f"MCP server '{name}' already registered")

        tool_names = self._registry.register_mcp_stdio(name=name, command=command, args=args, env=env)

        self._store.save({
            "name": name,
            "type": "stdio",
            "command": command,
            "args": args or [],
            "env": env or {},
        })

        return MCPServerInfo(name=name, type="stdio", tools=tool_names, command=command, args=args or [])

    def register_http(
        self,
        name: str,
        url: str,
        timeout: int = 30,
    ) -> MCPServerInfo:
        """注册 HTTP MCP Server，并持久化配置。"""
        if self._store.get(name) is not None:
            raise AppError("MCP_ALREADY_EXISTS", f"MCP server '{name}' already registered")

        tool_names = self._registry.register_mcp_http(name=name, url=url, timeout=timeout)

        self._store.save({
            "name": name,
            "type": "http",
            "url": url,
            "timeout": timeout,
        })

        return MCPServerInfo(name=name, type="http", tools=tool_names, url=url, timeout=timeout)

    # ── 查询 ──────────────────────────────────────────────────────────────

    def list_all(self) -> list[MCPServerInfo]:
        """列出所有已持久化的 MCP Server（从存储读取，不依赖内存状态）。"""
        result = []
        for cfg in self._store.list_all():
            info = self._config_to_info(cfg)
            if info:
                result.append(info)
        return result

    def get(self, name: str) -> MCPServerInfo:
        """获取指定 MCP Server 信息。"""
        cfg = self._store.get(name)
        if cfg is None:
            raise AppError("MCP_NOT_FOUND", f"MCP server '{name}' not found")
        info = self._config_to_info(cfg)
        if info is None:
            raise AppError("MCP_INVALID_CONFIG", f"MCP server '{name}' has invalid config")
        return info

    # ── 删除 ──────────────────────────────────────────────────────────────

    def delete(self, name: str) -> None:
        """停止 MCP Server，移除其工具，删除持久化配置。"""
        if self._store.get(name) is None:
            raise AppError("MCP_NOT_FOUND", f"MCP server '{name}' not found")

        self._registry.shutdown_one(name)
        self._store.delete(name)

    # ── 启动恢复 ──────────────────────────────────────────────────────────

    def refresh(self, name: str) -> MCPServerInfo:
        """重新拉取 MCP Server 工具列表，同步变更到外部 DB。

        Returns:
            含最新工具列表的 MCPServerInfo
        """
        cfg = self._store.get(name)
        if cfg is None:
            raise AppError("MCP_NOT_FOUND", f"MCP server '{name}' not found")

        added, removed = self._registry.refresh_mcp(name)
        logger.info("MCPService.refresh '%s': +%d -%d", name, len(added), len(removed))

        info = self._config_to_info(cfg)
        if info is None:
            raise AppError("MCP_INVALID_CONFIG", f"MCP server '{name}' has invalid config")
        # 返回内存中最新工具列表
        info.tools = self._registry._provider_tool_names.get(name, [])
        return info

    def restore_all(self) -> None:
        """从持久化配置恢复所有 MCP Server（应用启动时调用）。

        单个 Server 启动失败只记 warning，不阻断整体启动。
        恢复成功后将最新工具列表 upsert 到外部工具存储（保持 DB 与内存一致）。
        """
        configs = self._store.list_all()
        if not configs:
            return

        logger.info("MCPService.restore_all: restoring %d MCP server(s)", len(configs))
        for cfg in configs:
            name = cfg.get("name", "<unknown>")
            try:
                self._restore_one(cfg)
                logger.info("MCPService.restore_all: restored '%s'", name)
            except Exception:
                logger.warning("MCPService.restore_all: failed to restore '%s', skipping", name, exc_info=True)

    # ── 内部工具 ──────────────────────────────────────────────────────────

    def _restore_one(self, cfg: dict) -> None:
        """按配置类型恢复单个 MCP Server（跳过已在内存中的）。"""
        name = cfg["name"]
        server_type = cfg.get("type")

        # 若已在 provider_tool_names 中说明本次进程内已注册，跳过
        if name in self._registry._provider_tool_names:
            return

        if server_type == "stdio":
            self._registry.register_mcp_stdio(
                name=name,
                command=cfg["command"],
                args=cfg.get("args") or [],
                env=cfg.get("env") or None,
            )
        elif server_type == "http":
            self._registry.register_mcp_http(
                name=name,
                url=cfg["url"],
                timeout=cfg.get("timeout", 30),
            )
        else:
            raise AppError("MCP_INVALID_CONFIG", f"Unknown MCP server type: '{server_type}'")

    @staticmethod
    def _config_to_info(cfg: dict) -> MCPServerInfo | None:
        server_type = cfg.get("type")
        name = cfg.get("name", "")
        if server_type == "stdio":
            return MCPServerInfo(
                name=name,
                type="stdio",
                tools=[],
                command=cfg.get("command"),
                args=cfg.get("args") or [],
            )
        if server_type == "http":
            return MCPServerInfo(
                name=name,
                type="http",
                tools=[],
                url=cfg.get("url"),
                timeout=cfg.get("timeout", 30),
            )
        return None


# ── 全局单例 ──────────────────────────────────────────────────────────────

_mcp_service: MCPService | None = None


def get_mcp_service() -> MCPService:
    global _mcp_service
    if _mcp_service is None:
        from app.tools.registry import get_tool_registry
        _mcp_service = MCPService(
            tool_registry=get_tool_registry(),
            store=MCPConfigStore(),
        )
    return _mcp_service
