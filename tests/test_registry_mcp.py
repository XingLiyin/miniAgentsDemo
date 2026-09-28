"""Tests for ToolRegistry (app/tools/registry.py).

`register()` was renamed to `register_tools(definitions, name)` and MCP servers are
now held as lazily-connecting providers keyed by server name, so tool lists are read
live from each provider rather than copied into the registry at registration time.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from app.common.errors import AppError
from app.domain.models.agent import AgentCapability
from app.llm.types import InputSchema
from app.tools.registry import ToolRegistry
from app.tools.types import ToolDefinition, ToolResult


def _make_tool_def(name: str, description: str = "desc") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        input_schema=InputSchema(properties={"x": {"type": "string"}}, require=["x"]),
        handler=lambda args, ctx=None: ToolResult(content=f"{name}:ok"),
    )


def _fake_provider(tools: list[str] | None = None, initialized: bool = True) -> MagicMock:
    """A provider stand-in exposing the duck-typed surface ToolRegistry uses."""
    p = MagicMock()
    p._initialized = initialized
    p._loop = None
    p.list_definitions.return_value = [_make_tool_def(n) for n in (tools or [])]
    return p


def _register_provider(reg: ToolRegistry, name: str, provider) -> None:
    """Attach a provider the way register_mcp_* would, minus the background connect."""
    reg._mcp_providers[name] = provider
    reg._connect_locks[name] = threading.Lock()


# ── builtin tools ─────────────────────────────────────────────────────────────

class TestRegisterTools:
    def test_register_and_get(self):
        reg = ToolRegistry()
        td = _make_tool_def("x")
        reg.register_tools([td])
        assert reg.get("x") is td

    def test_register_many_with_source_name(self):
        reg = ToolRegistry()
        reg.register_tools([_make_tool_def("a"), _make_tool_def("b")], name="builtin")
        assert set(reg.list_names()) == {"a", "b"}

    def test_get_unknown_raises(self):
        with pytest.raises(AppError) as e:
            ToolRegistry().get("nope")
        assert e.value.code == "TOOL_NOT_FOUND"

    def test_is_registered(self):
        reg = ToolRegistry()
        reg.register_tools([_make_tool_def("known")])
        assert reg.is_registered("known")
        assert not reg.is_registered("unknown")

    def test_register_overwrites_duplicate(self):
        reg = ToolRegistry()
        reg.register_tools([_make_tool_def("dup", "first")])
        reg.register_tools([_make_tool_def("dup", "second")])
        assert reg.get("dup").description == "second"

    def test_handler_is_callable(self):
        reg = ToolRegistry()
        reg.register_tools([_make_tool_def("run")])
        assert reg.get("run").handler({}, None).content == "run:ok"

    def test_list_all_definitions(self):
        reg = ToolRegistry()
        reg.register_tools([_make_tool_def("a"), _make_tool_def("b")])
        assert {td.name for td in reg.list_all_definitions()} == {"a", "b"}

    def test_empty_registry(self):
        reg = ToolRegistry()
        assert reg.list_names() == [] and reg.list_all_definitions() == []


class TestRegisterControlTools:
    def test_registers_returned_definitions(self):
        reg = ToolRegistry()
        with patch("app.tools.control_tools.get_control_tools",
                   return_value=[_make_tool_def("submit_task")]) as g:
            reg.register_control_tools(MagicMock(), MagicMock(), MagicMock())
        assert reg.is_registered("submit_task")
        assert g.called

    def test_agent_store_optional(self):
        reg = ToolRegistry()
        with patch("app.tools.control_tools.get_control_tools",
                   return_value=[_make_tool_def("c")]) as g:
            reg.register_control_tools("task_svc", "session_svc")
        assert g.call_args.args == ("task_svc", "session_svc", None)


class TestToLLMTools:
    def test_returns_matching(self):
        reg = ToolRegistry()
        reg.register_tools([_make_tool_def("a"), _make_tool_def("b")])
        out = reg.to_llm_tools(["a", "b"])
        assert [t.name for t in out] == ["a", "b"]
        assert out[0].input_schema.require == ["x"]

    def test_skips_unknown(self):
        reg = ToolRegistry()
        reg.register_tools([_make_tool_def("known")])
        assert [t.name for t in reg.to_llm_tools(["known", "ghost"])] == ["known"]

    def test_preserves_requested_order(self):
        reg = ToolRegistry()
        reg.register_tools([_make_tool_def("a"), _make_tool_def("b")])
        assert [t.name for t in reg.to_llm_tools(["b", "a"])] == ["b", "a"]

    def test_empty_names(self):
        assert ToolRegistry().to_llm_tools([]) == []

    def test_builtin_wins_over_mcp_with_same_name(self):
        reg = ToolRegistry()
        reg.register_tools([_make_tool_def("shared", "builtin-desc")])
        _register_provider(reg, "srv", _fake_provider(["shared"]))
        assert reg.to_llm_tools(["shared"])[0].description == "builtin-desc"


# ── MCP registration ──────────────────────────────────────────────────────────

class TestRegisterMCPStdio:
    def test_creates_provider_and_starts_background_connect(self):
        reg = ToolRegistry()
        with patch("app.tools.mcp_provider.MCPStdioProvider") as cls, \
             patch.object(ToolRegistry, "_start_connect_bg") as bg:
            reg.register_mcp_stdio("fs", "npx", ["-y", "server"], {"K": "V"},
                                   timeout=11, connect_timeout=2)
        assert cls.call_args.kwargs == {
            "name": "fs", "command": "npx", "args": ["-y", "server"],
            "env": {"K": "V"}, "timeout": 11, "connect_timeout": 2,
        }
        assert "fs" in reg._mcp_providers and "fs" in reg._connect_locks
        assert bg.called

    def test_defaults(self):
        reg = ToolRegistry()
        with patch("app.tools.mcp_provider.MCPStdioProvider") as cls, \
             patch.object(ToolRegistry, "_start_connect_bg"):
            reg.register_mcp_stdio("fs", "npx")
        assert cls.call_args.kwargs["args"] is None
        assert cls.call_args.kwargs["timeout"] == 30


class TestRegisterMCPHttp:
    def test_creates_provider_and_starts_background_connect(self):
        reg = ToolRegistry()
        with patch("app.tools.mcp_http_provider.MCPStreamableHTTPProvider") as cls, \
             patch.object(ToolRegistry, "_start_connect_bg") as bg:
            reg.register_mcp_http("web", "http://h/mcp", timeout=9, connect_timeout=3)
        assert cls.call_args.kwargs == {
            "name": "web", "url": "http://h/mcp", "timeout": 9, "connect_timeout": 3}
        assert "web" in reg._mcp_providers
        assert bg.called


class TestLiveMCPDefinitions:
    def test_mcp_tools_visible_through_get_and_list(self):
        reg = ToolRegistry()
        _register_provider(reg, "srv", _fake_provider(["remote_a", "remote_b"]))
        assert reg.get("remote_a").name == "remote_a"
        assert reg.is_registered("remote_b")
        assert set(reg.list_names()) == {"remote_a", "remote_b"}

    def test_duplicate_names_reported_once_in_list_names(self):
        reg = ToolRegistry()
        reg.register_tools([_make_tool_def("shared")])
        _register_provider(reg, "srv", _fake_provider(["shared", "extra"]))
        assert sorted(reg.list_names()) == ["extra", "shared"]

    def test_list_all_definitions_prefers_builtin(self):
        reg = ToolRegistry()
        reg.register_tools([_make_tool_def("shared", "builtin")])
        _register_provider(reg, "srv", _fake_provider(["shared"]))
        defs = {td.name: td for td in reg.list_all_definitions()}
        assert defs["shared"].description == "builtin"

    def test_provider_error_is_swallowed(self):
        reg = ToolRegistry()
        bad = _fake_provider()
        bad.list_definitions.side_effect = RuntimeError("stream closed")
        _register_provider(reg, "bad", bad)
        assert reg.list_names() == []

    def test_disconnected_provider_triggers_lazy_connect_and_is_skipped(self):
        reg = ToolRegistry()
        p = _fake_provider(["t"], initialized=False)
        _register_provider(reg, "srv", p)
        with patch.object(ToolRegistry, "_start_connect_bg") as bg:
            assert reg.list_names() == []
        assert bg.called


class TestGetFromCapability:
    def test_builtin_found_without_consulting_mcp(self):
        reg = ToolRegistry()
        reg.register_tools([_make_tool_def("local")])
        cap = AgentCapability(tools=["local"], mcp_servers=[])
        assert reg.get_from_capability("local", cap).name == "local"

    def test_authorized_mcp_server_searched(self):
        reg = ToolRegistry()
        _register_provider(reg, "srv", _fake_provider(["remote"]))
        cap = AgentCapability(mcp_servers=["srv"])
        assert reg.get_from_capability("remote", cap).name == "remote"

    def test_unauthorized_server_not_searched(self):
        reg = ToolRegistry()
        _register_provider(reg, "srv", _fake_provider(["remote"]))
        with pytest.raises(AppError) as e:
            reg.get_from_capability("remote", AgentCapability(mcp_servers=["other"]))
        assert e.value.code == "TOOL_NOT_FOUND"

    def test_unknown_tool_raises(self):
        with pytest.raises(AppError) as e:
            ToolRegistry().get_from_capability("x", AgentCapability())
        assert e.value.code == "TOOL_NOT_FOUND"


class TestServerQueries:
    def test_tool_names_and_definitions(self):
        reg = ToolRegistry()
        _register_provider(reg, "srv", _fake_provider(["a", "b"]))
        assert reg.get_server_tool_names("srv") == ["a", "b"]
        assert [d.name for d in reg.get_server_tool_definitions("srv")] == ["a", "b"]

    def test_unknown_server_is_empty(self):
        reg = ToolRegistry()
        assert reg.get_server_tool_names("ghost") == []
        assert reg.get_server_tool_definitions("ghost") == []

    def test_definitions_error_swallowed(self):
        reg = ToolRegistry()
        p = _fake_provider()
        p.list_definitions.side_effect = RuntimeError("closed")
        _register_provider(reg, "srv", p)
        assert reg.get_server_tool_definitions("srv") == []

    def test_disconnected_server_returns_empty_and_connects(self):
        reg = ToolRegistry()
        _register_provider(reg, "srv", _fake_provider(["a"], initialized=False))
        with patch.object(ToolRegistry, "_start_connect_bg") as bg:
            assert reg.get_server_tool_definitions("srv") == []
        assert bg.called

    @pytest.mark.parametrize("initialized,expected", [(True, "CONNECTED"), (False, "DISCONNECTED")])
    def test_server_status(self, initialized, expected):
        reg = ToolRegistry()
        _register_provider(reg, "srv", _fake_provider(initialized=initialized))
        assert reg.get_server_status("srv") == expected

    def test_unknown_server_status(self):
        assert ToolRegistry().get_server_status("ghost") == "DISCONNECTED"


# ── warm hook ─────────────────────────────────────────────────────────────────

class TestWarmHook:
    def test_hook_receives_name_description_pairs(self):
        reg = ToolRegistry()
        seen = []
        reg.set_warm_hook(seen.extend)
        reg._warm_definitions([_make_tool_def("a", "da"), _make_tool_def("b", "db")])
        assert seen == [("a", "da"), ("b", "db")]

    def test_no_hook_is_noop(self):
        ToolRegistry()._warm_definitions([_make_tool_def("a")])

    def test_empty_definitions_skipped(self):
        reg = ToolRegistry()
        called = []
        reg.set_warm_hook(lambda pairs: called.append(pairs))
        reg._warm_definitions([])
        assert called == []

    def test_hook_exception_swallowed(self):
        reg = ToolRegistry()
        reg.set_warm_hook(lambda pairs: (_ for _ in ()).throw(RuntimeError("boom")))
        reg._warm_definitions([_make_tool_def("a")])

    def test_hook_can_be_cleared(self):
        reg = ToolRegistry()
        reg.set_warm_hook(lambda pairs: None)
        reg.set_warm_hook(None)
        reg._warm_definitions([_make_tool_def("a")])

    def test_missing_description_becomes_empty_string(self):
        reg = ToolRegistry()
        seen = []
        reg.set_warm_hook(seen.extend)
        td = _make_tool_def("a")
        td.description = ""
        reg._warm_definitions([td])
        assert seen == [("a", "")]


# ── refresh ───────────────────────────────────────────────────────────────────

class TestRefreshMCP:
    def test_unknown_server_raises(self):
        with pytest.raises(AppError) as e:
            ToolRegistry().refresh_mcp("ghost")
        assert e.value.code == "MCP_NOT_FOUND"

    def test_live_session_reloads_and_warms(self):
        reg = ToolRegistry()
        p = _fake_provider(["a"])
        _register_provider(reg, "srv", p)
        seen = []
        reg.set_warm_hook(seen.extend)
        reg.refresh_mcp("srv")
        assert p.reload_tools.called
        assert seen == [("a", "desc")]

    def test_not_yet_connected_triggers_connect(self):
        reg = ToolRegistry()
        p = _fake_provider(initialized=False)
        _register_provider(reg, "srv", p)
        with patch.object(ToolRegistry, "_start_connect_bg") as bg:
            reg.refresh_mcp("srv")
        assert not p.reload_tools.called
        assert bg.called

    def test_genuine_error_on_live_session_propagates(self):
        reg = ToolRegistry()
        p = _fake_provider()
        p.reload_tools.side_effect = RuntimeError("real failure")
        _register_provider(reg, "srv", p)
        with pytest.raises(RuntimeError, match="real failure"):
            reg.refresh_mcp("srv")

    def test_stale_session_reconnects_instead_of_raising(self):
        reg = ToolRegistry()
        p = _fake_provider()

        def _reload():
            p._initialized = False          # what mcp_base does on a dead transport
            raise RuntimeError("Session terminated")

        p.reload_tools.side_effect = _reload
        _register_provider(reg, "srv", p)
        with patch.object(ToolRegistry, "_start_connect_bg") as bg:
            reg.refresh_mcp("srv")          # must not raise
        assert bg.called


# ── shutdown ──────────────────────────────────────────────────────────────────

class TestShutdown:
    def test_shutdown_stops_all_and_clears(self):
        reg = ToolRegistry()
        a, b = _fake_provider(), _fake_provider()
        _register_provider(reg, "a", a)
        _register_provider(reg, "b", b)
        reg.shutdown()
        assert a.stop.called and b.stop.called
        assert reg._mcp_providers == {}

    def test_shutdown_survives_stop_failure(self):
        reg = ToolRegistry()
        bad = _fake_provider()
        bad.stop.side_effect = RuntimeError("cannot stop")
        good = _fake_provider()
        _register_provider(reg, "bad", bad)
        _register_provider(reg, "good", good)
        reg.shutdown()
        assert good.stop.called and reg._mcp_providers == {}

    def test_shutdown_empty_registry(self):
        ToolRegistry().shutdown()

    def test_shutdown_one_stops_connected_provider(self):
        reg = ToolRegistry()
        p = _fake_provider()
        _register_provider(reg, "srv", p)
        reg._last_connect_attempt["srv"] = 1.0
        reg.shutdown_one("srv")
        assert p.stop.called
        assert "srv" not in reg._mcp_providers
        assert "srv" not in reg._connect_locks
        assert "srv" not in reg._last_connect_attempt

    def test_shutdown_one_skips_stop_when_never_connected(self):
        reg = ToolRegistry()
        p = _fake_provider(initialized=False)
        _register_provider(reg, "srv", p)
        reg.shutdown_one("srv")
        assert not p.stop.called

    def test_shutdown_one_unknown_server_is_noop(self):
        ToolRegistry().shutdown_one("ghost")

    def test_shutdown_one_survives_stop_failure(self):
        reg = ToolRegistry()
        p = _fake_provider()
        p.stop.side_effect = RuntimeError("boom")
        _register_provider(reg, "srv", p)
        reg.shutdown_one("srv")
        assert "srv" not in reg._mcp_providers


# ── lazy connect bookkeeping ──────────────────────────────────────────────────

class TestConnectBookkeeping:
    def test_try_connect_returns_true_when_already_connected(self):
        reg = ToolRegistry()
        p = _fake_provider()
        _register_provider(reg, "srv", p)
        assert reg._try_connect("srv", p) is True

    def test_try_connect_without_lock_returns_false(self):
        reg = ToolRegistry()
        assert reg._try_connect("srv", _fake_provider(initialized=False)) is False

    def test_try_connect_resets_failure_count(self):
        reg = ToolRegistry()
        p = _fake_provider(initialized=False)
        _register_provider(reg, "srv", p)
        reg._connect_failures["srv"] = 99
        with patch.object(ToolRegistry, "_start_connect_bg"):
            assert reg._try_connect("srv", p) is False
        assert reg._connect_failures["srv"] == 0

    def test_connect_bg_without_lock_is_noop(self):
        reg = ToolRegistry()
        p = _fake_provider(initialized=False)
        reg._mcp_providers["srv"] = p
        reg._start_connect_bg("srv", p)          # no lock registered
        assert not p.start.called

    def test_connect_bg_skips_when_already_connected(self):
        reg = ToolRegistry()
        p = _fake_provider()
        _register_provider(reg, "srv", p)
        reg._start_connect_bg("srv", p)
        assert not p.start.called

    def test_connect_bg_skips_inside_cooldown(self):
        reg = ToolRegistry()
        p = _fake_provider(initialized=False)
        _register_provider(reg, "srv", p)
        reg._last_connect_attempt["srv"] = time.monotonic()
        reg._start_connect_bg("srv", p)
        assert not p.start.called

    def test_connect_bg_skips_after_max_failures(self):
        reg = ToolRegistry()
        p = _fake_provider(initialized=False)
        _register_provider(reg, "srv", p)
        reg._connect_failures["srv"] = ToolRegistry._MAX_CONNECT_FAILURES
        reg._start_connect_bg("srv", p)
        assert not p.start.called

    def _join_mcp_threads(self):
        for t in threading.enumerate():
            if t.name.startswith("mcp-connect-"):
                t.join(timeout=5)

    def test_connect_bg_success_resets_counters_and_warms(self):
        reg = ToolRegistry()
        p = _fake_provider(["a"], initialized=False)

        def _start():
            p._initialized = True

        p.start.side_effect = _start
        _register_provider(reg, "srv", p)
        seen = []
        reg.set_warm_hook(seen.extend)
        reg._start_connect_bg("srv", p)
        self._join_mcp_threads()
        assert p.start.called
        assert reg._connect_failures["srv"] == 0
        assert "srv" not in reg._last_connect_attempt
        assert seen == [("a", "desc")]

    def test_connect_bg_failure_increments_counter(self):
        reg = ToolRegistry()
        p = _fake_provider(initialized=False)
        p.start.side_effect = RuntimeError("refused")
        _register_provider(reg, "srv", p)
        reg._start_connect_bg("srv", p)
        self._join_mcp_threads()
        assert reg._connect_failures["srv"] == 1

    def test_connect_bg_logs_at_failure_ceiling(self):
        reg = ToolRegistry()
        p = _fake_provider(initialized=False)
        p.start.side_effect = RuntimeError("refused")
        _register_provider(reg, "srv", p)
        reg._connect_failures["srv"] = ToolRegistry._MAX_CONNECT_FAILURES - 1
        reg._start_connect_bg("srv", p)
        self._join_mcp_threads()
        assert reg._connect_failures["srv"] == ToolRegistry._MAX_CONNECT_FAILURES

    def test_connect_bg_stops_stale_loop_before_restart(self):
        reg = ToolRegistry()
        p = _fake_provider(initialized=False)
        p._loop = object()                    # a leftover loop from a previous attempt
        _register_provider(reg, "srv", p)
        reg._start_connect_bg("srv", p)
        self._join_mcp_threads()
        assert p.stop.called and p.start.called

    def test_connect_bg_ignores_stop_failure_before_restart(self):
        reg = ToolRegistry()
        p = _fake_provider(initialized=False)
        p._loop = object()
        p.stop.side_effect = RuntimeError("already dead")
        _register_provider(reg, "srv", p)
        reg._start_connect_bg("srv", p)
        self._join_mcp_threads()
        assert p.start.called

    def test_connect_bg_bails_if_connected_between_checks(self):
        """The provider connects between the caller's check and the bg thread's."""
        reg = ToolRegistry()
        p = _fake_provider(initialized=False)
        _register_provider(reg, "srv", p)

        class FlippingLock:
            """Marks the provider connected on the second `with` (the bg thread's)."""

            def __init__(self):
                self._inner = threading.Lock()
                self.entries = 0

            def __enter__(self):
                self._inner.acquire()
                self.entries += 1
                if self.entries == 2:
                    p._initialized = True
                return self

            def __exit__(self, *exc):
                self._inner.release()
                return False

        reg._connect_locks["srv"] = FlippingLock()
        reg._start_connect_bg("srv", p)
        self._join_mcp_threads()
        assert not p.start.called

    def test_warm_after_connect_failure_is_swallowed(self):
        reg = ToolRegistry()
        p = _fake_provider(initialized=False)

        def _start():
            p._initialized = True

        p.start.side_effect = _start
        p.list_definitions.side_effect = RuntimeError("gone")
        _register_provider(reg, "srv", p)
        reg.set_warm_hook(lambda pairs: None)
        reg._start_connect_bg("srv", p)
        self._join_mcp_threads()
        assert p.start.called
