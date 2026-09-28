"""Tests for MCPService (app/domain/services/mcp_service.py) and the skill-executor
tools (app/tools/skill_executor.py).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.common.errors import AppError
from app.domain.services.mcp_service import MCPServerInfo, MCPService, MCPToolInfo
from app.tools.skill_executor import (
    exec_skill_script, get_skill_executor_tools, get_skill_files,
    load_skill_reference, skill_executor_tool,
)
from app.tools import skill_executor as se
from app.tools.types import CallContext, ToolDefinition, ToolResult


# ── MCPService ────────────────────────────────────────────────────────────────

def _stdio_cfg(name="fs", **kw):
    base = dict(name=name, type="stdio", command="npx", args=["-y", "srv"],
                env={}, timeout=30, connect_timeout=5)
    base.update(kw)
    return base


def _http_cfg(name="web", **kw):
    base = dict(name=name, type="http", url="http://h/mcp", timeout=30, connect_timeout=5)
    base.update(kw)
    return base


def _svc(configs=None, status="CONNECTED", tools=None):
    store = MagicMock()
    by_name = {c["name"]: c for c in (configs or [])}
    store.get.side_effect = lambda n: by_name.get(n)
    store.list_all.return_value = list(by_name.values())
    store.save.side_effect = lambda d: by_name.__setitem__(d["name"], d)
    registry = MagicMock()
    registry.get_server_status.return_value = status
    registry.get_server_tool_definitions.return_value = tools or []
    registry._mcp_providers = {}
    return MCPService(registry, store), registry, store


def _tool_def(name="remote_tool", description="does a thing"):
    from app.llm.types import InputSchema
    return ToolDefinition(name=name, description=description,
                          input_schema=InputSchema(properties={}, require=[]),
                          handler=lambda a, c=None: ToolResult())


class TestRegisterStdio:
    def test_registers_persists_and_returns_info(self):
        svc, registry, store = _svc(tools=[_tool_def()])
        info = svc.register_stdio("fs", "npx", ["-y", "srv"], {"K": "V"},
                                  timeout=11, connect_timeout=2)
        assert registry.register_mcp_stdio.call_args.kwargs["command"] == "npx"
        saved = store.save.call_args.args[0]
        assert saved["type"] == "stdio" and saved["timeout"] == 11
        assert info.name == "fs" and info.type == "stdio"
        assert info.status == "CONNECTED"
        assert info.tools == [MCPToolInfo(name="remote_tool", description="does a thing")]

    def test_defaults_are_normalised(self):
        svc, _, store = _svc()
        svc.register_stdio("fs", "npx")
        saved = store.save.call_args.args[0]
        assert saved["args"] == [] and saved["env"] == {}
        assert saved["timeout"] == 30 and saved["connect_timeout"] == 5

    def test_duplicate_name_raises(self):
        svc, _, _ = _svc([_stdio_cfg()])
        with pytest.raises(AppError) as e:
            svc.register_stdio("fs", "npx")
        assert e.value.code == "MCP_ALREADY_EXISTS"


class TestRegisterHttp:
    def test_registers_persists_and_returns_info(self):
        svc, registry, store = _svc()
        info = svc.register_http("web", "http://h/mcp", timeout=9, connect_timeout=3)
        assert registry.register_mcp_http.call_args.kwargs["url"] == "http://h/mcp"
        saved = store.save.call_args.args[0]
        assert saved["type"] == "http" and saved["timeout"] == 9
        assert info.type == "http" and info.url == "http://h/mcp"

    def test_duplicate_name_raises(self):
        svc, _, _ = _svc([_http_cfg()])
        with pytest.raises(AppError) as e:
            svc.register_http("web", "http://h/mcp")
        assert e.value.code == "MCP_ALREADY_EXISTS"


class TestListAll:
    def test_returns_live_status_and_tools(self):
        svc, _, _ = _svc([_stdio_cfg(), _http_cfg()], tools=[_tool_def()])
        out = svc.list_all()
        assert {i.name for i in out} == {"fs", "web"}
        assert all(i.status == "CONNECTED" and len(i.tools) == 1 for i in out)

    def test_invalid_configs_are_skipped(self):
        svc, _, _ = _svc([{"name": "broken", "type": "unknown"}])
        assert svc.list_all() == []

    def test_empty_store(self):
        svc, _, _ = _svc()
        assert svc.list_all() == []


class TestGet:
    def test_returns_info(self):
        svc, _, _ = _svc([_stdio_cfg()], status="DISCONNECTED")
        info = svc.get("fs")
        assert info.name == "fs" and info.status == "DISCONNECTED"
        assert info.command == "npx" and info.args == ["-y", "srv"]

    def test_http_fields(self):
        svc, _, _ = _svc([_http_cfg()])
        info = svc.get("web")
        assert info.url == "http://h/mcp" and info.timeout == 30

    def test_unknown_raises(self):
        svc, _, _ = _svc()
        with pytest.raises(AppError) as e:
            svc.get("ghost")
        assert e.value.code == "MCP_NOT_FOUND"

    def test_invalid_config_raises(self):
        svc, _, _ = _svc([{"name": "broken", "type": "weird"}])
        with pytest.raises(AppError) as e:
            svc.get("broken")
        assert e.value.code == "MCP_INVALID_CONFIG"


class TestDelete:
    def test_stops_and_removes(self):
        svc, registry, store = _svc([_stdio_cfg()])
        svc.delete("fs")
        registry.shutdown_one.assert_called_once_with("fs")
        store.delete.assert_called_once_with("fs")

    def test_unknown_raises(self):
        svc, _, _ = _svc()
        with pytest.raises(AppError) as e:
            svc.delete("ghost")
        assert e.value.code == "MCP_NOT_FOUND"


class TestRefresh:
    def test_reloads_and_returns_info(self):
        svc, registry, _ = _svc([_stdio_cfg()], tools=[_tool_def()])
        info = svc.refresh("fs")
        registry.refresh_mcp.assert_called_once_with("fs")
        assert info.name == "fs" and len(info.tools) == 1

    def test_unknown_raises(self):
        svc, _, _ = _svc()
        with pytest.raises(AppError) as e:
            svc.refresh("ghost")
        assert e.value.code == "MCP_NOT_FOUND"

    def test_invalid_config_raises(self):
        svc, _, _ = _svc([{"name": "broken", "type": "weird"}])
        with pytest.raises(AppError) as e:
            svc.refresh("broken")
        assert e.value.code == "MCP_INVALID_CONFIG"


class TestRestoreAll:
    def test_restores_both_kinds(self):
        svc, registry, _ = _svc([_stdio_cfg(), _http_cfg()])
        svc.restore_all()
        assert registry.register_mcp_stdio.called and registry.register_mcp_http.called

    def test_empty_store_is_a_noop(self):
        svc, registry, _ = _svc()
        svc.restore_all()
        assert not registry.register_mcp_stdio.called

    def test_already_registered_is_skipped(self):
        svc, registry, _ = _svc([_stdio_cfg()])
        registry._mcp_providers = {"fs": MagicMock()}
        svc.restore_all()
        assert not registry.register_mcp_stdio.called

    def test_unknown_type_is_logged_not_raised(self):
        svc, registry, _ = _svc([{"name": "broken", "type": "weird"}])
        svc.restore_all()
        assert not registry.register_mcp_stdio.called

    def test_one_failure_does_not_stop_the_rest(self):
        svc, registry, _ = _svc([_stdio_cfg("bad"), _http_cfg("good")])
        registry.register_mcp_stdio.side_effect = RuntimeError("spawn failed")
        svc.restore_all()
        assert registry.register_mcp_http.called

    def test_stdio_restore_forwards_settings(self):
        svc, registry, _ = _svc([_stdio_cfg(env={"A": "B"}, timeout=7, connect_timeout=1)])
        svc.restore_all()
        kw = registry.register_mcp_stdio.call_args.kwargs
        assert kw["env"] == {"A": "B"} and kw["timeout"] == 7 and kw["connect_timeout"] == 1

    def test_empty_env_becomes_none(self):
        svc, registry, _ = _svc([_stdio_cfg(env={})])
        svc.restore_all()
        assert registry.register_mcp_stdio.call_args.kwargs["env"] is None

    def test_missing_name_key_is_tolerated(self):
        svc, registry, _ = _svc()
        svc._store.list_all.return_value = [{"type": "stdio"}]
        svc.restore_all()
        assert not registry.register_mcp_stdio.called


class TestConfigToInfo:
    def test_stdio(self):
        info = MCPService._config_to_info(_stdio_cfg())
        assert info.type == "stdio" and info.command == "npx"

    def test_http(self):
        info = MCPService._config_to_info(_http_cfg())
        assert info.type == "http" and info.url == "http://h/mcp"

    def test_unknown_type_is_none(self):
        assert MCPService._config_to_info({"name": "x", "type": "weird"}) is None

    def test_missing_type_is_none(self):
        assert MCPService._config_to_info({"name": "x"}) is None

    def test_stdio_defaults(self):
        info = MCPService._config_to_info({"name": "x", "type": "stdio"})
        assert info.args == [] and info.connect_timeout == 5

    def test_info_defaults(self):
        info = MCPServerInfo(name="n", type="stdio")
        assert info.status == "DISCONNECTED" and info.tools == []
        assert info.connect_timeout == 5


# ── skill_executor tools ──────────────────────────────────────────────────────

def _ctx(skill_name="pptx", **kw) -> CallContext:
    task = SimpleNamespace(settings={"skill_name": skill_name} if skill_name else {})
    return CallContext(session_id="s1", agent_id="a1", task=task, **kw)


def _skill(name="pptx", **kw):
    s = MagicMock()
    s.name = name
    s.source = SimpleNamespace(label="local")
    s.get_files.return_value = kw.get("files", "a.py\nb.py")
    s.load_reference.return_value = kw.get("reference", "reference body")
    s.exec_script.return_value = kw.get("exec", ToolResult(content="script output"))
    return s


@pytest.fixture
def registry():
    reg = MagicMock()
    with patch("app.skills.registry.get_skill_registry", return_value=reg):
        yield reg


class TestSkillExecutorRegistration:
    def test_all_three_tools_are_registered(self):
        names = {t.name for t in get_skill_executor_tools()}
        assert names == {"get_skill_files", "load_skill_reference", "exec_skill_script"}

    def test_returns_a_copy(self):
        assert get_skill_executor_tools() is not get_skill_executor_tools()

    def test_tools_are_not_control_tools(self):
        assert all(not t.is_control for t in get_skill_executor_tools())

    def test_ctx_is_excluded_from_the_schema(self):
        for t in get_skill_executor_tools():
            assert "ctx" not in t.input_schema.properties

    def test_decorator_registers_and_wraps(self):
        before = len(se._SKILL_EXECUTOR_TOOLS)

        @skill_executor_tool
        def sample_skill_tool(x: str = "", *, ctx=None) -> ToolResult:
            """Docs."""
            return ToolResult(content=f"got {x}")

        try:
            assert len(se._SKILL_EXECUTOR_TOOLS) == before + 1
            assert sample_skill_tool.handler({"x": "v"}, None).content == "got v"
            assert sample_skill_tool.description == "Docs."
        finally:
            se._SKILL_EXECUTOR_TOOLS.remove(sample_skill_tool)


class TestSkillNameResolution:
    def test_missing_task_raises(self, registry):
        with pytest.raises(AppError) as e:
            get_skill_files.handler({}, CallContext(session_id="s1"))
        assert e.value.code == "MISSING_SKILL_NAME"

    def test_no_ctx_raises(self, registry):
        with pytest.raises(AppError) as e:
            get_skill_files.handler({}, None)
        assert e.value.code == "MISSING_SKILL_NAME"

    def test_task_without_a_skill_name_raises(self, registry):
        with pytest.raises(AppError) as e:
            get_skill_files.handler({}, _ctx(skill_name=""))
        assert e.value.code == "MISSING_SKILL_NAME"

    def test_unknown_skill_raises(self, registry):
        registry.fetch_skill.return_value = None
        with pytest.raises(AppError) as e:
            get_skill_files.handler({}, _ctx())
        assert e.value.code == "SKILL_NOT_FOUND"


class TestGetSkillFiles:
    def test_lists_files_with_metadata(self, registry):
        registry.fetch_skill.return_value = _skill()
        r = get_skill_files.handler({}, _ctx())
        assert r.content == "a.py\nb.py"
        assert r.metadata == {"skill": "pptx", "source": "local"}

    def test_defaults_are_forwarded(self, registry):
        skill = _skill()
        registry.fetch_skill.return_value = skill
        get_skill_files.handler({}, _ctx())
        assert skill.get_files.call_args.args[:2] == ("**/*", 200)

    def test_pattern_and_limit_are_forwarded(self, registry):
        skill = _skill()
        registry.fetch_skill.return_value = skill
        get_skill_files.handler({"pattern": "scripts/*.py", "limit": 5}, _ctx())
        assert skill.get_files.call_args.args[:2] == ("scripts/*.py", 5)


class TestLoadSkillReference:
    def test_reads_the_file(self, registry):
        registry.fetch_skill.return_value = _skill()
        r = load_skill_reference.handler({"reference_path": "references/bg.md"}, _ctx())
        assert r.content == "reference body"
        assert r.metadata == {"skill": "pptx", "path": "references/bg.md"}

    def test_blank_path_raises(self, registry):
        with pytest.raises(AppError) as e:
            load_skill_reference.handler({"reference_path": ""}, _ctx())
        assert e.value.code == "INVALID_ARGUMENT"

    def test_missing_path_raises(self, registry):
        with pytest.raises(AppError) as e:
            load_skill_reference.handler({}, _ctx())
        assert e.value.code == "INVALID_ARGUMENT"


class TestExecSkillScript:
    def test_returns_the_skill_result(self, registry):
        registry.fetch_skill.return_value = _skill()
        r = exec_skill_script.handler({"script_path": "scripts/run.py"}, _ctx())
        assert r.content == "script output"

    def test_args_are_forwarded(self, registry):
        skill = _skill()
        registry.fetch_skill.return_value = skill
        exec_skill_script.handler({"script_path": "s.py", "args": "--flag x"}, _ctx())
        assert skill.exec_script.call_args.args[:2] == ("s.py", "--flag x")

    def test_default_args_are_empty(self, registry):
        skill = _skill()
        registry.fetch_skill.return_value = skill
        exec_skill_script.handler({"script_path": "s.py"}, _ctx())
        assert skill.exec_script.call_args.args[1] == ""

    def test_blank_path_raises(self, registry):
        with pytest.raises(AppError) as e:
            exec_skill_script.handler({"script_path": ""}, _ctx())
        assert e.value.code == "INVALID_ARGUMENT"

    def test_missing_path_raises(self, registry):
        with pytest.raises(AppError) as e:
            exec_skill_script.handler({}, _ctx())
        assert e.value.code == "INVALID_ARGUMENT"
