"""Tests for the shared MCP provider base (app/tools/mcp_base.py).

The two concrete providers only supply `start()` + `get_mcp_client()`; everything
else — the background event loop, the drain-guarded call counter, tool mapping and
session-termination detection — lives here. Transports are never opened.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from app.common.errors import AppError
from app.tools.mcp_base import (
    _MCPProviderBase, _is_session_terminated, _parse_mcp_tool_result,
)
from app.tools.types import CallContext, ToolResult


def _mock_tool(name: str = "list_dir", description: str = "List directory",
               input_schema: dict | None = None) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.inputSchema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    } if input_schema is None else input_schema
    return tool


class _StubProvider(_MCPProviderBase):
    """Concrete subclass whose transport is never actually opened."""

    def __init__(self, **kw):
        super().__init__(thread_name="mcp-test-loop", **kw)

    def get_mcp_client(self):
        return MagicMock()

    def start(self) -> None:
        self._start_loop()
        self._finish_start()


@pytest.fixture
def started():
    p = _StubProvider()
    p.start()
    yield p
    p.stop()


# ── _map_tool ─────────────────────────────────────────────────────────────────

class TestMapTool:
    def test_schema_fields_mapped(self):
        td = _StubProvider()._map_tool(_mock_tool())
        assert td.name == "list_dir"
        assert td.description == "List directory"
        assert td.input_schema.type == "object"
        assert td.input_schema.properties["path"]["type"] == "string"
        assert td.input_schema.require == ["path"]

    def test_object_schema_without_properties_gets_empty_dict(self):
        td = _StubProvider()._map_tool(_mock_tool(input_schema={"type": "object"}))
        assert td.input_schema.properties == {}

    def test_missing_input_schema_defaults_to_object(self):
        td = _StubProvider()._map_tool(_mock_tool(input_schema=None if False else {}))
        assert td.input_schema.type == "object" and td.input_schema.require == []

    def test_null_input_schema(self):
        tool = _mock_tool()
        tool.inputSchema = None
        td = _StubProvider()._map_tool(tool)
        assert td.input_schema.properties == {}

    def test_none_description_becomes_empty_string(self):
        tool = _mock_tool(description=None)
        assert _StubProvider()._map_tool(tool).description == ""

    def test_non_object_schema_type_preserved(self):
        td = _StubProvider()._map_tool(_mock_tool(input_schema={"type": "array"}))
        assert td.input_schema.type == "array"

    def test_handler_delegates_to_provider_call(self):
        p = _StubProvider()
        seen = {}

        def fake_call(name, args, ctx):
            seen.update(name=name, args=args, ctx=ctx)
            return ToolResult(content="ok")

        p.call = fake_call
        td = p._map_tool(_mock_tool())
        assert td.handler({"path": "."}, "CTX").content == "ok"
        assert seen == {"name": "list_dir", "args": {"path": "."}, "ctx": "CTX"}

    def test_handler_defaults_ctx_to_none(self):
        p = _StubProvider()
        seen = {}
        p.call = lambda n, a, c: seen.update(ctx=c) or ToolResult()
        p._map_tool(_mock_tool()).handler({})
        assert seen["ctx"] is None


# ── result parsing / error sniffing ───────────────────────────────────────────

class TestParseMCPToolResult:
    def test_joins_text_items(self):
        result = MagicMock()
        result.content = [MagicMock(text="a"), MagicMock(text="b")]
        assert _parse_mcp_tool_result(result) == "a\nb"

    def test_skips_items_without_text(self):
        blank = MagicMock()
        blank.text = None
        result = MagicMock()
        result.content = [blank, MagicMock(text="only")]
        assert _parse_mcp_tool_result(result) == "only"

    def test_falls_back_to_structured_content(self):
        result = MagicMock()
        result.content = []
        result.structuredContent = {"k": "值"}
        out = _parse_mcp_tool_result(result)
        assert '"k"' in out and "值" in out  # ensure_ascii=False

    def test_empty_when_nothing_present(self):
        result = MagicMock()
        result.content = []
        result.structuredContent = None
        assert _parse_mcp_tool_result(result) == ""

    def test_missing_content_attribute(self):
        class Bare:
            structuredContent = None

        assert _parse_mcp_tool_result(Bare()) == ""


class TestIsSessionTerminated:
    def test_anyio_closed_resource_error(self):
        from anyio import ClosedResourceError
        assert _is_session_terminated(ClosedResourceError())

    @pytest.mark.parametrize("msg,expected", [
        ("Session terminated", True),
        ("mcp session terminated abruptly", True),
        ("connection refused", False),
        ("", False),
    ])
    def test_message_sniffing(self, msg, expected):
        assert _is_session_terminated(RuntimeError(msg)) is expected


# ── not-started guards ────────────────────────────────────────────────────────

class TestNotStartedGuards:
    def test_list_definitions(self):
        with pytest.raises(AppError) as e:
            _StubProvider().list_definitions()
        assert e.value.code == "MCP_NOT_STARTED"
        assert "_StubProvider" in e.value.message

    def test_reload_tools(self):
        with pytest.raises(AppError) as e:
            _StubProvider().reload_tools()
        assert e.value.code == "MCP_NOT_STARTED"

    def test_call(self):
        with pytest.raises(AppError) as e:
            _StubProvider().call("t", {})
        assert e.value.code == "MCP_NOT_STARTED"


# ── reload_tools ──────────────────────────────────────────────────────────────

class TestReloadTools:
    def test_returns_fresh_definitions(self, started):
        async def fake_load():
            started._tools = [_mock_tool(name="added")]

        started._load_tools = fake_load
        assert [d.name for d in started.reload_tools()] == ["added"]

    def test_terminated_session_marks_disconnected_and_reraises(self, started):
        async def boom():
            raise RuntimeError("Session terminated")

        started._load_tools = boom
        with pytest.raises(RuntimeError, match="Session terminated"):
            started.reload_tools()
        assert not started._initialized

    def test_other_error_keeps_session_live(self, started):
        async def boom():
            raise RuntimeError("transient")

        started._load_tools = boom
        with pytest.raises(RuntimeError, match="transient"):
            started.reload_tools()
        assert started._initialized


# ── call() ────────────────────────────────────────────────────────────────────

class TestCallPaths:
    def test_session_id_passed_as_meta(self, started):
        seen = {}
        started._do_call = lambda n, a, meta: seen.update(meta=meta) or ToolResult()
        started.call("t", {}, CallContext(session_id="sess-9"))
        assert seen["meta"] == {"netcowork/sessionId": "sess-9"}

    def test_no_ctx_sends_no_meta(self, started):
        seen = {}
        started._do_call = lambda n, a, meta: seen.update(meta=meta) or ToolResult()
        started.call("t", {})
        assert seen["meta"] is None

    def test_active_call_counter_returns_to_zero(self, started):
        started._do_call = lambda n, a, m: ToolResult()
        started.call("t", {})
        assert started._active_calls == 0

    def test_counter_released_on_failure(self, started):
        def boom(n, a, m):
            raise RuntimeError("tool blew up")

        started._do_call = boom
        with pytest.raises(RuntimeError):
            started.call("t", {})
        assert started._active_calls == 0
        assert started._initialized

    def test_terminated_session_flips_initialized(self, started):
        def boom(n, a, m):
            raise RuntimeError("Session terminated")

        started._do_call = boom
        with pytest.raises(RuntimeError):
            started.call("t", {})
        assert not started._initialized

    def test_do_call_wraps_session_result(self, started):
        call_result = MagicMock()
        call_result.content = [MagicMock(text="payload")]
        session = MagicMock()

        async def call_tool(name, arguments=None, meta=None):
            return call_result

        session.call_tool = call_tool
        started._session = session
        assert started._do_call("t", {"a": 1}, None).content == "payload"


# ── _run_sync ─────────────────────────────────────────────────────────────────

class TestRunSync:
    def test_without_loop_raises(self):
        async def noop():
            return 1

        coro = noop()
        with pytest.raises(AppError) as e:
            _StubProvider()._run_sync(coro)
        assert e.value.code == "MCP_NOT_STARTED"
        coro.close()

    def test_result_returned(self, started):
        async def answer():
            return 42

        assert started._run_sync(answer()) == 42

    def test_timeout_raises_connect_timeout(self, started):
        async def slow():
            await asyncio.sleep(5)

        with pytest.raises(AppError) as e:
            started._run_sync(slow(), timeout=1)
        assert e.value.code == "MCP_CONNECT_TIMEOUT"

    def test_cancelled_raises_connect_cancelled(self, started):
        async def cancelled():
            raise asyncio.CancelledError()

        with pytest.raises(AppError) as e:
            started._run_sync(cancelled())
        assert e.value.code == "MCP_CONNECT_CANCELLED"

    def test_zero_request_timeout_falls_back_to_30(self):
        p = _StubProvider(request_timeout=0)
        p.start()
        try:
            async def answer():
                return "v"

            assert p._run_sync(answer()) == "v"
        finally:
            p.stop()

    def test_start_connect_uses_connect_timeout(self):
        p = _StubProvider(connect_timeout=3)
        p.start()
        try:
            seen = {}

            def capture(coro, timeout=None):
                coro.close()          # we never run it; avoid an un-awaited warning
                seen["timeout"] = timeout

            p._run_sync = capture
            p._start_connect()
            assert seen["timeout"] == 3
        finally:
            p.stop()


# ── loop lifecycle ────────────────────────────────────────────────────────────

class TestLoopLifecycle:
    def test_stop_without_start_is_noop(self):
        p = _StubProvider()
        p.stop()
        assert p._loop is None

    def test_stop_is_idempotent(self):
        p = _StubProvider()
        p.start()
        p.stop()
        p.stop()
        assert p._loop is None and p._thread is None

    def test_stop_swallows_close_failure(self):
        p = _StubProvider()
        p.start()

        async def boom():
            raise RuntimeError("close failed")

        p._close = boom
        p.stop()
        assert p._loop is None

    def test_cancel_scope_errors_suppressed(self, started):
        handler = started._loop.get_exception_handler()
        default = MagicMock()
        started._loop.default_exception_handler = default
        handler(started._loop, {"exception": RuntimeError("cancel scope in wrong task")})
        assert not default.called

    def test_real_errors_reach_default_handler(self, started):
        handler = started._loop.get_exception_handler()
        default = MagicMock()
        started._loop.default_exception_handler = default
        handler(started._loop, {"exception": ValueError("something real")})
        assert default.called

    def test_non_exception_context_reaches_default_handler(self, started):
        handler = started._loop.get_exception_handler()
        default = MagicMock()
        started._loop.default_exception_handler = default
        handler(started._loop, {"message": "no exception key"})
        assert default.called

    def test_finish_start_sets_initialized(self):
        p = _StubProvider()
        assert not p._initialized
        p._finish_start()
        assert p._initialized


class TestClose:
    def test_clears_session_and_tools(self, started):
        stack = MagicMock()

        async def aclose():
            return None

        stack.aclose = aclose
        started._exit_stack = stack
        started._session = MagicMock()
        started._tools = [_mock_tool()]
        started._run_sync(started._close())
        assert started._session is None
        assert started._tools == []
        assert started._exit_stack is None

    def test_swallows_aclose_failure(self, started):
        stack = MagicMock()

        async def aclose():
            raise RuntimeError("already closed")

        stack.aclose = aclose
        started._exit_stack = stack
        started._run_sync(started._close())
        assert started._exit_stack is None

    def test_with_no_exit_stack(self, started):
        started._exit_stack = None
        started._run_sync(started._close())
        assert started._session is None


class TestLoadToolsPagination:
    def test_follows_next_cursor(self, started):
        page1 = MagicMock(tools=[_mock_tool(name="a")], nextCursor="c2")
        page2 = MagicMock(tools=[_mock_tool(name="b")], nextCursor=None)
        pages = [page1, page2]
        seen_params = []

        async def list_tools(params=None):
            seen_params.append(params)
            return pages.pop(0)

        session = MagicMock()
        session.list_tools = list_tools
        started._session = session
        started._run_sync(started._load_tools())
        assert [t.name for t in started._tools] == ["a", "b"]
        assert seen_params[0] is None
        assert seen_params[1].cursor == "c2"

    def test_single_page(self, started):
        async def list_tools(params=None):
            return MagicMock(tools=[_mock_tool(name="only")], nextCursor=None)

        session = MagicMock()
        session.list_tools = list_tools
        started._session = session
        started._run_sync(started._load_tools())
        assert [t.name for t in started._tools] == ["only"]


class TestConnectHandshake:
    def test_opens_session_initializes_and_loads_tools(self, started):
        session = MagicMock()
        calls = []

        async def initialize():
            calls.append("init")

        session.initialize = initialize

        class FakeStack:
            async def enter_async_context(self, cm):
                # first call is the transport, second is the ClientSession
                return session if cm is session else ("read", "write")

            async def aclose(self):
                return None

        async def fake_load():
            calls.append("load")

        started._load_tools = fake_load
        with patch("app.tools.mcp_base.AsyncExitStack", return_value=FakeStack()), \
             patch("app.tools.mcp_base.ClientSession", return_value=session) as cs:
            started._run_sync(started._connect())

        assert calls == ["init", "load"]
        assert started._session is session
        assert cs.call_args.kwargs["read_stream"] == "read"
        assert cs.call_args.kwargs["write_stream"] == "write"
        assert cs.call_args.kwargs["read_timeout_seconds"].total_seconds() == 30

    def test_zero_request_timeout_means_no_read_timeout(self):
        p = _StubProvider(request_timeout=0)
        p.start()
        try:
            session = MagicMock()

            async def initialize():
                return None

            session.initialize = initialize

            class FakeStack:
                async def enter_async_context(self, cm):
                    return session if cm is session else ("r", "w")

                async def aclose(self):
                    return None

            async def fake_load():
                return None

            p._load_tools = fake_load
            with patch("app.tools.mcp_base.AsyncExitStack", return_value=FakeStack()), \
                 patch("app.tools.mcp_base.ClientSession", return_value=session) as cs:
                p._run_sync(p._connect())
            assert cs.call_args.kwargs["read_timeout_seconds"] is None
        finally:
            p.stop()
