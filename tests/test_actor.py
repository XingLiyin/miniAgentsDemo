"""Tests for Actor (app/runtime/actor.py): the unified plan/act tool-use loop.

The LLM is a scripted fake stream, the ToolGateway is a mock, and SSE is captured
so the event stream can be asserted without a bus.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.common.errors import AppError
from app.common.interrupt import AgentInterruptedError, InterruptContext
from app.domain.models.agent import Agent, AgentCapability, LoopGuard
from app.domain.models.session import Session
from app.domain.models.task import Task
from app.llm.types import ImageBlock, InputSchema, LLMTool, LLMUsage, StreamChunk
from app.runtime.actor import Actor, _compute_max_tokens
from app.runtime.types import ContextResource, ReasoningContext
from app.tools.types import ToolResult


# ── fixtures / builders ───────────────────────────────────────────────────────

def _task(**kw) -> Task:
    base = dict(id="t1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                status="ACTIVE", user_prompt="do X", title="Do X", description="d",
                created_at="", updated_at="")
    base.update(kw)
    return Task(**base)


def _agent(max_rounds=3, **kw) -> Agent:
    base = dict(id="a1", session_id="s1", template_id="tpl", name="agent", status="RUNNING",
                actor=AgentCapability(tools=["read"]),
                loop_guard=LoopGuard(actor_max_tool_rounds=max_rounds))
    base.update(kw)
    return Agent(**base)


def _session(**kw) -> Session:
    base = dict(id="s1", user_prompt="p", goal="g", status="RUNNING",
                template_id=None, root_agent_id="a1")
    base.update(kw)
    return Session(**base)


def _tool_resource(name="read") -> ContextResource:
    return ContextResource(
        name=name, description="d", kind="tool",
        llm_tool=LLMTool(name=name, input_schema=InputSchema(properties={}, require=[])),
    )


def _ctx(**kw) -> ReasoningContext:
    base = dict(goal="g", recent_messages=[], blackboard_snippets=[],
                soul="SOUL", actor_resources=[_tool_resource()])
    base.update(kw)
    return ReasoningContext(**base)


class _FakeLLM:
    """Streams scripted turns. Each turn is a dict of chunk instructions."""

    context_limit = 100_000
    max_output_tokens = 4096

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    def stream_message(self, messages, system_prompt=None, tools=None, max_tokens=None):
        self.calls.append({"messages": list(messages), "system_prompt": system_prompt,
                           "tools": tools, "max_tokens": max_tokens})
        turn = self.turns.pop(0) if self.turns else {}
        for chunk in turn.get("chunks", [StreamChunk(text_delta="done"),
                                         StreamChunk(is_done=True, finish_reason="stop")]):
            yield chunk

    def parse_stream_acc(self, full_text, acc, images=None, usage=None):
        from app.llm.types import ToolCallBlock
        calls = [
            ToolCallBlock(type="tool_call", id=b.get("id", ""), name=b.get("name", ""),
                          input=b.get("input", {}))
            for b in acc.values()
        ]
        return SimpleNamespace(text=full_text, tool_calls=calls, blocks=[], images=images or [])


def _text_turn(text="answer", usage=None):
    chunks = [StreamChunk(text_delta=text)]
    chunks.append(StreamChunk(is_done=True, finish_reason="stop", usage=usage))
    return {"chunks": chunks}


def _tool_turn(tool="read", tc_id="tc1", text="calling", usage=None):
    return {"chunks": [
        StreamChunk(text_delta=text),
        StreamChunk(tool_call_delta={"index": 0, "id": tc_id, "name": tool, "input": {}}),
        StreamChunk(is_done=True, finish_reason="tool_calls", usage=usage),
    ]}


def _actor(turns, gateway=None, task_svc=None, session_svc=None, sse=None):
    gw = gateway or MagicMock()
    gw.call.return_value = ToolResult(content="tool ok")
    llm = _FakeLLM(turns)
    ac = Actor(gw, task_svc or MagicMock(), session_svc)
    ac._resolve_llm_client = lambda session: llm
    bus = sse if sse is not None else MagicMock()
    ac._get_sse = lambda session_id: bus
    return ac, llm, gw, bus


def _events(bus) -> list[dict]:
    return [c.args[1] for c in bus.push.call_args_list]


def _types(bus) -> list[str]:
    return [e["type"] for e in _events(bus)]


# ── _compute_max_tokens ───────────────────────────────────────────────────────

class TestComputeMaxTokens:
    def test_capped_by_max_output_tokens(self):
        llm = SimpleNamespace(context_limit=100_000, max_output_tokens=4096)
        assert _compute_max_tokens(llm, 1000) == 4096

    def test_capped_by_remaining_context(self):
        llm = SimpleNamespace(context_limit=5000, max_output_tokens=4096)
        assert _compute_max_tokens(llm, 4000) == 1000

    def test_never_below_one(self):
        llm = SimpleNamespace(context_limit=1000, max_output_tokens=4096)
        assert _compute_max_tokens(llm, 5000) == 1


# ── single-round text path ────────────────────────────────────────────────────

class TestTextPath:
    def test_no_tool_calls_exits_normally(self):
        ac, llm, _, _ = _actor([_text_turn("the answer")])
        r = ac.act(_task(), _ctx(), _agent(), _session())
        assert r.success and r.output == "the answer"
        assert r.exit_reason == "normal" and r.actor_mode == "text"
        assert r.error is None
        assert len(r.conversation_turns) == 1
        assert len(llm.calls) == 1

    def test_system_prompt_and_tools_forwarded(self):
        ac, llm, _, _ = _actor([_text_turn()])
        ac.act(_task(), _ctx(), _agent(), _session())
        assert "SOUL" in llm.calls[0]["system_prompt"]
        assert [t.name for t in llm.calls[0]["tools"]] == ["read"]

    def test_resources_without_llm_tool_are_skipped(self):
        ctx = _ctx(actor_resources=[
            _tool_resource(), ContextResource(name="sk", description="", kind="skill")])
        ac, llm, _, _ = _actor([_text_turn()])
        ac.act(_task(), ctx, _agent(), _session())
        assert len(llm.calls[0]["tools"]) == 1

    def test_pending_task_is_activated_first(self):
        svc = MagicMock()
        ac, _, _, _ = _actor([_text_turn()], task_svc=svc)
        ac.act(_task(status="PENDING"), _ctx(), _agent(), _session())
        svc.transition.assert_called_once_with("t1", "ACTIVE", "s1")

    def test_active_task_is_not_transitioned(self):
        svc = MagicMock()
        ac, _, _, _ = _actor([_text_turn()], task_svc=svc)
        ac.act(_task(status="ACTIVE"), _ctx(), _agent(), _session())
        assert not svc.transition.called

    def test_conversation_turn_records_round_and_text(self):
        ac, _, _, _ = _actor([_text_turn("txt")])
        turn = ac.act(_task(), _ctx(), _agent(), _session()).conversation_turns[0]
        assert turn.round == 0 and turn.llm_text == "txt" and turn.tool_calls == []


# ── tool-use loop ─────────────────────────────────────────────────────────────

class TestToolLoop:
    def test_tool_then_text_runs_two_rounds(self):
        ac, llm, gw, _ = _actor([_tool_turn(), _text_turn("final")])
        r = ac.act(_task(), _ctx(), _agent(), _session())
        assert r.output == "final" and r.exit_reason == "normal"
        assert r.actor_mode == "tool_use"
        assert [tc.tool_name for tc in r.tool_calls_made] == ["read"]
        assert len(llm.calls) == 2 and len(r.conversation_turns) == 2

    def test_gateway_receives_capability_and_task(self):
        ac, _, gw, _ = _actor([_tool_turn(), _text_turn()])
        agent = _agent()
        ac.act(_task(), _ctx(), agent, _session())
        kw = gw.call.call_args.kwargs
        assert kw["tool_name"] == "read"
        assert kw["capability"] is agent.actor
        assert kw["task_id"] == "t1"
        assert kw["ctx"].agent_id == "a1" and kw["ctx"].session_id == "s1"

    def test_tool_result_recorded(self):
        gw = MagicMock()
        gw.call.return_value = ToolResult(content="file contents")
        ac, _, _, _ = _actor([_tool_turn(), _text_turn()], gateway=gw)
        rec = ac.act(_task(), _ctx(), _agent(), _session()).tool_calls_made[0]
        assert rec.result == "file contents" and rec.is_error is False
        assert rec.tool_call_id == "tc1"

    def test_multimodal_tool_result_flattened(self):
        gw = MagicMock()
        gw.call.return_value = ToolResult(content=[{"type": "text", "text": "flat"}])
        ac, _, _, _ = _actor([_tool_turn(), _text_turn()], gateway=gw)
        assert ac.act(_task(), _ctx(), _agent(), _session()).tool_calls_made[0].result == "flat"

    def test_gateway_exception_becomes_error_result(self):
        gw = MagicMock()
        gw.call.side_effect = RuntimeError("gateway exploded")
        ac, _, _, _ = _actor([_tool_turn(), _text_turn()], gateway=gw)
        r = ac.act(_task(), _ctx(), _agent(), _session())
        rec = r.tool_calls_made[0]
        assert rec.is_error and rec.result == "gateway exploded"
        assert "Tool errors: read: gateway exploded" in r.error

    def test_actor_done_flag_breaks_the_loop(self):
        task = _task()
        gw = MagicMock()

        def mark_done(**kw):
            task.actor_done = True
            return ToolResult(content="submitted")

        gw.call.side_effect = mark_done
        ac, llm, _, _ = _actor([_tool_turn(tool="submit_plan"), _text_turn()], gateway=gw)
        r = ac.act(task, _ctx(), _agent(), _session())
        assert r.exit_reason == "normal" and len(llm.calls) == 1

    def test_max_rounds_exhausted(self):
        ac, llm, _, _ = _actor([_tool_turn() for _ in range(5)])
        r = ac.act(_task(), _ctx(), _agent(max_rounds=2), _session())
        assert len(llm.calls) == 2
        assert r.exit_reason == "max_rounds" and r.success is False
        assert "max tool rounds" in r.error

    def test_error_result_from_gateway_is_flagged(self):
        gw = MagicMock()
        gw.call.return_value = ToolResult(content="denied", is_error=True, error_code="E")
        ac, _, _, _ = _actor([_tool_turn(), _text_turn()], gateway=gw)
        r = ac.act(_task(), _ctx(), _agent(), _session())
        assert r.tool_calls_made[0].is_error
        assert "Tool errors" in r.error


# ── working_dir resolution ────────────────────────────────────────────────────

class TestWorkingDir:
    def _wd_used(self, task, agent, settings_cwd=""):
        gw = MagicMock()
        gw.call.return_value = ToolResult(content="")
        ac, _, _, _ = _actor([_tool_turn(), _text_turn()], gateway=gw)
        with patch("app.runtime.actor.get_settings",
                   return_value=SimpleNamespace(bash_exec_cwd=settings_cwd)), \
             patch("app.config.settings.resolve_working_dir", side_effect=lambda r: f"abs:{r}"):
            ac.act(task, _ctx(), agent, _session())
        return gw.call.call_args.kwargs["ctx"].working_dir

    def test_task_setting_wins(self):
        task = _task(settings={"working_dir": "/from-task"})
        agent = _agent(settings={"working_dir": "/from-agent"})
        assert self._wd_used(task, agent, "/from-settings") == "abs:/from-task"

    def test_agent_setting_is_next(self):
        assert self._wd_used(_task(), _agent(settings={"working_dir": "/from-agent"}),
                             "/from-settings") == "abs:/from-agent"

    def test_falls_back_to_bash_exec_cwd(self):
        assert self._wd_used(_task(), _agent(), "/from-settings") == "abs:/from-settings"

    def test_empty_everywhere(self):
        assert self._wd_used(_task(), _agent(), "") == "abs:"


# ── token accounting ──────────────────────────────────────────────────────────

class TestTokenAccounting:
    def test_usage_recorded_on_session(self):
        svc = MagicMock()
        usage = LLMUsage(prompt_tokens=120, completion_tokens=30, total_tokens=150)
        ac, _, _, _ = _actor([_text_turn(usage=usage)], session_svc=svc)
        r = ac.act(_task(), _ctx(), _agent(), _session())
        svc.add_tokens.assert_called_once_with(
            "s1", input_tokens=120, output_tokens=30, context_tokens=120)
        assert r.context_tokens == 120

    def test_no_session_service_is_fine(self):
        usage = LLMUsage(prompt_tokens=10, completion_tokens=1)
        ac, _, _, _ = _actor([_text_turn(usage=usage)], session_svc=None)
        assert ac.act(_task(), _ctx(), _agent(), _session()).context_tokens == 10

    def test_missing_usage_leaves_context_tokens_zero(self):
        ac, _, _, _ = _actor([_text_turn()])
        assert ac.act(_task(), _ctx(), _agent(), _session()).context_tokens == 0

    def test_max_context_tokens_is_the_peak(self):
        turns = [_tool_turn(usage=LLMUsage(prompt_tokens=50)),
                 _text_turn(usage=LLMUsage(prompt_tokens=900))]
        ac, _, _, _ = _actor(turns)
        assert ac.act(_task(), _ctx(), _agent(), _session()).context_tokens == 900

    def test_max_tokens_shrinks_as_context_grows(self):
        turns = [_tool_turn(usage=LLMUsage(prompt_tokens=1000)), _text_turn()]
        ac, llm, _, _ = _actor(turns)
        ac.act(_task(), _ctx(token_estimate=0), _agent(), _session())
        assert llm.calls[0]["max_tokens"] == 4096
        assert llm.calls[1]["max_tokens"] <= 4096

    def test_context_limit_hit_stops_the_loop(self):
        usage = LLMUsage(prompt_tokens=90_000)       # >= 80% of 100_000
        ac, llm, _, _ = _actor([_tool_turn(usage=usage), _text_turn()])
        r = ac.act(_task(), _ctx(), _agent(), _session())
        assert r.exit_reason == "context_limit" and r.success is False
        assert "context limit reached" in r.error
        assert len(llm.calls) == 1

    def test_zero_context_limit_disables_the_check(self):
        ac, llm, _, _ = _actor([_tool_turn(usage=LLMUsage(prompt_tokens=10)), _text_turn()])
        llm.context_limit = 0
        r = ac.act(_task(), _ctx(), _agent(), _session())
        assert r.exit_reason == "normal" and len(llm.calls) == 2


# ── SSE events ────────────────────────────────────────────────────────────────

class TestSSEEvents:
    def test_prompt_and_text_events_pushed(self):
        ac, _, _, bus = _actor([_text_turn("hello")])
        ac.act(_task(), _ctx(current_task=_task()), _agent(), _session())
        types = _types(bus)
        assert "llm_prompt" in types and "text_delta" in types and "text_done" in types

    def test_prompt_event_carries_task_and_tool_names(self):
        ac, _, _, bus = _actor([_text_turn()])
        ac.act(_task(), _ctx(current_task=_task()), _agent(), _session())
        prompt_ev = [e for e in _events(bus) if e["type"] == "llm_prompt"][0]
        assert prompt_ev["source"] == "actor"
        assert prompt_ev["round_label"] == "actor_round_0"
        assert prompt_ev["task_id"] == "t1" and prompt_ev["agent_id"] == "a1"
        assert prompt_ev["tool_names"] == ["read"]

    def test_prompt_event_without_current_task(self):
        ac, _, _, bus = _actor([_text_turn()])
        ac.act(_task(), _ctx(current_task=None), _agent(), _session())
        prompt_ev = [e for e in _events(bus) if e["type"] == "llm_prompt"][0]
        assert prompt_ev["task_id"] == "" and prompt_ev["agent_id"] == ""

    def test_reasoning_events(self):
        turn = {"chunks": [StreamChunk(reasoning_delta="thinking"),
                           StreamChunk(text_delta="said"),
                           StreamChunk(is_done=True, finish_reason="stop")]}
        ac, _, _, bus = _actor([turn])
        ac.act(_task(), _ctx(), _agent(), _session())
        types = _types(bus)
        assert "reasoning_delta" in types and "reasoning_done" in types

    def test_image_event(self):
        img = ImageBlock(type="image", media_type="image/png", source_type="base64", data="D")
        turn = {"chunks": [StreamChunk(image=img),
                           StreamChunk(is_done=True, finish_reason="stop")]}
        ac, _, _, bus = _actor([turn])
        r = ac.act(_task(), _ctx(), _agent(), _session())
        img_ev = [e for e in _events(bus) if e["type"] == "image"][0]
        assert img_ev["media_type"] == "image/png" and img_ev["data"] == "D"
        assert r.conversation_turns[0].images == [img]

    def test_daemon_task_uses_daemon_event_types(self):
        ac, _, _, bus = _actor([_text_turn()])
        ac.act(_task(settings={"_daemon": True}), _ctx(), _agent(), _session())
        types = _types(bus)
        assert "daemon_prompt" in types and "daemon_message" in types
        assert "text_delta" not in types and "text_done" not in types

    def test_daemon_suppresses_reasoning_and_image_deltas(self):
        img = ImageBlock(type="image", media_type="image/png", source_type="base64", data="D")
        turn = {"chunks": [StreamChunk(reasoning_delta="r"), StreamChunk(image=img),
                           StreamChunk(text_delta="t"),
                           StreamChunk(is_done=True, finish_reason="stop")]}
        ac, _, _, bus = _actor([turn])
        ac.act(_task(settings={"_daemon": True}), _ctx(), _agent(), _session())
        types = _types(bus)
        assert "reasoning_delta" not in types and "image" not in types

    def test_push_failure_is_swallowed(self):
        bus = MagicMock()
        bus.push.side_effect = RuntimeError("bus down")
        ac, _, _, _ = _actor([_text_turn("ok")], sse=bus)
        assert ac.act(_task(), _ctx(), _agent(), _session()).output == "ok"

    def test_no_sse_bus_is_fine(self):
        ac, _, _, _ = _actor([_text_turn("ok")], sse=None)
        ac._get_sse = lambda sid: None
        assert ac.act(_task(), _ctx(), _agent(), _session()).output == "ok"

    def test_get_sse_returns_none_without_session_id(self):
        ac = Actor(MagicMock(), MagicMock())
        assert ac._get_sse("") is None

    def test_get_sse_returns_the_bus(self):
        ac = Actor(MagicMock(), MagicMock())
        with patch("app.common.sse_bus.get_sse_bus", return_value="BUS"):
            assert ac._get_sse("s1") == "BUS"

    def test_get_sse_swallows_import_failure(self):
        ac = Actor(MagicMock(), MagicMock())
        with patch("app.common.sse_bus.get_sse_bus", side_effect=RuntimeError("no bus")):
            assert ac._get_sse("s1") is None


# ── stream error handling ─────────────────────────────────────────────────────

class TestStreamErrors:
    def test_error_chunk_raises_llm_api_error(self):
        turn = {"chunks": [StreamChunk(is_done=True, finish_reason="api_error",
                                       error="rate limited")]}
        ac, _, _, _ = _actor([turn])
        with pytest.raises(AppError) as e:
            ac.act(_task(), _ctx(), _agent(), _session())
        assert e.value.code == "LLM_API_ERROR" and "rate limited" in e.value.message

    def test_unexpected_stream_exception_wrapped(self):
        ac, llm, _, _ = _actor([])

        def boom(**kw):
            raise ValueError("socket closed")
            yield

        llm.stream_message = boom
        with pytest.raises(AppError) as e:
            ac.act(_task(), _ctx(), _agent(), _session())
        assert e.value.code == "LLM_API_ERROR"

    def test_empty_stream_without_completion_raises(self):
        ac, _, _, _ = _actor([{"chunks": []}])
        with pytest.raises(AppError) as e:
            ac.act(_task(), _ctx(), _agent(), _session())
        assert "ended without completion" in e.value.message

    def test_reasoning_only_stream_is_accepted(self):
        turn = {"chunks": [StreamChunk(reasoning_delta="just thinking")]}
        ac, _, _, _ = _actor([turn])
        assert ac.act(_task(), _ctx(), _agent(), _session()).output == ""


# ── interrupts ────────────────────────────────────────────────────────────────

class TestInterrupts:
    def test_before_first_round(self):
        flag = threading.Event()
        flag.set()
        ac, llm, _, _ = _actor([_text_turn()])
        with pytest.raises(AgentInterruptedError) as e:
            ac.act(_task(), _ctx(), _agent(), _session(), interrupt_flag=flag)
        assert "before actor round" in str(e.value)
        assert llm.calls == []

    def test_unset_flag_does_not_interrupt(self):
        ac, _, _, _ = _actor([_text_turn("fine")])
        assert ac.act(_task(), _ctx(), _agent(), _session(),
                      interrupt_flag=threading.Event()).output == "fine"

    def test_during_llm_stream_preserves_partial_text(self):
        flag = threading.Event()

        def turn_chunks():
            yield StreamChunk(text_delta="partial")
            flag.set()
            yield StreamChunk(text_delta="never seen")

        ac, llm, _, _ = _actor([])
        llm.stream_message = lambda **kw: turn_chunks()
        with pytest.raises(AgentInterruptedError) as e:
            ac.act(_task(), _ctx(), _agent(), _session(), interrupt_flag=flag)
        assert e.value.context.partial_text == "partial"

    def test_before_a_tool_call(self):
        flag = threading.Event()
        gw = MagicMock()
        gw.call.return_value = ToolResult(content="")

        def chunks():
            yield StreamChunk(text_delta="calling")
            yield StreamChunk(tool_call_delta={"index": 0, "id": "tc", "name": "read"})
            flag.set()
            yield StreamChunk(is_done=True, finish_reason="tool_calls")

        ac, llm, _, _ = _actor([], gateway=gw)
        llm.stream_message = lambda **kw: chunks()
        with pytest.raises(AgentInterruptedError) as e:
            ac.act(_task(), _ctx(), _agent(), _session(), interrupt_flag=flag)
        assert "before tool call" in str(e.value)
        assert not gw.call.called

    def test_after_a_tool_result_keeps_the_record(self):
        flag = threading.Event()
        gw = MagicMock()

        def run_tool(**kw):
            flag.set()
            return ToolResult(content="did it")

        gw.call.side_effect = run_tool
        ac, _, _, _ = _actor([_tool_turn(), _text_turn()], gateway=gw)
        with pytest.raises(AgentInterruptedError) as e:
            ac.act(_task(), _ctx(), _agent(), _session(), interrupt_flag=flag)
        assert "after tool result" in str(e.value)
        assert [tc.tool_name for tc in e.value.context.tool_calls] == ["read"]

    def test_partial_calls_from_earlier_rounds_are_carried(self):
        flag = threading.Event()
        gw = MagicMock()
        state = {"n": 0}

        def run_tool(**kw):
            state["n"] += 1
            if state["n"] == 2:
                flag.set()
            return ToolResult(content="r")

        gw.call.side_effect = run_tool
        ac, _, _, _ = _actor([_tool_turn(tc_id="tc1"), _tool_turn(tc_id="tc2")], gateway=gw)
        with pytest.raises(AgentInterruptedError) as e:
            ac.act(_task(), _ctx(), _agent(max_rounds=4), _session(), interrupt_flag=flag)
        assert len(e.value.context.tool_calls) == 2

    def test_gateway_interrupt_propagates(self):
        gw = MagicMock()
        gw.call.side_effect = AgentInterruptedError(
            "cancelled inside gateway", context=InterruptContext())
        ac, _, _, _ = _actor([_tool_turn(), _text_turn()], gateway=gw)
        with pytest.raises(AgentInterruptedError):
            ac.act(_task(), _ctx(), _agent(), _session())


# ── result building ───────────────────────────────────────────────────────────

class TestBuildResult:
    def test_skill_mode_when_skill_name_present(self):
        ac, _, _, _ = _actor([_text_turn()])
        r = ac.act(_task(settings={"skill_name": "pptx"}), _ctx(), _agent(), _session())
        assert r.actor_mode == "skill" and r.skill_used == "pptx"

    def test_skill_mode_wins_over_tool_use(self):
        ac, _, _, _ = _actor([_tool_turn(), _text_turn()])
        r = ac.act(_task(settings={"skill_name": "pptx"}), _ctx(), _agent(), _session())
        assert r.actor_mode == "skill"

    def test_task_id_recorded(self):
        ac, _, _, _ = _actor([_text_turn()])
        assert ac.act(_task(id="t42"), _ctx(), _agent(), _session()).task_id == "t42"

    def test_several_failed_tools_are_all_listed(self):
        gw = MagicMock()
        gw.call.return_value = ToolResult(content="bad", is_error=True)
        turn = {"chunks": [
            StreamChunk(text_delta="two calls"),
            StreamChunk(tool_call_delta={"index": 0, "id": "tc1", "name": "read"}),
            StreamChunk(tool_call_delta={"index": 1, "id": "tc2", "name": "write"}),
            StreamChunk(is_done=True, finish_reason="tool_calls"),
        ]}
        ac, _, _, _ = _actor([turn, _text_turn()], gateway=gw)
        r = ac.act(_task(), _ctx(), _agent(), _session())
        assert "read: bad" in r.error and "write: bad" in r.error

    def test_max_rounds_and_tool_errors_both_reported(self):
        gw = MagicMock()
        gw.call.return_value = ToolResult(content="bad", is_error=True)
        ac, _, _, _ = _actor([_tool_turn(), _tool_turn()], gateway=gw)
        r = ac.act(_task(), _ctx(), _agent(max_rounds=2), _session())
        assert "Tool errors" in r.error and "max tool rounds" in r.error


# ── llm client resolution ─────────────────────────────────────────────────────

class TestResolveLLMClient:
    def test_uses_session_provider_and_model(self):
        ac = Actor(MagicMock(), MagicMock())
        registry = MagicMock()
        with patch("app.llm.registry.get_llm_registry", return_value=registry):
            ac._resolve_llm_client(_session(llm_provider="p1", llm_model="m1"))
        registry.get_client.assert_called_once_with("p1", "m1")

    def test_falls_back_to_default_provider(self):
        ac = Actor(MagicMock(), MagicMock())
        registry = MagicMock()
        with patch("app.llm.registry.get_llm_registry", return_value=registry), \
             patch("app.config.settings.get_settings",
                   return_value=SimpleNamespace(default_llm_provider="envp")):
            ac._resolve_llm_client(_session())
        registry.get_client.assert_called_once_with("envp", None)
