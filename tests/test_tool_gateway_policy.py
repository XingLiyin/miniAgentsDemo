"""Tests for the tool-call path: PolicyEngine / PolicyRule / ToolGateway,
plus the MemoryCompactionAgent (app/runtime/).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.common.errors import AppError
from app.domain.models.agent import AgentCapability
from app.domain.models.tool_call import ToolCall
from app.llm.types import InputSchema, LLMMessage, StreamChunk, ToolCallBlock
from app.runtime import memory_compaction as mc_mod
from app.runtime.memory_compaction import MemoryCompactionAgent, _build_user_message, _stream
from app.runtime.policy_engine import PolicyEngine
from app.runtime.policy_rule import (
    BashExecGuardRule, GlobalRule, PolicyRule, ToolRule, WhitelistRule,
)
from app.runtime.tool_gateway import ToolGateway, _redact
from app.tools.types import CallContext, ToolDefinition, ToolResult


def _tool_def(name="read", handler=None, is_control=False) -> ToolDefinition:
    return ToolDefinition(
        name=name, description="d",
        input_schema=InputSchema(properties={}, require=[]),
        handler=handler or (lambda args, ctx=None: ToolResult(content="ok")),
        is_control=is_control,
    )


def _ctx(session_id="s1", agent_id="a1", task=None) -> CallContext:
    return CallContext(session_id=session_id, agent_id=agent_id, task=task)


# ── ToolCall model ────────────────────────────────────────────────────────────

class TestToolCallModel:
    def test_roundtrip(self):
        tc = ToolCall(id="t", session_id="s", task_id="tk", agent_id="a",
                      tool_name="read", status="SUCCEEDED", arguments={"p": 1},
                      result="out", error=None, started_at="t0", finished_at="t1")
        assert ToolCall.from_dict(tc.to_dict()) == tc

    def test_defaults(self):
        tc = ToolCall(id="t", session_id="s", task_id="", agent_id="a",
                      tool_name="read", status="RUNNING")
        assert tc.arguments == {} and tc.result is None and tc.started_at == ""

    def test_from_dict_tolerates_missing_optionals(self):
        tc = ToolCall.from_dict({
            "id": "t", "session_id": "s", "task_id": "", "agent_id": "a",
            "tool_name": "read", "status": "RUNNING"})
        assert tc.arguments == {} and tc.finished_at == ""


# ── PolicyEngine ──────────────────────────────────────────────────────────────

class _RecordingGlobal(GlobalRule):
    def __init__(self):
        self.calls = []

    def check(self, capability, tool_name, arguments, ctx):
        self.calls.append(tool_name)


class _RecordingTool(ToolRule):
    def __init__(self, *tools):
        super().__init__(*tools)
        self.calls = []

    def check(self, capability, tool_name, arguments, ctx):
        self.calls.append(tool_name)


class TestPolicyEngine:
    def test_global_rules_always_run(self):
        g = _RecordingGlobal()
        PolicyEngine([g]).authorize(AgentCapability(), "anything")
        assert g.calls == ["anything"]

    def test_tool_rules_only_run_for_their_tools(self):
        t = _RecordingTool("bash_exec")
        eng = PolicyEngine([t])
        eng.authorize(AgentCapability(), "read")
        assert t.calls == []
        eng.authorize(AgentCapability(), "bash_exec")
        assert t.calls == ["bash_exec"]

    def test_tool_rule_registered_for_several_tools(self):
        t = _RecordingTool("a", "b")
        eng = PolicyEngine([t])
        eng.authorize(AgentCapability(), "a")
        eng.authorize(AgentCapability(), "b")
        assert t.calls == ["a", "b"]

    def test_arguments_default_to_empty_dict(self):
        seen = {}

        class Spy(GlobalRule):
            def check(self, capability, tool_name, arguments, ctx):
                seen["args"] = arguments

        PolicyEngine([Spy()]).authorize(AgentCapability(), "t")
        assert seen["args"] == {}

    def test_arguments_forwarded(self):
        seen = {}

        class Spy(GlobalRule):
            def check(self, capability, tool_name, arguments, ctx):
                seen["args"] = arguments

        PolicyEngine([Spy()]).authorize(AgentCapability(), "t", {"x": 1})
        assert seen["args"] == {"x": 1}

    def test_rule_failure_propagates(self):
        class Deny(GlobalRule):
            def check(self, capability, tool_name, arguments, ctx):
                raise AppError("DENIED", "no")

        with pytest.raises(AppError) as e:
            PolicyEngine([Deny()]).authorize(AgentCapability(), "t")
        assert e.value.code == "DENIED"

    def test_unclassified_rule_is_ignored(self):
        class Odd(PolicyRule):
            def check(self, capability, tool_name, arguments, ctx):
                raise AssertionError("must not run")

        PolicyEngine([Odd()]).authorize(AgentCapability(), "t")

    def test_default_installs_whitelist_rule(self):
        eng = PolicyEngine.default(MagicMock())
        assert len(eng._global_rules) == 1
        assert isinstance(eng._global_rules[0], WhitelistRule)

    def test_empty_engine_allows_everything(self):
        PolicyEngine([]).authorize(AgentCapability(), "anything")

    def test_policy_rule_is_abstract(self):
        with pytest.raises(TypeError):
            PolicyRule()


# ── WhitelistRule ─────────────────────────────────────────────────────────────

class TestWhitelistRule:
    def _registry(self, registered=True, server_tools=None):
        reg = MagicMock()
        reg.is_registered.return_value = registered
        reg.get_server_tool_names.side_effect = lambda s: (server_tools or {}).get(s, [])
        return reg

    def test_unregistered_tool_rejected(self):
        rule = WhitelistRule(self._registry(registered=False))
        with pytest.raises(AppError) as e:
            rule.check(AgentCapability(tools=["read"]), "read", {}, None)
        assert e.value.code == "TOOL_NOT_FOUND"

    def test_allowed_by_direct_tool_list(self):
        rule = WhitelistRule(self._registry())
        rule.check(AgentCapability(tools=["read"]), "read", {}, None)

    def test_allowed_via_authorized_mcp_server(self):
        rule = WhitelistRule(self._registry(server_tools={"srv": ["remote"]}))
        rule.check(AgentCapability(mcp_servers=["srv"]), "remote", {}, None)

    def test_rejected_when_not_in_any_list(self):
        rule = WhitelistRule(self._registry())
        with pytest.raises(AppError) as e:
            rule.check(AgentCapability(tools=["other"]), "read", {}, _ctx(agent_id="a7"))
        assert e.value.code == "TOOL_NOT_AUTHORIZED"
        assert "a7" in e.value.message

    def test_unknown_agent_id_without_ctx(self):
        rule = WhitelistRule(self._registry())
        with pytest.raises(AppError) as e:
            rule.check(AgentCapability(), "read", {}, None)
        assert "unknown" in e.value.message

    def test_none_mcp_servers_tolerated(self):
        rule = WhitelistRule(self._registry())
        cap = AgentCapability(tools=[])
        cap.mcp_servers = None
        with pytest.raises(AppError):
            rule.check(cap, "read", {}, None)

    def test_wrong_server_does_not_authorize(self):
        rule = WhitelistRule(self._registry(server_tools={"other": ["remote"]}))
        with pytest.raises(AppError):
            rule.check(AgentCapability(mcp_servers=["srv"]), "remote", {}, None)


# ── BashExecGuardRule ─────────────────────────────────────────────────────────

class TestBashExecGuardRule:
    def test_registered_for_bash_exec_only(self):
        assert BashExecGuardRule(MagicMock())._tools == frozenset({"bash_exec"})

    def test_no_session_skips_the_prompt(self):
        svc = MagicMock()
        BashExecGuardRule(svc).check(AgentCapability(), "bash_exec", {"command": "ls"}, None)
        assert not svc.transition.called

    def _run(self, answer, svc=None, arguments=None, push_error=False):
        svc = svc or MagicMock()
        store = MagicMock()
        store.wait.return_value = answer
        bus = MagicMock()
        if push_error:
            bus.push.side_effect = RuntimeError("no subscribers")
        with patch("app.storage.file.hitl_store.get_hitl_store", return_value=store), \
             patch("app.common.sse_bus.get_sse_bus", return_value=bus):
            BashExecGuardRule(svc).check(
                AgentCapability(), "bash_exec",
                arguments if arguments is not None else {"command": "ls -la"}, _ctx())
        return svc, store, bus

    def test_approval_passes_and_restores_running(self):
        svc, store, bus = self._run("approved")
        assert svc.transition.call_args_list[0].args == ("s1", "WAITING_INPUT")
        assert svc.transition.call_args_list[1].args == ("s1", "RUNNING")
        assert bus.push.call_args.args[1]["type"] == "bash_exec_confirm"
        assert bus.push.call_args.args[1]["command"] == "ls -la"
        assert store.wait.call_args.args[3] == "bash_exec_confirm"

    def test_approval_is_case_and_space_insensitive(self):
        self._run("  APPROVED \n")

    def test_rejection_raises(self):
        with pytest.raises(AppError) as e:
            self._run("too dangerous")
        assert e.value.code == "TOOL_EXECUTION_REJECTED"
        assert "too dangerous" in e.value.message

    def test_empty_answer_is_a_rejection(self):
        with pytest.raises(AppError) as e:
            self._run("")
        assert "no reason given" in e.value.message

    def test_sse_push_failure_does_not_block(self):
        self._run("approved", push_error=True)

    def test_missing_command_argument(self):
        with pytest.raises(AppError):
            self._run("no", arguments={})

    def test_session_returns_to_running_even_on_rejection(self):
        svc = MagicMock()
        with pytest.raises(AppError):
            self._run("no", svc=svc)
        assert svc.transition.call_args_list[-1].args == ("s1", "RUNNING")


# ── _redact ───────────────────────────────────────────────────────────────────

class TestRedact:
    def test_masks_sensitive_headers(self):
        out = _redact({"headers": {"Authorization": "Bearer x", "X-Api-Key": "k",
                                   "Cookie": "c", "Accept": "json"}})
        assert out["headers"] == {"Authorization": "***", "X-Api-Key": "***",
                                 "Cookie": "***", "Accept": "json"}

    def test_leaves_other_arguments_alone(self):
        assert _redact({"command": "ls"}) == {"command": "ls"}

    def test_non_dict_headers_untouched(self):
        assert _redact({"headers": "raw"}) == {"headers": "raw"}

    def test_does_not_mutate_input(self):
        src = {"headers": {"Authorization": "secret"}}
        _redact(src)
        assert src["headers"]["Authorization"] == "secret"


# ── ToolGateway ───────────────────────────────────────────────────────────────

class TestToolGateway:
    def _gateway(self, tool=None, policy=None):
        registry = MagicMock()
        registry.get.return_value = tool or _tool_def()
        registry.get_from_capability.return_value = tool or _tool_def()
        store = MagicMock()
        gw = ToolGateway(policy or PolicyEngine([]), registry, store)
        return gw, registry, store

    def test_successful_call_returns_result(self):
        gw, _, _ = self._gateway()
        assert gw.call("read", {}, None, "task-1").content == "ok"

    def test_writes_running_then_succeeded_audit(self):
        gw, _, store = self._gateway()
        gw.call("read", {"p": 1}, None, "task-1", _ctx())
        assert store.append.call_count == 2
        first = store.append.call_args_list[0].args[1]
        second = store.append.call_args_list[1].args[1]
        assert first["status"] == "RUNNING" and second["status"] == "SUCCEEDED"
        assert first["id"] == second["id"]          # same call id
        assert second["task_id"] == "task-1" and second["agent_id"] == "a1"
        assert second["finished_at"]

    def test_audit_arguments_are_redacted(self):
        gw, _, store = self._gateway()
        gw.call("http", {"headers": {"Authorization": "Bearer s"}}, None, "t", _ctx())
        assert store.append.call_args.args[1]["arguments"]["headers"]["Authorization"] == "***"

    def test_capability_none_skips_authorize_and_uses_plain_get(self):
        policy = MagicMock()
        gw, registry, _ = self._gateway(policy=policy)
        gw.call("read", {}, None, "t", _ctx())
        assert not policy.authorize.called
        assert registry.get.called and not registry.get_from_capability.called

    def test_capability_given_authorizes_and_scopes_lookup(self):
        policy = MagicMock()
        gw, registry, _ = self._gateway(policy=policy)
        cap = AgentCapability(tools=["read"])
        gw.call("read", {}, cap, "t", _ctx())
        assert policy.authorize.called
        assert registry.get_from_capability.called

    def test_authorization_failure_propagates(self):
        policy = MagicMock()
        policy.authorize.side_effect = AppError("TOOL_NOT_AUTHORIZED", "nope")
        gw, _, store = self._gateway(policy=policy)
        with pytest.raises(AppError):
            gw.call("read", {}, AgentCapability(), "t", _ctx())
        assert not store.append.called      # rejected before any audit row

    def test_app_error_from_handler_becomes_error_result(self):
        def boom(args, ctx=None):
            raise AppError("FILE_NOT_FOUND", "missing file")

        gw, _, store = self._gateway(tool=_tool_def(handler=boom))
        result = gw.call("read", {}, None, "t", _ctx())
        assert result.is_error and result.error_code == "FILE_NOT_FOUND"
        assert result.content == "missing file"
        assert store.append.call_args.args[1]["status"] == "FAILED"
        assert store.append.call_args.args[1]["error"] == "missing file"

    def test_app_error_without_message_falls_back_to_code(self):
        def boom(args, ctx=None):
            raise AppError("SOME_CODE", "")

        gw, _, _ = self._gateway(tool=_tool_def(handler=boom))
        assert gw.call("read", {}, None, "t", _ctx()).content == "SOME_CODE"

    def test_unexpected_exception_becomes_tool_exec_error(self):
        def boom(args, ctx=None):
            raise ValueError("kaboom")

        gw, _, store = self._gateway(tool=_tool_def(handler=boom))
        result = gw.call("read", {}, None, "t", _ctx())
        assert result.is_error and result.error_code == "TOOL_EXEC_ERROR"
        assert result.content == "kaboom"
        assert store.append.call_args.args[1]["status"] == "FAILED"

    def test_missing_tool_is_reported_as_error_result(self):
        gw, registry, _ = self._gateway()
        registry.get.side_effect = AppError("TOOL_NOT_FOUND", "no such tool")
        assert gw.call("ghost", {}, None, "t", _ctx()).error_code == "TOOL_NOT_FOUND"

    def test_ctx_is_forwarded_to_the_handler(self):
        seen = {}
        gw, _, _ = self._gateway(
            tool=_tool_def(handler=lambda a, c=None: seen.update(ctx=c) or ToolResult()))
        ctx = _ctx()
        gw.call("read", {}, None, "t", ctx)
        assert seen["ctx"] is ctx

    def test_synthesises_ctx_when_none_given(self):
        seen = {}
        gw, _, _ = self._gateway(
            tool=_tool_def(handler=lambda a, c=None: seen.update(ctx=c) or ToolResult()))
        gw.call("read", {}, None, "t")
        assert isinstance(seen["ctx"], CallContext)
        assert seen["ctx"].session_id == "" and seen["ctx"].agent_id == ""

    def test_audit_result_is_truncated_to_200_chars(self):
        gw, _, store = self._gateway(
            tool=_tool_def(handler=lambda a, c=None: ToolResult(content="x" * 500)))
        gw.call("read", {}, None, "t", _ctx())
        assert len(store.append.call_args.args[1]["result"]) == 200

    def test_empty_result_recorded_as_none(self):
        gw, _, store = self._gateway(
            tool=_tool_def(handler=lambda a, c=None: ToolResult(content="")))
        gw.call("read", {}, None, "t", _ctx())
        assert store.append.call_args.args[1]["result"] is None

    def test_long_output_is_truncated_in_the_returned_result(self):
        big = "y" * 2000
        gw, _, _ = self._gateway(
            tool=_tool_def(handler=lambda a, c=None: ToolResult(content=big)))
        with patch("app.runtime.tool_gateway.get_settings",
                   return_value=SimpleNamespace(http_response_limit_bytes=100)):
            result = gw.call("read", {}, None, "t", _ctx())
        assert "[output truncated at 100 bytes]" in result.content
        assert len(result.content) < len(big)

    def test_short_output_is_not_truncated(self):
        gw, _, _ = self._gateway(
            tool=_tool_def(handler=lambda a, c=None: ToolResult(content="small")))
        with patch("app.runtime.tool_gateway.get_settings",
                   return_value=SimpleNamespace(http_response_limit_bytes=100)):
            assert gw.call("read", {}, None, "t", _ctx()).content == "small"

    def test_multimodal_content_is_not_truncated(self):
        blocks = [{"type": "text", "text": "z" * 2000}]
        gw, _, _ = self._gateway(
            tool=_tool_def(handler=lambda a, c=None: ToolResult(content=blocks)))
        with patch("app.runtime.tool_gateway.get_settings",
                   return_value=SimpleNamespace(http_response_limit_bytes=10)):
            assert gw.call("read", {}, None, "t", _ctx()).content == blocks

    def test_truncation_preserves_error_fields(self):
        gw, _, _ = self._gateway(tool=_tool_def(
            handler=lambda a, c=None: ToolResult(
                content="q" * 500, is_error=True, error_code="E", metadata={"m": 1})))
        with patch("app.runtime.tool_gateway.get_settings",
                   return_value=SimpleNamespace(http_response_limit_bytes=50)):
            r = gw.call("read", {}, None, "t", _ctx())
        assert r.is_error and r.error_code == "E" and r.metadata == {"m": 1}


class TestToolGatewaySSE:
    def _call(self, tool=None, ctx=None, bus=None):
        registry = MagicMock()
        registry.get.return_value = tool or _tool_def()
        bus = bus or MagicMock()
        gw = ToolGateway(PolicyEngine([]), registry, MagicMock())
        with patch("app.common.sse_bus.get_sse_bus", return_value=bus):
            result = gw.call("read", {"p": 1}, None, "t", ctx if ctx is not None else _ctx())
        return bus, result

    def test_pushes_tool_call_event(self):
        bus, _ = self._call()
        payload = bus.push.call_args.args[1]
        assert payload["type"] == "tool_call"
        assert payload["tool_name"] == "read"
        assert payload["arguments"] == {"p": 1}
        assert payload["result"] == "ok"
        assert payload["is_error"] is False

    def test_control_tool_gets_its_own_event_type(self):
        bus, _ = self._call(tool=_tool_def(is_control=True))
        assert bus.push.call_args.args[1]["type"] == "control_tool_call"

    def test_daemon_task_prefixes_the_event_type(self):
        task = SimpleNamespace(settings={"_daemon": True})
        bus, _ = self._call(ctx=_ctx(task=task))
        assert bus.push.call_args.args[1]["type"] == "daemon_tool_call"

    def test_daemon_control_tool(self):
        task = SimpleNamespace(settings={"_daemon": True})
        bus, _ = self._call(tool=_tool_def(is_control=True), ctx=_ctx(task=task))
        assert bus.push.call_args.args[1]["type"] == "daemon_control_tool_call"

    def test_non_daemon_task_settings(self):
        task = SimpleNamespace(settings={"other": 1})
        bus, _ = self._call(ctx=_ctx(task=task))
        assert bus.push.call_args.args[1]["type"] == "tool_call"

    def test_task_without_settings(self):
        task = SimpleNamespace(settings=None)
        bus, _ = self._call(ctx=_ctx(task=task))
        assert bus.push.call_args.args[1]["type"] == "tool_call"

    def test_multimodal_result_flattened_to_text(self):
        blocks = [{"type": "text", "text": "flat"}, {"type": "image", "data": "x"}]
        bus, _ = self._call(tool=_tool_def(handler=lambda a, c=None: ToolResult(content=blocks)))
        assert bus.push.call_args.args[1]["result"] == "flat"

    def test_no_session_id_means_no_push(self):
        bus, _ = self._call(ctx=_ctx(session_id=""))
        assert not bus.push.called

    def test_push_failure_does_not_break_the_call(self):
        bus = MagicMock()
        bus.push.side_effect = RuntimeError("bus down")
        _, result = self._call(bus=bus)
        assert result.content == "ok"


# ── MemoryCompactionAgent ─────────────────────────────────────────────────────

class _FakeLLM:
    """Streams a scripted sequence of (text, tool_calls) turns."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    def stream_message(self, messages, system_prompt=None, tools=None):
        self.calls.append({"messages": list(messages), "system_prompt": system_prompt,
                           "tools": tools})
        text, tool_calls = self.turns.pop(0) if self.turns else ("", [])
        yield StreamChunk(text_delta=text)
        for i, tc in enumerate(tool_calls):
            yield StreamChunk(tool_call_delta={"index": i, "id": tc[0], "name": tc[1],
                                               "arguments": "{}"})
        yield StreamChunk(is_done=True)

    def parse_stream_acc(self, full_text, acc, images=None, usage=None):
        calls = [ToolCallBlock(type="tool_call", id=b.get("id", ""), name=b.get("name", ""),
                               input={}) for b in acc.values()]
        return SimpleNamespace(text=full_text, tool_calls=calls, blocks=[])


