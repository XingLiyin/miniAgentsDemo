"""MCP Provider 公共基类。

使用 mcp SDK (mcp.client.session / stdio / streamable_http)，

子类实现 get_mcp_client() 返回一个 async context manager，
yield (read_stream, write_stream) 元组。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any

from mcp import types
from mcp.client.session import ClientSession

from app.common.errors import AppError
from app.llm.types import InputSchema
from app.tools.types import CallContext, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)


class _MCPProviderBase(ABC):
    """MCP Provider 同步封装基类。

    子类在 get_mcp_client() 中返回传输层 async context manager；
    start() 启动后台事件循环线程，完成连接握手并加载工具列表。
    """

    def __init__(self, thread_name: str, request_timeout: int = 30, connect_timeout: int = 5) -> None:
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._tools: list[types.Tool] = []
        self._thread_name = thread_name
        self._request_timeout = request_timeout
        self._connect_timeout = connect_timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._initialized = False
        self._generation: int = 0
        # Set by ToolRegistry to route reconnects through _connect_locks; fallback uses _reconnect_lock.
        self._reconnect_fn: Callable[[int], None] | None = None
        self._reconnect_lock = threading.Lock()

    # ── 子类接口 ──────────────────────────────────────────────────────────

    @abstractmethod
    def get_mcp_client(self) -> Any:
        """返回 async context manager，yield (read_stream, write_stream)。"""

    @abstractmethod
    def start(self) -> None:
        """启动后台事件循环，连接 MCP Server 并加载工具列表。"""

    # ── ToolProvider Protocol ──────────────────────────────────────────────

    def list_definitions(self) -> list[ToolDefinition]:
        if not self._initialized:
            raise AppError(
                "MCP_NOT_STARTED",
                f"{type(self).__name__}.start() has not been called",
            )
        return [self._map_tool(t) for t in self._tools]

    def reload_tools(self) -> list[ToolDefinition]:
        if not self._initialized:
            raise AppError(
                "MCP_NOT_STARTED",
                f"{type(self).__name__}.start() has not been called",
            )
        self._run_sync(self._load_tools())
        return self.list_definitions()

    def call(self, tool_name: str, arguments: dict, ctx: CallContext | None = None) -> ToolResult:
        if not self._initialized:
            raise AppError(
                "MCP_NOT_STARTED",
                f"{type(self).__name__}.start() has not been called",
            )
        gen = self._generation
        meta = {"netcowork/sessionId": ctx.session_id} if ctx else None
        try:
            return self._do_call(tool_name, arguments, meta)
        except Exception as e:
            if not _is_session_terminated(e):
                raise
            logger.warning(
                "MCP session terminated while calling '%s', reconnecting...", tool_name
            )
            self._handle_session_terminated(gen)
            logger.info("MCP reconnected, retrying '%s'", tool_name)
            return self._do_call(tool_name, arguments, meta)

    def _handle_session_terminated(self, gen: int) -> None:
        """Reconnect after session termination; routes through registry lock when available."""
        if self._reconnect_fn is not None:
            self._reconnect_fn(gen)
        else:
            with self._reconnect_lock:
                if self._generation == gen and self._initialized:
                    self._reconnect()

    def _do_call(self, tool_name: str, arguments: dict, meta: dict | None) -> ToolResult:
        logger.debug("Calling MCP tool '%s' with arguments %s", tool_name, arguments)
        assert self._session is not None
        result = self._run_sync(self._session.call_tool(tool_name, arguments=arguments, meta=meta))
        return ToolResult(content=_parse_mcp_tool_result(result))

    def _reconnect(self) -> None:
        try:
            self.stop()
        except Exception:
            pass
        self.start()

    def _finish_start(self) -> None:
        """Called by subclasses at the end of start() to record a successful connection."""
        self._initialized = True
        self._generation += 1

    # ── 生命周期（共享） ───────────────────────────────────────────────────

    def stop(self) -> None:
        self._initialized = False
        if self._loop is not None:
            try:
                self._run_sync(self._close())
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=5)
            self._loop = None
            self._thread = None

    # ── 内部实现（共享） ───────────────────────────────────────────────────

    def _start_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        _loop = self._loop

        def _exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
            exc = context.get("exception")
            # anyio cancel scope cross-task error caused by streamable_http_client
            # teardown racing with connection failure — harmless, suppress the log noise.
            if isinstance(exc, RuntimeError) and "cancel scope" in str(exc):
                return
            loop.default_exception_handler(context)

        _loop.set_exception_handler(_exception_handler)

        def _run() -> None:
            asyncio.set_event_loop(_loop)
            _loop.run_forever()

        self._thread = threading.Thread(
            target=_run, daemon=True, name=self._thread_name
        )
        self._thread.start()

    def _run_sync(self, coro: Any, timeout: int | None = None) -> Any:
        if self._loop is None:
            raise AppError("MCP_NOT_STARTED", "Event loop not initialized")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        t = timeout if timeout is not None else (self._request_timeout or 30)
        try:
            return future.result(timeout=t)
        except concurrent.futures.CancelledError as exc:
            raise AppError(
                "MCP_CONNECT_CANCELLED",
                "MCP connection was cancelled; check server availability",
            ) from exc
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise AppError(
                "MCP_CONNECT_TIMEOUT",
                f"MCP connection timed out after {t} s",
            ) from exc

    def _start_connect(self) -> None:
        """用 connect_timeout 完成握手，与工具调用的 request_timeout 分开。"""
        self._run_sync(self._connect(), timeout=self._connect_timeout)

    async def _connect(self) -> None:
        """连接 MCP Server，建立 session，加载工具列表。"""
        self._exit_stack = AsyncExitStack()
        transport = await self._exit_stack.enter_async_context(self.get_mcp_client())
        timeout = timedelta(seconds=self._request_timeout) if self._request_timeout else None
        session = await self._exit_stack.enter_async_context(
            ClientSession(
                read_stream=transport[0],
                write_stream=transport[1],
                read_timeout_seconds=timeout,
            )
        )
        await session.initialize()
        self._session = session
        await self._load_tools()

    async def _load_tools(self) -> None:
        assert self._session is not None
        tools: list[types.Tool] = []
        params: types.PaginatedRequestParams | None = None
        while True:
            result = await self._session.list_tools(params=params)
            tools.extend(result.tools)
            if not result.nextCursor:
                break
            params = types.PaginatedRequestParams(cursor=result.nextCursor)
        self._tools = tools

    async def _close(self) -> None:
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            except Exception:
                pass
        self._session = None
        self._exit_stack = None
        self._tools = []

    def _map_tool(self, tool: types.Tool) -> ToolDefinition:
        schema = dict(tool.inputSchema or {})
        if schema.get("type") == "object" and "properties" not in schema:
            schema["properties"] = {}
        input_schema = InputSchema(
            type=schema.get("type", "object"),
            properties=schema.get("properties", {}),
            require=schema.get("required", []),
        )
        provider_ref = self
        tool_name = tool.name

        def handler(arguments: dict, ctx: CallContext | None = None) -> ToolResult:
            return provider_ref.call(tool_name, arguments, ctx)

        return ToolDefinition(
            name=tool.name,
            description=tool.description or "",
            input_schema=input_schema,
            handler=handler,
        )


def _parse_mcp_tool_result(result: types.CallToolResult) -> str:
    texts = [
        item.text
        for item in getattr(result, "content", [])
        if getattr(item, "text", None)
    ]
    if texts:
        return "\n".join(texts)
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        import json as _json
        return _json.dumps(structured, ensure_ascii=False)
    return ""


def _is_session_terminated(exc: Exception) -> bool:
    msg = str(exc)
    return "Session terminated" in msg or "session terminated" in msg
