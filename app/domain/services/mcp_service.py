"""MCP Server 管理服务。

职责：
- 注册 stdio / http MCP Server（注册到 ToolRegistry + 持久化配置）
- 删除 MCP Server（停止 Provider + 删除持久化）
- 列出所有已持久化的 MCP Server 配置
- restore_all()：应用启动时从持久化配置恢复所有 MCP Server
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.common.errors import AppError
from app.storage.file.mcp_config_store import MCPConfigStore
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class MCPToolInfo:
    name: str
    description: str


@dataclass
class MCPServerInfo:
    """MCP Server 的概要信息（用于 API 响应）。"""
    name: str
    type: str           # "stdio" | "http"
    status: str = "DISCONNECTED"   # "CONNECTED" | "DISCONNECTED"
    tools: list[MCPToolInfo] = field(default_factory=list)
    # stdio 字段
    command: str | None = None
    args: list[str] | None = None
    # http 字段
    url: str | None = None
    timeout: int | None = None
    # 通用
    connect_timeout: int = 5


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
        connect_timeout: int = 5,
    ) -> MCPServerInfo:
        """注册 stdio MCP Server，并持久化配置。"""
        if self._store.get(name) is not None:
            raise AppError("MCP_ALREADY_EXISTS", f"MCP server '{name}' already registered")

        self._registry.register_mcp_stdio(name=name, command=command, args=args, env=env, connect_timeout=connect_timeout)

        self._store.save({
            "name": name,
            "type": "stdio",
            "command": command,
            "args": args or [],
            "env": env or {},
            "connect_timeout": connect_timeout,
        })

        return MCPServerInfo(
            name=name, type="stdio",
            status=self._registry.get_server_status(name),
            tools=self._tool_infos(name),
            command=command, args=args or [],
            connect_timeout=connect_timeout,
        )

    def register_http(
        self,
        name: str,
        url: str,
        timeout: int = 30,
        connect_timeout: int = 5,
    ) -> MCPServerInfo:
        """注册 HTTP MCP Server，并持久化配置。"""
        if self._store.get(name) is not None:
            raise AppError("MCP_ALREADY_EXISTS", f"MCP server '{name}' already registered")

        self._registry.register_mcp_http(name=name, url=url, timeout=timeout, connect_timeout=connect_timeout)

        self._store.save({
            "name": name,
            "type": "http",
            "url": url,
            "timeout": timeout,
            "connect_timeout": connect_timeout,
        })

        return MCPServerInfo(
            name=name, type="http",
            status=self._registry.get_server_status(name),
            tools=self._tool_infos(name),
            url=url, timeout=timeout,
            connect_timeout=connect_timeout,
        )

    # ── 查询 ──────────────────────────────────────────────────────────────

    def list_all(self) -> list[MCPServerInfo]:
        """列出所有已持久化的 MCP Server，工具列表实时从 provider 读取。"""
        result = []
        for cfg in self._store.list_all():
            info = self._config_to_info(cfg)
            if info:
                info.status = self._registry.get_server_status(info.name)
                info.tools = self._tool_infos(info.name)
                result.append(info)
        return result

    def get(self, name: str) -> MCPServerInfo:
        """获取指定 MCP Server 信息，工具列表实时从 provider 读取。"""
        cfg = self._store.get(name)
        if cfg is None:
            raise AppError("MCP_NOT_FOUND", f"MCP server '{name}' not found")
        info = self._config_to_info(cfg)
        if info is None:
            raise AppError("MCP_INVALID_CONFIG", f"MCP server '{name}' has invalid config")
        info.status = self._registry.get_server_status(name)
        info.tools = self._tool_infos(name)
        return info

    # ── 删除 ──────────────────────────────────────────────────────────────

    def delete(self, name: str) -> None:
        """停止 MCP Server 并删除持久化配置。"""
        if self._store.get(name) is None:
            raise AppError("MCP_NOT_FOUND", f"MCP server '{name}' not found")
        self._registry.shutdown_one(name)
        self._store.delete(name)

    # ── Refresh ───────────────────────────────────────────────────────────

    def refresh(self, name: str) -> MCPServerInfo:
        """重新拉取 MCP Server 工具列表（刷新内存快照）。"""
        cfg = self._store.get(name)
        if cfg is None:
            raise AppError("MCP_NOT_FOUND", f"MCP server '{name}' not found")

        self._registry.refresh_mcp(name)

        info = self._config_to_info(cfg)
        if info is None:
            raise AppError("MCP_INVALID_CONFIG", f"MCP server '{name}' has invalid config")
        info.status = self._registry.get_server_status(name)
        info.tools = self._tool_infos(name)
        return info

    # ── 启动恢复 ──────────────────────────────────────────────────────────

    def restore_all(self) -> None:
        """从持久化配置恢复所有 MCP Server（应用启动时调用）。"""
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
                logger.warning(
                    "MCPService.restore_all: failed to restore '%s', skipping", name, exc_info=True
                )

    # ── 内部工具 ──────────────────────────────────────────────────────────

    def _restore_one(self, cfg: dict) -> None:
        name = cfg["name"]
        server_type = cfg.get("type")

        if name in self._registry._mcp_providers:
            return  # 已在本进程内注册，跳过

        if server_type == "stdio":
            self._registry.register_mcp_stdio(
                name=name,
                command=cfg["command"],
                args=cfg.get("args") or [],
                env=cfg.get("env") or None,
                connect_timeout=cfg.get("connect_timeout", 5),
            )
        elif server_type == "http":
            self._registry.register_mcp_http(
                name=name,
                url=cfg["url"],
                timeout=cfg.get("timeout", 30),
                connect_timeout=cfg.get("connect_timeout", 5),
            )
        else:
            raise AppError("MCP_INVALID_CONFIG", f"Unknown MCP server type: '{server_type}'")

    def _tool_infos(self, name: str) -> list[MCPToolInfo]:
        return [
            MCPToolInfo(name=td.name, description=td.description)
            for td in self._registry.get_server_tool_definitions(name)
        ]

    @staticmethod
    def _config_to_info(cfg: dict) -> MCPServerInfo | None:
        server_type = cfg.get("type")
        name = cfg.get("name", "")
        if server_type == "stdio":
            return MCPServerInfo(
                name=name, type="stdio",
                command=cfg.get("command"),
                args=cfg.get("args") or [],
                connect_timeout=cfg.get("connect_timeout", 5),
            )
        if server_type == "http":
            return MCPServerInfo(
                name=name, type="http",
                url=cfg.get("url"),
                timeout=cfg.get("timeout", 30),
                connect_timeout=cfg.get("connect_timeout", 5),
            )
        return None

