"""Tests for ToolRegistry MCP lifecycle management (app/tools/registry.py)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.common.errors import AppError
from app.tools.definition import ToolDefinition, ToolResult
from app.tools.registry import ToolRegistry


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_tool_def(name: str) -> ToolDefinition:
    from app.llm.types import InputSchema
    return ToolDefinition(
        name=name,
        description=f"Tool {name}",
        input_schema=InputSchema(properties={"x": {"type": "string"}}, require=["x"]),
        handler=lambda args: ToolResult(content="ok"),
    )


def _mock_provider(tool_names: list[str]) -> MagicMock:
    """Build a mock ToolProvider that lists given tool names."""
    provider = MagicMock()
    provider.list_definitions.return_value = [_make_tool_def(n) for n in tool_names]
    return provider


async def _noop():
    return None


def _noop_side_effect(*args, **kwargs):
    return _noop()


def _make_mock_mcp_af(tool_names: list[str]) -> MagicMock:
    """Build a mock AF MCP tool with given function names."""
    fts = []
    for name in tool_names:
        ft = MagicMock()
        ft.name = name
        ft.description = f"Tool {name}"
        ft.to_json_schema_spec.return_value = {
            "function": {
                "parameters": {
                    "type": "object",
                    "properties": {"x": {"type": "string"}},
                    "required": ["x"],
                }
            }
        }
        fts.append(ft)
    mock_af = MagicMock()
    mock_af.functions = fts
    mock_af.connect = MagicMock(side_effect=_noop_side_effect)
    mock_af.load_tools = MagicMock(side_effect=_noop_side_effect)
    mock_af.close = MagicMock(side_effect=_noop_side_effect)
    return mock_af


# ── Basic registry operations ──────────────────────────────────────────────────

class TestToolRegistryBasic:
    def test_register_and_get(self):
        reg = ToolRegistry()
        td = _make_tool_def("tool_a")
        reg.register(td)
        assert reg.get("tool_a") is td

    def test_is_registered(self):
        reg = ToolRegistry()
        reg.register(_make_tool_def("tool_b"))
        assert reg.is_registered("tool_b")
        assert not reg.is_registered("missing")

    def test_get_missing_raises_app_error(self):
        reg = ToolRegistry()
        with pytest.raises(AppError) as exc_info:
            reg.get("no_such_tool")
        assert exc_info.value.code == "TOOL_NOT_FOUND"

    def test_register_overwrites_duplicate(self):
        reg = ToolRegistry()
        td1 = _make_tool_def("dup")
        td2 = _make_tool_def("dup")
        reg.register(td1)
        reg.register(td2)
        assert reg.get("dup") is td2

    def test_register_provider_registers_all_tools(self):
        reg = ToolRegistry()
        provider = _mock_provider(["t1", "t2", "t3"])
        reg.register_provider(provider)
        assert reg.is_registered("t1")
        assert reg.is_registered("t2")
        assert reg.is_registered("t3")

    def test_to_llm_tools_returns_matching(self):
        reg = ToolRegistry()
        reg.register(_make_tool_def("x"))
        reg.register(_make_tool_def("y"))
        llm_tools = reg.to_llm_tools(["x", "y"])
        assert len(llm_tools) == 2
        assert {t.name for t in llm_tools} == {"x", "y"}

    def test_to_llm_tools_skips_unknown(self):
        reg = ToolRegistry()
        reg.register(_make_tool_def("known"))
        llm_tools = reg.to_llm_tools(["known", "unknown"])
        assert len(llm_tools) == 1
        assert llm_tools[0].name == "known"


# ── register_mcp_stdio ─────────────────────────────────────────────────────────

class TestRegisterMCPStdio:
    def test_registers_tools_after_start(self):
        reg = ToolRegistry()
        mock_af = _make_mock_mcp_af(["read_file", "write_file"])

        with patch("app.tools.mcp_provider.MCPStdioTool", return_value=mock_af):
            reg.register_mcp_stdio(name="fs", command="npx", args=["-y", "server-fs"])

        try:
            assert reg.is_registered("read_file")
            assert reg.is_registered("write_file")
        finally:
            reg.shutdown()

    def test_provider_added_to_mcp_providers_list(self):
        reg = ToolRegistry()
        mock_af = _make_mock_mcp_af(["tool_x"])

        with patch("app.tools.mcp_provider.MCPStdioTool", return_value=mock_af):
            reg.register_mcp_stdio(name="srv", command="python", args=["server.py"])

        try:
            assert len(reg._mcp_providers) == 1
        finally:
            reg.shutdown()


# ── register_mcp_http ─────────────────────────────────────────────────────────

class TestRegisterMCPHttp:
    def test_registers_tools_after_start(self):
        reg = ToolRegistry()
        mock_af = _make_mock_mcp_af(["search", "summarize"])

        with patch("app.tools.mcp_http_provider.MCPStreamableHTTPTool", return_value=mock_af):
            reg.register_mcp_http(name="web", url="http://mcp-server/mcp")

        try:
            assert reg.is_registered("search")
            assert reg.is_registered("summarize")
        finally:
            reg.shutdown()

    def test_provider_added_to_mcp_providers_list(self):
        reg = ToolRegistry()
        mock_af = _make_mock_mcp_af(["tool_y"])

        with patch("app.tools.mcp_http_provider.MCPStreamableHTTPTool", return_value=mock_af):
            reg.register_mcp_http(name="srv", url="http://srv/mcp")

        try:
            assert len(reg._mcp_providers) == 1
        finally:
            reg.shutdown()


# ── shutdown ──────────────────────────────────────────────────────────────────

class TestRegistryShutdown:
    def test_shutdown_stops_all_providers(self):
        reg = ToolRegistry()
        mock_af_a = _make_mock_mcp_af(["a1"])
        mock_af_b = _make_mock_mcp_af(["b1"])

        with patch("app.tools.mcp_provider.MCPStdioTool", return_value=mock_af_a):
            reg.register_mcp_stdio(name="srv_a", command="cmd_a")
        with patch("app.tools.mcp_http_provider.MCPStreamableHTTPTool", return_value=mock_af_b):
            reg.register_mcp_http(name="srv_b", url="http://b/mcp")

        assert len(reg._mcp_providers) == 2
        reg.shutdown()
        assert reg._mcp_providers == []

    def test_shutdown_with_no_mcp_providers_is_noop(self):
        reg = ToolRegistry()
        reg.shutdown()  # must not raise
        assert reg._mcp_providers == []

    def test_shutdown_tolerates_stop_exception(self):
        reg = ToolRegistry()
        bad_provider = MagicMock()
        bad_provider.stop.side_effect = RuntimeError("force fail")
        reg._mcp_providers.append(bad_provider)
        reg.shutdown()  # must not propagate
        assert reg._mcp_providers == []

    def test_multiple_mcp_servers_tools_coexist(self):
        reg = ToolRegistry()
        mock_af_a = _make_mock_mcp_af(["tool_from_a"])
        mock_af_b = _make_mock_mcp_af(["tool_from_b"])

        with patch("app.tools.mcp_provider.MCPStdioTool", return_value=mock_af_a):
            reg.register_mcp_stdio(name="a", command="a_cmd")
        with patch("app.tools.mcp_http_provider.MCPStreamableHTTPTool", return_value=mock_af_b):
            reg.register_mcp_http(name="b", url="http://b/mcp")

        try:
            assert reg.is_registered("tool_from_a")
            assert reg.is_registered("tool_from_b")
        finally:
            reg.shutdown()
