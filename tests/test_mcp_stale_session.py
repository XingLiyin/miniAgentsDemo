"""Regression tests: refresh on an MCP server whose transport stream was
already closed must NOT raise a raw anyio.ClosedResourceError (which bubbles
up as an unhandled 500). Instead the provider is marked disconnected and the
registry transparently reconnects.

Root cause: `reload_tools()` lacked the session-terminated guard that `call()`
has, so a stale-but-`_initialized=True` session raised ClosedResourceError out
of `refresh_mcp`.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import anyio
import pytest

from app.tools.mcp_provider import MCPStdioProvider
from app.tools.registry import ToolRegistry


# ── Provider layer: reload_tools must flip _initialized on a closed session ────

def test_reload_tools_marks_disconnected_on_closed_session():
    p = MCPStdioProvider(name="fs", command="npx", args=["-y", "server"])
    p._initialized = True

    def boom(coro, timeout=None):
        coro.close()  # avoid "coroutine was never awaited" warning
        raise anyio.ClosedResourceError()

    p._run_sync = boom  # type: ignore[method-assign]

    with pytest.raises(anyio.ClosedResourceError):
        p.reload_tools()

    # The stale flag must be corrected so the next demand reconnects.
    assert p._initialized is False


# ── Registry layer: refresh_mcp reconnects instead of propagating ──────────────

def test_refresh_mcp_reconnects_when_session_closed():
    reg = ToolRegistry()
    p = MagicMock()
    p._initialized = True
    p._loop = None

    def reload_side_effect():
        # mirrors the fixed provider: terminated session flips the flag, re-raises
        p._initialized = False
        raise anyio.ClosedResourceError()

    p.reload_tools.side_effect = reload_side_effect

    reg._mcp_providers["srv"] = p
    reg._connect_locks["srv"] = threading.Lock()

    # Must NOT raise: stale session should trigger a (background) reconnect.
    reg.refresh_mcp("srv")


def test_refresh_mcp_propagates_genuine_error_on_live_session():
    """A failure that does NOT terminate the session (provider stays initialized)
    should still surface, not be silently swallowed."""
    reg = ToolRegistry()
    p = MagicMock()
    p._initialized = True
    p._loop = None
    p.reload_tools.side_effect = RuntimeError("boom")  # leaves _initialized True

    reg._mcp_providers["srv"] = p
    reg._connect_locks["srv"] = threading.Lock()

    with pytest.raises(RuntimeError):
        reg.refresh_mcp("srv")
