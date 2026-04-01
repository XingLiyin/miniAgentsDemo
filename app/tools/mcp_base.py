"""MCP Provider 公共基类。

提取 MCPStdioProvider 和 MCPStreamableHTTPProvider 的共享逻辑：
- 后台事件循环线程管理（_loop / _thread）
- _run_sync()        — 将协程派发到后台 loop 并同步等待
- stop()             — 关闭连接、停止事件循环
- list_definitions() — 返回已加载的 ToolDefinition 列表
- call()             — 调用远端工具并返回 ToolResult
- _map_function_tool() — AF FunctionTool → miniAgents ToolDefinition

子类只需实现 start()，完成 provider 特有的连接与工具加载逻辑。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from abc import ABC, abstractmethod
from typing import Any

from app.common.errors import AppError
from app.tools.definition import ToolDefinition, ToolResult
from app.tools.tool_decorator import _extract_input_schema

logger = logging.getLogger(__name__)


class _MCPProviderBase(ABC):
    """MCP Provider 同步封装基类。

    子类在 start() 中调用 self._run_sync(...) 完成连接握手，
    并确保 self._af_tool.functions 在 start() 返回前已填充。
    """

    def __init__(self, af_tool: Any, thread_name: str) -> None:
        self._af_tool = af_tool
        self._thread_name = thread_name
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._initialized = False

    # ── 子类接口 ──────────────────────────────────────────────────────────

    @abstractmethod
    def start(self) -> None:
        """启动后台事件循环，连接 MCP Server 并加载工具列表。"""

    # ── ToolProvider Protocol ──────────────────────────────────────────────

    def list_definitions(self) -> list[ToolDefinition]:
        """返回已加载的工具定义列表。"""
        if not self._initialized:
            raise AppError(
                "MCP_NOT_STARTED",
                f"{type(self).__name__}.start() has not been called",
            )
        return [self._map_function_tool(ft) for ft in self._af_tool.functions]

    def reload_tools(self) -> list[ToolDefinition]:
        """重新从 MCP Server 拉取工具列表并返回最新定义。

        用于处理 MCP Server 工具列表在运行时发生变化的场景。
        """
        if not self._initialized:
            raise AppError(
                "MCP_NOT_STARTED",
                f"{type(self).__name__}.start() has not been called",
            )
        self._run_sync(self._af_tool.load_tools())
        return self.list_definitions()

    def call(self, tool_name: str, arguments: dict) -> ToolResult:
        """调用远端工具。"""
        if not self._initialized:
            raise AppError(
                "MCP_NOT_STARTED",
                f"{type(self).__name__}.start() has not been called",
            )
        result = self._run_sync(self._af_tool.call_tool(tool_name, **arguments))
        text = result if isinstance(result, str) else _content_to_text(result)
        return ToolResult(content=text)

    # ── 生命周期（共享） ───────────────────────────────────────────────────

    def stop(self) -> None:
        """关闭 MCP 连接并停止后台事件循环。"""
        self._initialized = False
        if self._loop is not None:
            try:
                self._run_sync(self._af_tool.close())
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=5)
            self._loop = None
            self._thread = None

    # ── 内部实现（共享） ───────────────────────────────────────────────────

    def _start_loop(self) -> None:
        """启动后台事件循环线程（子类在 start() 开头调用）。"""
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name=self._thread_name
        )
        self._thread.start()

    def _run_sync(self, coro: Any) -> Any:
        """在后台事件循环中同步运行协程（阻塞直到完成）。"""
        if self._loop is None:
            raise AppError("MCP_NOT_STARTED", "Event loop not initialized")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=30)

    def _map_function_tool(self, ft: Any) -> ToolDefinition:
        """将 AF FunctionTool 映射为 miniAgents ToolDefinition。"""
        input_schema = _extract_input_schema(ft)
        provider_ref = self
        tool_name = ft.name

        def handler(arguments: dict) -> ToolResult:
            return provider_ref.call(tool_name, arguments)

        return ToolDefinition(
            name=ft.name,
            description=ft.description or "",
            input_schema=input_schema,
            handler=handler,
        )


def _content_to_text(content: Any) -> str:
    """将 AF Content 列表转为纯文本。"""
    if isinstance(content, list):
        return "\n".join(getattr(c, "text", "") or "" for c in content)
    return str(content)