def _agent(turns, keep_last=6, gateway=None):
    registry = MagicMock()
    registry.to_llm_tools.return_value = ["TOOLS"]
    gw = gateway or MagicMock()
    gw.call.return_value = ToolResult(content="tool output")
    llm = _FakeLLM(turns)
    return MemoryCompactionAgent(llm, registry, gw, "SOUL", ["read"], keep_last=keep_last), llm, gw


class TestBuildUserMessage:
    def test_includes_goal_and_history(self):
        out = _build_user_message([{"role": "user", "content": "hi"}], "ship it")
        assert "ship it" in out and "[user]: hi" in out

    def test_without_goal(self):
        out = _build_user_message([{"role": "user", "content": "hi"}], "")
        assert "会话目标" not in out

    def test_flattens_multimodal_content(self):
        out = _build_user_message(
            [{"role": "user", "content": [{"type": "text", "text": "flat"}]}], "")
        assert "[user]: flat" in out

    def test_tolerates_missing_keys(self):
        assert "[]: " in _build_user_message([{}], "")


class TestStreamHelper:
    def test_accumulates_text_and_tool_calls(self):
        llm = _FakeLLM([("hello", [("tc1", "read")])])
        text, acc = _stream(llm, [], "sys", [])
        assert text == "hello"
        assert acc[0]["name"] == "read"

    def test_error_chunk_raises_app_error(self):
        class Boom:
            def stream_message(self, **kw):
                yield StreamChunk(is_done=True, error="rate limited")

        with pytest.raises(AppError) as e:
            _stream(Boom(), [], "sys", [])
        assert e.value.code == "LLM_API_ERROR"

    def test_done_without_error_is_fine(self):
        class Fine:
            def stream_message(self, **kw):
                yield StreamChunk(text_delta="t")
                yield StreamChunk(is_done=True)

        assert _stream(Fine(), [], "sys", [])[0] == "t"


