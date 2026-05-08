"""Tests for MCP providers (app/tools/mcp_provider.py, mcp_http_provider.py).

mcp 网络调用全部 mock，不需要真实 MCP Server。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.common.errors import AppError
from app.tools.types import ToolDefinition, ToolResult
from app.tools.mcp_base import _MCPProviderBase


# ── Shared helper ─────────────────────────────────────────────────────────────

def _make_mock_mcp_tool(
    name: str = "list_dir",
    description: str = "List directory",
    properties: dict | None = None,
    required: list | None = None,
) -> MagicMock:
    """Build a minimal mcp.types.Tool mock."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.inputSchema = {
        "type": "object",
        "properties": properties or {"path": {"type": "string", "description": "Directory path"}},
        "required": required or ["path"],
    }
    return tool


def _make_fake_connect(provider: _MCPProviderBase, tools: list):
    """Return a no-op async _connect that seeds provider._tools and _session."""
    async def fake_connect():
        provider._tools = tools
        provider._session = MagicMock()
    return fake_connect


def _make_fake_close(provider: _MCPProviderBase):
    async def fake_close():
        provider._session = None
        provider._tools = []
    return fake_close


# ── MCPStdioProvider ──────────────────────────────────────────────────────────

class TestMCPStdioProvider:
    @pytest.fixture
    def provider(self):
        from app.tools.mcp_provider import MCPStdioProvider

        p = MCPStdioProvider(name="fs", command="npx", args=["-y", "server"])
        mock_tool = _make_mock_mcp_tool()
        p._connect = _make_fake_connect(p, [mock_tool])
        p._close = _make_fake_close(p)
        return p

    def test_not_started_raises_on_list_definitions(self, provider):
        with pytest.raises(AppError) as exc_info:
            provider.list_definitions()
        assert exc_info.value.code == "MCP_NOT_STARTED"

    def test_not_started_raises_on_call(self, provider):
        with pytest.raises(AppError) as exc_info:
            provider.call("list_dir", {"path": "."})
        assert exc_info.value.code == "MCP_NOT_STARTED"

    def test_start_sets_initialized(self, provider):
        provider.start()
        try:
            assert provider._initialized
        finally:
            provider.stop()

    def test_list_definitions_after_start(self, provider):
        provider.start()
        try:
            defs = provider.list_definitions()
            assert len(defs) == 1
            td = defs[0]
            assert isinstance(td, ToolDefinition)
            assert td.name == "list_dir"
            assert td.description == "List directory"
            assert "path" in td.input_schema.properties
            assert "path" in td.input_schema.require
        finally:
            provider.stop()

    def test_call_tool_returns_tool_result(self, provider):
        provider.start()
        try:
            provider._do_call = lambda name, args, meta: ToolResult(content="file1.txt\nfile2.txt")
            result = provider.call("list_dir", {"path": "."})
            assert isinstance(result, ToolResult)
            assert "file1.txt" in result.content
        finally:
            provider.stop()

    def test_stop_clears_state(self, provider):
        provider.start()
        provider.stop()
        assert not provider._initialized
        assert provider._loop is None
        assert provider._thread is None

    def test_is_tool_provider_protocol(self, provider):
        from app.tools.provider import ToolProvider
        assert isinstance(provider, ToolProvider)


# ── MCPStreamableHTTPProvider ─────────────────────────────────────────────────

class TestMCPStreamableHTTPProvider:
    @pytest.fixture
    def provider(self):
        from app.tools.mcp_http_provider import MCPStreamableHTTPProvider

        p = MCPStreamableHTTPProvider(name="web", url="http://mcp-server/mcp")
        mock_tool = _make_mock_mcp_tool(name="fetch", description="Fetch URL")
        p._connect = _make_fake_connect(p, [mock_tool])
        p._close = _make_fake_close(p)
        return p

    def test_not_started_raises_on_list_definitions(self, provider):
        with pytest.raises(AppError) as exc_info:
            provider.list_definitions()
        assert exc_info.value.code == "MCP_NOT_STARTED"

    def test_start_sets_initialized(self, provider):
        provider.start()
        try:
            assert provider._initialized
        finally:
            provider.stop()

    def test_list_definitions_after_start(self, provider):
        provider.start()
        try:
            defs = provider.list_definitions()
            assert len(defs) == 1
            assert defs[0].name == "fetch"
        finally:
            provider.stop()

    def test_call_tool_with_string_result(self, provider):
        provider.start()
        try:
            provider._do_call = lambda name, args, meta: ToolResult(content="fetched content")
            result = provider.call("fetch", {"url": "https://example.com"})
            assert result.content == "fetched content"
            assert not result.is_error
        finally:
            provider.stop()

    def test_stop_clears_state(self, provider):
        provider.start()
        provider.stop()
        assert not provider._initialized
        assert provider._loop is None

    def test_is_tool_provider_protocol(self, provider):
        from app.tools.provider import ToolProvider
        assert isinstance(provider, ToolProvider)


# ── _MCPProviderBase shared behavior ──────────────────────────────────────────

class TestMCPProviderBaseShared:
    """Verify that the two providers share base class methods (not copies)."""

    def test_stop_is_inherited_from_base(self):
        from app.tools.mcp_provider import MCPStdioProvider
        from app.tools.mcp_http_provider import MCPStreamableHTTPProvider

        assert MCPStdioProvider.stop is _MCPProviderBase.stop
        assert MCPStreamableHTTPProvider.stop is _MCPProviderBase.stop

    def test_run_sync_is_inherited_from_base(self):
        from app.tools.mcp_provider import MCPStdioProvider
        from app.tools.mcp_http_provider import MCPStreamableHTTPProvider

        assert MCPStdioProvider._run_sync is _MCPProviderBase._run_sync
        assert MCPStreamableHTTPProvider._run_sync is _MCPProviderBase._run_sync

    def test_map_function_tool_is_inherited_from_base(self):
        from app.tools.mcp_provider import MCPStdioProvider
        from app.tools.mcp_http_provider import MCPStreamableHTTPProvider

        assert MCPStdioProvider._map_function_tool is _MCPProviderBase._map_function_tool
        assert MCPStreamableHTTPProvider._map_function_tool is _MCPProviderBase._map_function_tool

    def test_stdio_own_methods_only_start_and_client(self):
        from app.tools.mcp_provider import MCPStdioProvider
        own = {k for k, v in MCPStdioProvider.__dict__.items()
               if callable(v) and not k.startswith("__")}
        assert own == {"start", "get_mcp_client"}

    def test_http_own_methods_only_start_and_client(self):
        from app.tools.mcp_http_provider import MCPStreamableHTTPProvider
        own = {k for k, v in MCPStreamableHTTPProvider.__dict__.items()
               if callable(v) and not k.startswith("__")}
        assert own == {"start", "get_mcp_client"}
