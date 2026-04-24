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
import concurrent.futures
import logging
import threading
from abc import ABC, abstractmethod
from typing import Any

from app.common.errors import AppError
from app.tools.definition import CallContext, ToolDefinition, ToolResult
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
        self._reconnect_lock = threading.Lock()

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

    def call(self, tool_name: str, arguments: dict, ctx: CallContext | None = None) -> ToolResult:
        """调用远端工具，session 断开时自动重连一次后重试。"""
        if not self._initialized:
            raise AppError(
                "MCP_NOT_STARTED",
                f"{type(self).__name__}.start() has not been called",
            )
        meta = {"netcowork/sessionId": ctx.session_id} if ctx else None
        try:
            return self._do_call(tool_name, arguments, meta)
        except Exception as e:
            if not _is_session_terminated(e):
                raise
            logger.warning(
                "MCP session terminated while calling '%s', reconnecting...", tool_name
            )
            with self._reconnect_lock:
                if self._initialized:  # 避免并发时重复重连
                    self._reconnect()
            logger.info("MCP reconnected, retrying '%s'", tool_name)
            return self._do_call(tool_name, arguments, meta)

    def _do_call(self, tool_name: str, arguments: dict, meta: dict | None) -> ToolResult:
        logger.debug("Calling MCP tool '%s' with arguments %s", tool_name, arguments)
        result = self._run_sync(self._af_tool.call_tool(tool_name, _injected_meta=meta, **arguments))
        text = result if isinstance(result, str) else _content_to_text(result)
        return ToolResult(content=text)

    def _reconnect(self) -> None:
        """stop() + start()，事件循环线程完整重建。"""
        try:
            self.stop()
        except Exception:
            pass
        self.start()

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
        _loop = self._loop

        def _run() -> None:
            # 必须绑定到当前线程，否则 agent-framework 内部的
            # asyncio.get_event_loop() 在 Python 3.10+ 会返回错误的 loop，
            # 导致内部 task 被取消并抛出 CancelledError。
            asyncio.set_event_loop(_loop)
            _loop.run_forever()

        self._thread = threading.Thread(
            target=_run, daemon=True, name=self._thread_name
        )
        self._thread.start()

    def _run_sync(self, coro: Any) -> Any:
        """在后台事件循环中同步运行协程（阻塞直到完成）。"""
        if self._loop is None:
            raise AppError("MCP_NOT_STARTED", "Event loop not initialized")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=30)
        except concurrent.futures.CancelledError as exc:
            raise AppError(
                "MCP_CONNECT_CANCELLED",
                "MCP connection was cancelled; check server availability",
            ) from exc
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise AppError(
                "MCP_CONNECT_TIMEOUT",
                "MCP connection timed out after 30 s",
            ) from exc

    def _map_function_tool(self, ft: Any) -> ToolDefinition:
        """将 AF FunctionTool 映射为 miniAgents ToolDefinition。"""
        input_schema = _extract_input_schema(ft)
        provider_ref = self
        tool_name = ft.name

        def handler(arguments: dict, ctx: CallContext | None = None) -> ToolResult:
            return provider_ref.call(tool_name, arguments, ctx)

        return ToolDefinition(
            name=ft.name,
            description=ft.description or "",
            input_schema=input_schema,
            handler=handler,
        )


class _MetaInjectingMixin:
    """将调用方传入的 meta 合并到 MCP session.call_tool 的 meta 参数中。

    与 AF 的 _inject_otel_into_mcp_meta 模式对称：通过 _injected_meta 参数
    在 coroutine 创建时绑定 meta，避免共享实例属性带来的多线程竞态。
    """

    async def call_tool(self, tool_name: str, *, _injected_meta: dict | None = None, **kwargs: Any) -> Any:
        extra = _injected_meta or {}
        if not extra:
            return await super().call_tool(tool_name, **kwargs)  # type: ignore[misc]

        original = self.session.call_tool  # type: ignore[attr-defined]

        async def _with_extra_meta(name: str, *, arguments: Any = None, meta: Any = None) -> Any:
            merged = dict(meta or {})
            merged.update(extra)
            return await original(name, arguments=arguments, meta=merged)

        self.session.call_tool = _with_extra_meta  # type: ignore[method-assign]
        try:
            return await super().call_tool(tool_name, **kwargs)  # type: ignore[misc]
        finally:
            self.session.call_tool = original


def _content_to_text(content: Any) -> str:
    """将 AF Content 列表转为纯文本。"""
    if isinstance(content, list):
        return "\n".join(getattr(c, "text", "") or "" for c in content)
    return str(content)


def _is_session_terminated(exc: Exception) -> bool:
    """判断异常是否为 MCP session 断开（可重连）。"""
    msg = str(exc)
    return "Session terminated" in msg or "session terminated" in msg


def _parse_mcp_tool_result(result: Any) -> str:
    """MCP CallToolResult → str。

    优先取 content[].text；若全为空则回退到 structuredContent（MCP 2025-06-18）。
    直接返回 str，跳过 AF 默认的 list[Content] 转换，避免 Content 类型不可序列化等问题。
    """
    import json as _json

    # 1. 取 content 中的 text
    texts = [
        item.text
        for item in getattr(result, "content", [])
        if getattr(item, "text", None)
    ]
    if texts:
        return "\n".join(texts)

    # 2. 回退到 structuredContent
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return _json.dumps(structured, ensure_ascii=False)

    return ""