class TestCompact:
    def test_empty_messages_returned_as_is(self):
        ag, llm, _ = _agent([])
        assert ag.compact([]) == ([], "")
        assert llm.calls == []

    def test_splits_at_keep_last(self):
        ag, llm, _ = _agent([("SUMMARY", [])], keep_last=2)
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(5)]
        kept, summary = ag.compact(msgs)
        assert [m["content"] for m in kept] == ["m3", "m4"]
        assert summary == "SUMMARY"
        assert "m0" in llm.calls[0]["messages"][0].content
        assert "m4" not in llm.calls[0]["messages"][0].content

    def test_short_history_compacts_everything(self):
        ag, _, _ = _agent([("S", [])], keep_last=6)
        kept, summary = ag.compact([{"role": "user", "content": "only"}])
        assert kept == [] and summary == "S"

    def test_exactly_keep_last_compacts_everything(self):
        ag, _, _ = _agent([("S", [])], keep_last=2)
        kept, _ = ag.compact([{"role": "user", "content": "a"}, {"role": "user", "content": "b"}])
        assert kept == []

    def test_llm_failure_falls_back_to_truncation(self):
        ag, _, _ = _agent([], keep_last=1)

        def boom(**kw):
            raise RuntimeError("llm down")

        ag._llm_client.stream_message = boom
        msgs = [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]
        kept, summary = ag.compact(msgs)
        assert [m["content"] for m in kept] == ["b"] and summary == ""

    def test_blank_summary_returns_empty_string(self):
        ag, _, _ = _agent([("", [])], keep_last=1)
        kept, summary = ag.compact([{"role": "user", "content": "a"},
                                    {"role": "user", "content": "b"}])
        assert summary == "" and [m["content"] for m in kept] == ["b"]

    def test_soul_and_tools_passed_to_the_llm(self):
        ag, llm, _ = _agent([("S", [])], keep_last=1)
        ag.compact([{"role": "user", "content": "a"}, {"role": "user", "content": "b"}])
        assert llm.calls[0]["system_prompt"] == "SOUL"
        assert llm.calls[0]["tools"] == ["TOOLS"]

    def test_session_goal_reaches_the_prompt(self):
        ag, llm, _ = _agent([("S", [])], keep_last=1)
        ag.compact([{"role": "user", "content": "a"}, {"role": "user", "content": "b"}],
                   session_goal="the goal")
        assert "the goal" in llm.calls[0]["messages"][0].content

    def test_tool_calls_are_executed_then_loop_continues(self):
        ag, llm, gw = _agent([("thinking", [("tc1", "read")]), ("FINAL", [])], keep_last=1)
        kept, summary = ag.compact([{"role": "user", "content": "a"},
                                    {"role": "user", "content": "b"}])
        assert summary == "FINAL"
        assert gw.call.call_args.args[0] == "read"
        assert len(llm.calls) == 2

    def test_tool_context_carries_session_and_working_dir(self):
        ag, _, gw = _agent([("t", [("tc1", "read")]), ("done", [])], keep_last=1)
        ag.compact([{"role": "user", "content": "a"}, {"role": "user", "content": "b"}],
                   working_dir="/work", session_id="s9", agent_id="a9")
        ctx = gw.call.call_args.kwargs["ctx"]
        assert ctx.session_id == "s9" and ctx.agent_id == "a9"
        assert ctx.working_dir == "/work"

    def test_stops_at_max_tool_rounds(self, monkeypatch):
        monkeypatch.setattr(mc_mod, "_MAX_TOOL_ROUNDS", 3)
        ag, llm, gw = _agent([("t", [("tc", "read")])] * 10, keep_last=1)
        ag.compact([{"role": "user", "content": "a"}, {"role": "user", "content": "b"}])
        assert len(llm.calls) == 3
