"""工具注册表（全局单例）。

职责：
- 存储并索引所有 ToolDefinition（跨 Provider）
- to_llm_tools()  — 生成 LLMTool[]，供 LLM 感知可用工具
- get() / is_registered() — 供 ToolGateway 和 PolicyEngine 使用
- register_provider() — 批量接入 ToolProvider（内置或外部 MCP）
"""

from __future__ import annotations

import logging

from app.common.errors import AppError
from app.llm.llm_base import LLMTool
from app.tools.definition import ToolDefinition
from app.tools.provider import ToolProvider

logger = logging.getLogger(__name__)


class ToolRegistry:
    """工具注册表（内存单例）。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool_def: ToolDefinition) -> None:
        """注册单个工具定义（重复注册会覆盖）。"""
        self._tools[tool_def.name] = tool_def
        logger.debug("ToolRegistry: registered tool '%s'", tool_def.name)

    def register_provider(self, provider: ToolProvider) -> None:
        """批量注册 Provider 提供的所有工具定义。

        这是接入外部 MCP Server 的扩展点：
        将来只需传入 MCPServerProvider 实例即可接入远端工具，
        无需修改 ToolRegistry 或 ToolGateway 的任何代码。
        """
        for tool_def in provider.list_definitions():
            self.register(tool_def)

    def get(self, name: str) -> ToolDefinition:
        """获取工具定义；不存在则抛 AppError。"""
        if name not in self._tools:
            raise AppError("TOOL_NOT_FOUND", f"Tool '{name}' is not registered")
        return self._tools[name]

    def is_registered(self, name: str) -> bool:
        return name in self._tools

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def to_llm_tools(self, names: list[str]) -> list[LLMTool]:
        """将工具名称列表转为 LLMTool[]（名称不存在的静默跳过）。"""
        result = []
        for name in names:
            if name in self._tools:
                result.append(self._tools[name].to_llm_tool())
            else:
                logger.warning("ToolRegistry.to_llm_tools: unknown tool '%s', skipped", name)
        return result


# ── 全局单例 ──────────────────────────────────────────────────────────────

_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """获取全局工具注册表（首次调用时注册所有内置工具）。"""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        from app.tools.builtins import get_builtin_provider
        _registry.register_provider(get_builtin_provider())
    return _registry
