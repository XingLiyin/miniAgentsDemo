"""Tests for MCP providers (app/tools/mcp_provider.py, mcp_http_provider.py).

AF network calls are mocked out entirely — no real MCP server needed.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.common.errors import AppError
from app.tools.definition import ToolDefinition, ToolResult
from app.tools.mcp_base import _MCPProviderBase


# ── Shared helper ─────────────────────────────────────────────────────────────

def _make_mock_ft(name: str = "list_dir", description: str = "List directory") -> MagicMock:
    """Build a minimal AF FunctionTool mock with schema."""
    ft = MagicMock()
    ft.name = name
    ft.description = description
    ft.to_json_schema_spec.return_value = {
        "function": {
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Directory path"}},
                "required": ["path"],
            }
        }
    }
    return ft


async def _noop():
    return None


def _noop_side_effect(*args, **kwargs):
    """side_effect for MagicMock: returns a fresh coroutine on every call."""
    return _noop()


# ── MCPStdioProvider ──────────────────────────────────────────────────────────

class TestMCPStdioProvider:
    @pytest.fixture
    def provider(self):
        """MCPStdioProvider with AF internals fully mocked."""
        from app.tools.mcp_provider import MCPStdioProvider

        mock_ft = _make_mock_ft()
        mock_af = MagicMock()
        mock_af.functions = [mock_ft]
        mock_af.connect = MagicMock(side_effect=_noop_side_effect)
        mock_af.load_tools = MagicMock(side_effect=_noop_side_effect)
        mock_af.close = MagicMock(side_effect=_noop_side_effect)

        with patch("app.tools.mcp_provider.MCPStdioTool", return_value=mock_af):
            p = MCPStdioProvider(name="fs", command="npx", args=["-y", "server"])
        p._mock_af = mock_af
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
        content = MagicMock()
        content.text = "file1.txt\nfile2.txt"

        async def _call_tool(name, **kwargs):
            return [content]

        provider._mock_af.call_tool = _call_tool
        provider.start()
        try:
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
        """MCPStreamableHTTPProvider with AF internals fully mocked."""
        from app.tools.mcp_http_provider import MCPStreamableHTTPProvider

        mock_ft = _make_mock_ft(name="fetch", description="Fetch URL")
        mock_af = MagicMock()
        mock_af.functions = [mock_ft]
        mock_af.connect = MagicMock(side_effect=_noop_side_effect)
        mock_af.close = MagicMock(side_effect=_noop_side_effect)

        with patch("app.tools.mcp_http_provider.MCPStreamableHTTPTool", return_value=mock_af):
            p = MCPStreamableHTTPProvider(name="web", url="http://mcp-server/mcp")
        p._mock_af = mock_af
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
        async def _call_tool(name, **kwargs):
            return "fetched content"

        provider._mock_af.call_tool = _call_tool
        provider.start()
        try:
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

    def test_stdio_own_methods_only_start(self):
        from app.tools.mcp_provider import MCPStdioProvider
        own = {k for k, v in MCPStdioProvider.__dict__.items()
               if callable(v) and not k.startswith("__")}
        assert own == {"start"}

    def test_http_own_methods_only_start(self):
        from app.tools.mcp_http_provider import MCPStreamableHTTPProvider
        own = {k for k, v in MCPStreamableHTTPProvider.__dict__.items()
               if callable(v) and not k.startswith("__")}
        assert own == {"start"}
