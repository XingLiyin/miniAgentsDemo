"""Tests for Observer (app/runtime/observer.py).

Two paths: the LLM ReAct loop (which must land a task assessment via a control tool)
and the rule-based fallback taken when role_md is absent, when a root agent finished
cleanly, or when the LLM call blows up.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.common.errors import AppError
from app.domain.models.agent import Agent, AgentCapability, LoopGuard
from app.domain.models.session import Session
from app.domain.models.task import Task
from app.llm.types import ImageBlock, InputSchema, LLMTool, LLMUsage, StreamChunk
from app.runtime.observer import Observer, _push_llm_event, _stream_observer
from app.runtime.types import (
    ActorResult, ContextResource, ConversationTurn, ObserverVerdict, ReasoningContext,
    ToolCallRecord,
)
from app.tools.types import ToolResult


# ── builders ──────────────────────────────────────────────────────────────────

def _task(**kw) -> Task:
    base = dict(id="t1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                status="TO_BE_OBSERVED", user_prompt="do X", title="Do X",
                created_at="", updated_at="")
    base.update(kw)
    return Task(**base)


def _agent(spawn_depth=1, observer_rounds=5, **kw) -> Agent:
    base = dict(id="a1", session_id="s1", template_id="tpl", name="ag", status="RUNNING",
                observer=AgentCapability(tools=["submit_task_assessment"]),
                loop_guard=LoopGuard(observer_max_tool_rounds=observer_rounds),
                spawn_depth=spawn_depth)
    base.update(kw)
    return Agent(**base)


def _session(**kw) -> Session:
    base = dict(id="s1", user_prompt="p", goal="g", status="RUNNING",
                template_id=None, root_agent_id="a1")
    base.update(kw)
    return Session(**base)


def _result(success=True, output="did it", exit_reason="normal", turns=1,
            tool_calls=None, error=None, context_tokens=0) -> ActorResult:
    return ActorResult(
        task_id="t1", success=success, output=output, exit_reason=exit_reason,
        tool_calls_made=tool_calls or [],
        conversation_turns=[ConversationTurn(round=i, messages_sent=[], llm_text="")
                            for i in range(turns)],
        error=error, context_tokens=context_tokens,
    )


def _rec(name="read", is_error=False) -> ToolCallRecord:
    return ToolCallRecord(tool_name=name, arguments={}, result="r", is_error=is_error)


def _obs_tool(name="submit_task_assessment") -> ContextResource:
    return ContextResource(
        name=name, description="d", kind="tool",
        llm_tool=LLMTool(name=name, input_schema=InputSchema(properties={}, require=[])),
    )


def _ctx(role="ROLE", **kw) -> ReasoningContext:
    base = dict(goal="g", recent_messages=[], blackboard_snippets=[],
                role=role, observer_resources=[_obs_tool()], current_task=_task())
    base.update(kw)
    return ReasoningContext(**base)


class _FakeLLM:
    context_limit = 100_000
    max_output_tokens = 4096

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    def stream_message(self, messages, system_prompt=None, tools=None, max_tokens=None):
        self.calls.append({"messages": list(messages), "system_prompt": system_prompt,
                           "tools": tools, "max_tokens": max_tokens})
        for chunk in (self.turns.pop(0) if self.turns else
                      [StreamChunk(text_delta="ok"), StreamChunk(is_done=True)]):
            yield chunk

    def parse_stream_acc(self, full_text, acc, images=None, usage=None):
        from app.llm.types import ToolCallBlock
        calls = [ToolCallBlock(type="tool_call", id=b.get("id", ""), name=b.get("name", ""),
                               input=b.get("input", {})) for b in acc.values()]
        return SimpleNamespace(text=full_text, tool_calls=calls, images=images or [], blocks=[])


def _assess_turn(text="assessing", tool="submit_task_assessment", usage=None):
    return [StreamChunk(text_delta=text),
            StreamChunk(tool_call_delta={"index": 0, "id": "tc1", "name": tool}),
            StreamChunk(is_done=True, finish_reason="tool_calls", usage=usage)]


def _text_turn(text="no tools", usage=None):
    return [StreamChunk(text_delta=text), StreamChunk(is_done=True, usage=usage)]


def _observer(turns, gateway=None, task_svc=None, session_svc=None):
    gw = gateway
    if gw is None:
        gw = MagicMock()
        gw.call.return_value = ToolResult(content="assessment recorded")
    llm = _FakeLLM(turns)
    ob = Observer(gw, task_svc or MagicMock(), session_svc)
    ob._resolve_llm_client = lambda session: llm
    return ob, llm, gw


def _finishing_gateway(task: Task, status="FINISHED", report="all good"):
    """A gateway whose control tool marks the task observed, like the real one does."""
    gw = MagicMock()

    def call(*a, **kw):
        task.status = status
        task.process_report = report
        return ToolResult(content="recorded")

    gw.call.side_effect = call
    return gw


# ── rule-based fallback ───────────────────────────────────────────────────────

class TestRuleFallback:
    def test_no_role_md_skips_the_llm(self):
        svc = MagicMock()
        ob, llm, _ = _observer([], task_svc=svc)
        v = ob.observe(_session(), _result(), _ctx(role=""), _task(), agent=_agent())
        assert llm.calls == []
        assert isinstance(v, ObserverVerdict)
        svc.finish.assert_called_once()

    def test_root_agent_clean_exit_skips_the_llm(self):
        ob, llm, _ = _observer([])
        ob.observe(_session(), _result(exit_reason="normal"), _ctx(), _task(),
                   agent=_agent(spawn_depth=0))
        assert llm.calls == []

    @pytest.mark.parametrize("reason", ["max_rounds", "context_limit"])
    def test_root_agent_abnormal_exit_still_observes(self, reason):
        task = _task()
        gw = _finishing_gateway(task)
        ob, llm, _ = _observer([_assess_turn()], gateway=gw)
        ob.observe(_session(), _result(exit_reason=reason), _ctx(), task,
                   agent=_agent(spawn_depth=0))
        assert len(llm.calls) == 1

    def test_llm_failure_falls_back(self):
        svc = MagicMock()
        ob, llm, _ = _observer([], task_svc=svc)
        llm.stream_message = MagicMock(side_effect=RuntimeError("llm down"))
        v = ob.observe(_session(), _result(), _ctx(), _task(), agent=_agent())
        assert "Task completed successfully" in v.summary
        svc.finish.assert_called_once()

    def test_success_finishes_the_task(self):
        svc = MagicMock()
        ob, _, _ = _observer([], task_svc=svc)
        ob.observe(_session(), _result(success=True, output="the output"),
                   _ctx(role=""), _task(), agent=_agent())
        kw = svc.finish.call_args.kwargs
        assert kw["outputs"] == "the output" and kw["session_id"] == "s1"

    def test_failure_fails_the_task_and_backfills_report(self):
        svc = MagicMock()
        stored = _task(status="FAILED")
        svc.get.return_value = stored
        ob, _, _ = _observer([], task_svc=svc)
        v = ob.observe(_session(), _result(success=False, error="it broke", output="partial"),
                       _ctx(role=""), _task(), agent=_agent())
        svc.fail.assert_called_once()
        assert svc.fail.call_args.kwargs["error"] == "it broke"
        assert stored.process_report == v.summary
        assert stored.outputs == "partial"
        svc.save.assert_called_once_with(stored)

    def test_failure_without_error_uses_output(self):
        svc = MagicMock()
        svc.get.return_value = _task()
        ob, _, _ = _observer([], task_svc=svc)
        ob.observe(_session(), _result(success=False, error=None, output="only output"),
                   _ctx(role=""), _task(), agent=_agent())
        assert svc.fail.call_args.kwargs["error"] == "only output"

    def test_token_budget_exhausted_finishes_regardless_of_failure(self):
        svc = MagicMock()
        ob, _, _ = _observer([], task_svc=svc)
        session = _session(token_budget=1000, output_tokens_used=950)
        v = ob.observe(session, _result(success=False), _ctx(role=""), _task(), agent=_agent())
        svc.finish.assert_called_once()
        assert not svc.fail.called
        assert "Token budget nearly exhausted" in v.summary

    def test_zero_budget_is_not_exhausted(self):
        svc = MagicMock()
        ob, _, _ = _observer([], task_svc=svc)
        session = _session(token_budget=0, output_tokens_used=10**9)
        v = ob.observe(session, _result(success=True), _ctx(role=""), _task(), agent=_agent())
        assert "Token budget" not in v.summary


class TestBuildRuleReport:
    def test_round_count_and_no_tools(self):
        out = Observer._build_rule_report(_result(turns=3))
        assert "Ran 3 conversation round(s)." in out
        assert "No tools were called." in out
        assert "Task completed successfully." in out

    def test_tool_names_deduplicated_in_order(self):
        out = Observer._build_rule_report(
            _result(tool_calls=[_rec("read"), _rec("write"), _rec("read")]))
        assert "Tools used: read, write." in out

    def test_error_tools_listed(self):
        out = Observer._build_rule_report(
            _result(tool_calls=[_rec("read"), _rec("write", is_error=True)]))
        assert "Tools with errors: write." in out

    def test_no_error_tools_omits_the_line(self):
        assert "errors" not in Observer._build_rule_report(_result(tool_calls=[_rec()]))

    def test_failure_line(self):
        assert "Task failed this turn." in Observer._build_rule_report(_result(success=False))

    def test_token_exhausted_wins_over_success(self):
        out = Observer._build_rule_report(_result(success=True), token_exhausted=True)
        assert "Token budget nearly exhausted" in out
        assert "completed successfully" not in out


# ── LLM ReAct loop ────────────────────────────────────────────────────────────

class TestLLMObserve:
    def test_assessment_tool_ends_the_loop(self):
        task = _task()
        gw = _finishing_gateway(task, report="looks done")
        ob, llm, _ = _observer([_assess_turn()], gateway=gw)
        v = ob.observe(_session(), _result(), _ctx(), task, agent=_agent())
        assert v.summary == "looks done"
        assert len(llm.calls) == 1
        assert gw.call.call_args.args[0] == "submit_task_assessment"

    def test_role_and_tools_forwarded(self):
        task = _task()
        ob, llm, _ = _observer([_assess_turn()], gateway=_finishing_gateway(task))
        ob.observe(_session(), _result(), _ctx(), task, agent=_agent())
        assert "ROLE" in llm.calls[0]["system_prompt"]
        assert [t.name for t in llm.calls[0]["tools"]] == ["submit_task_assessment"]

    def test_capability_and_task_passed_to_the_gateway(self):
        task = _task()
        gw = _finishing_gateway(task)
        ob, _, _ = _observer([_assess_turn()], gateway=gw)
        agent = _agent()
        ob.observe(_session(), _result(), _ctx(), task, agent=agent)
        args = gw.call.call_args.args
        assert args[2] is agent.observer and args[3] == "t1"
        assert args[4].agent_id == "a1" and args[4].session_id == "s1"

    def test_resources_without_llm_tool_skipped(self):
        task = _task()
        ctx = _ctx(observer_resources=[_obs_tool(),
                                       ContextResource(name="s", description="", kind="skill")])
        ob, llm, _ = _observer([_assess_turn()], gateway=_finishing_gateway(task))
        ob.observe(_session(), _result(), ctx, task, agent=_agent())
        assert len(llm.calls[0]["tools"]) == 1

    def test_no_tool_calls_breaks_then_missing_assessment_falls_back(self):
        svc = MagicMock()
        ob, llm, _ = _observer([_text_turn()], task_svc=svc)
        v = ob.observe(_session(), _result(), _ctx(), _task(), agent=_agent())
        # loop broke without an assessment -> RuntimeError -> rule fallback
        assert len(llm.calls) == 1
        assert "Task completed successfully" in v.summary
        svc.finish.assert_called_once()

    def test_multiple_rounds_until_assessment(self):
        task = _task()
        gw = MagicMock()
        state = {"n": 0}

        def call(*a, **kw):
            state["n"] += 1
            if state["n"] == 2:
                task.status = "FINISHED"
                task.process_report = "done at last"
            return ToolResult(content="ok")

        gw.call.side_effect = call
        ob, llm, _ = _observer([_assess_turn(tool="read"), _assess_turn()], gateway=gw)
        v = ob.observe(_session(), _result(), _ctx(), task, agent=_agent())
        assert len(llm.calls) == 2 and v.summary == "done at last"

    def test_max_rounds_without_assessment_falls_back(self):
        svc = MagicMock()
        gw = MagicMock()
        gw.call.return_value = ToolResult(content="still thinking")
        ob, llm, _ = _observer([_assess_turn(tool="read") for _ in range(6)],
                               gateway=gw, task_svc=svc)
        v = ob.observe(_session(), _result(), _ctx(), _task(), agent=_agent(observer_rounds=2))
        assert len(llm.calls) == 2
        svc.finish.assert_called_once()      # fell back to rules
        assert "conversation round(s)" in v.summary

    def test_multimodal_tool_result_is_flattened(self):
        task = _task()
        gw = MagicMock()

        def call(*a, **kw):
            task.status = "FINISHED"
            task.process_report = "r"
            return ToolResult(content=[{"type": "text", "text": "flat"}])

        gw.call.side_effect = call
        ob, _, _ = _observer([_assess_turn()], gateway=gw)
        assert ob.observe(_session(), _result(), _ctx(), task, agent=_agent()).summary == "r"


class TestSummaryComposition:
    def _observe(self, status="FINISHED", report=None, error=None, llm_text="llm said"):
        task = _task()
        gw = MagicMock()

        def call(*a, **kw):
            task.status = status
            task.process_report = report
            task.error = error
            return ToolResult(content="ok")

        gw.call.side_effect = call
        ob, _, _ = _observer([_assess_turn(text=llm_text)], gateway=gw)
        return ob.observe(_session(), _result(), _ctx(), task, agent=_agent())

    def test_report_and_error_are_combined(self):
        v = self._observe(report="the report", error="the error")
        assert v.summary == "the report\n\nError encountered: the error"

    def test_report_alone(self):
        assert self._observe(report="only report").summary == "only report"

    def test_error_alone(self):
        assert self._observe(report=None, error="only error").summary == "only error"

    def test_falls_back_to_llm_text(self):
        assert self._observe(report=None, error=None, llm_text="what it said").summary \
            == "what it said"

    def test_empty_when_nothing_available(self):
        assert self._observe(report=None, error=None, llm_text="").summary == ""


class TestObserverTokens:
    def test_usage_recorded_on_the_session(self):
        task = _task()
        svc = MagicMock()
        usage = LLMUsage(prompt_tokens=200, completion_tokens=40)
        ob, _, _ = _observer([_assess_turn(usage=usage)],
                             gateway=_finishing_gateway(task), session_svc=svc)
        v = ob.observe(_session(), _result(), _ctx(), task, agent=_agent())
        svc.add_tokens.assert_called_once_with(
            "s1", input_tokens=200, output_tokens=40, context_tokens=200)
        assert v.context_tokens == 200

    def test_no_session_service_is_fine(self):
        task = _task()
        ob, _, _ = _observer([_assess_turn(usage=LLMUsage(prompt_tokens=7))],
                             gateway=_finishing_gateway(task), session_svc=None)
        assert ob.observe(_session(), _result(), _ctx(), task, agent=_agent()).context_tokens == 7

    def test_max_tokens_is_none_when_no_prior_estimate(self):
        task = _task()
        ob, llm, _ = _observer([_assess_turn()], gateway=_finishing_gateway(task))
        ob.observe(_session(), _result(context_tokens=0), _ctx(), task, agent=_agent())
        assert llm.calls[0]["max_tokens"] is None

    def test_max_tokens_derived_from_actor_context(self):
        task = _task()
        ob, llm, _ = _observer([_assess_turn()], gateway=_finishing_gateway(task))
        ob.observe(_session(), _result(context_tokens=1000), _ctx(), task, agent=_agent())
        assert llm.calls[0]["max_tokens"] == 4096


class TestWorkingDirResolution:
    def _wd(self, task, agent):
        gw = _finishing_gateway(task)
        ob, _, _ = _observer([_assess_turn()], gateway=gw)
        with patch("app.config.settings.resolve_working_dir", side_effect=lambda r: f"abs:{r}"):
            ob.observe(_session(), _result(), _ctx(), task, agent=agent)
        return gw.call.call_args.args[4].working_dir

    def test_task_setting_wins(self):
        task = _task(settings={"working_dir": "/task"})
        assert self._wd(task, _agent(settings={"working_dir": "/agent"})) == "abs:/task"

    def test_agent_setting_next(self):
        assert self._wd(_task(), _agent(settings={"working_dir": "/agent"})) == "abs:/agent"

    def test_empty_when_neither_set(self):
        assert self._wd(_task(), _agent()) == "abs:"


# ── SSE helpers ───────────────────────────────────────────────────────────────

class TestPushLLMEvent:
    def test_pushes_a_prompt_event(self):
        bus = MagicMock()
        with patch("app.common.sse_bus.get_sse_bus", return_value=bus):
            _push_llm_event("s1", "observer_round_0", "sys",
                            [SimpleNamespace(role="user", content="hi")],
                            [SimpleNamespace(name="tool_a")], _task())
        ev = bus.push.call_args.args[1]
        assert ev["type"] == "llm_prompt" and ev["source"] == "observer"
        assert ev["round_label"] == "observer_round_0"
        assert ev["task_id"] == "t1" and ev["agent_id"] == "a1"
        assert ev["tool_names"] == ["tool_a"]
        assert ev["messages"] == [{"role": "user", "content": "hi"}]

    def test_daemon_flag_changes_type(self):
        bus = MagicMock()
        with patch("app.common.sse_bus.get_sse_bus", return_value=bus):
            _push_llm_event("s1", "r", "sys", [], [], _task(), is_daemon=True)
        assert bus.push.call_args.args[1]["type"] == "daemon_prompt"

    def test_without_task(self):
        bus = MagicMock()
        with patch("app.common.sse_bus.get_sse_bus", return_value=bus):
            _push_llm_event("s1", "r", "sys", [], [], None)
        ev = bus.push.call_args.args[1]
        assert ev["task_id"] == "" and ev["agent_id"] == ""

    def test_tool_without_name_is_stringified(self):
        bus = MagicMock()
        with patch("app.common.sse_bus.get_sse_bus", return_value=bus):
            _push_llm_event("s1", "r", "sys", [], ["plain-string"], None)
        assert bus.push.call_args.args[1]["tool_names"] == ["plain-string"]

    def test_no_session_id_is_a_noop(self):
        bus = MagicMock()
        with patch("app.common.sse_bus.get_sse_bus", return_value=bus):
            _push_llm_event("", "r", "sys", [], [], None)
        assert not bus.push.called

    def test_push_failure_is_swallowed(self):
        bus = MagicMock()
        bus.push.side_effect = RuntimeError("bus down")
        with patch("app.common.sse_bus.get_sse_bus", return_value=bus):
            _push_llm_event("s1", "r", "sys", [], [], None)


class TestStreamObserver:
    def _stream(self, chunks, session_id="s1", is_daemon=False, bus=None):
        client = MagicMock()
        client.stream_message.return_value = iter(chunks)
        bus = bus if bus is not None else MagicMock()
        with patch("app.common.sse_bus.get_sse_bus", return_value=bus):
            out = _stream_observer(client, [], "sys", [], session_id, "r",
                                   is_daemon=is_daemon)
        return out, bus

    def test_accumulates_text_tool_calls_and_images(self):
        img = ImageBlock(type="image", media_type="image/png", source_type="base64", data="D")
        (text, acc, images, usage), bus = self._stream([
            StreamChunk(text_delta="he"), StreamChunk(text_delta="llo"),
            StreamChunk(tool_call_delta={"index": 0, "id": "tc", "name": "n"}),
            StreamChunk(image=img),
            StreamChunk(is_done=True, finish_reason="stop", usage=LLMUsage(prompt_tokens=9)),
        ])
        assert text == "hello" and acc[0]["id"] == "tc"
        assert images == [img] and usage.prompt_tokens == 9

    def test_pushes_delta_and_done_events(self):
        _, bus = self._stream([StreamChunk(text_delta="x"), StreamChunk(is_done=True)])
        types = [c.args[1]["type"] for c in bus.push.call_args_list]
        assert "observer_text_delta" in types and "observer_text_done" in types

    def test_reasoning_events(self):
        _, bus = self._stream([StreamChunk(reasoning_delta="think"),
                               StreamChunk(is_done=True)])
        types = [c.args[1]["type"] for c in bus.push.call_args_list]
        assert "observer_reasoning_delta" in types and "observer_reasoning_done" in types

    def test_daemon_suppresses_deltas_and_renames_done(self):
        _, bus = self._stream([StreamChunk(text_delta="x"),
                               StreamChunk(reasoning_delta="r"),
                               StreamChunk(is_done=True)], is_daemon=True)
        types = [c.args[1]["type"] for c in bus.push.call_args_list]
        assert types == ["daemon_message"]

    def test_error_chunk_raises(self):
        with pytest.raises(AppError) as e:
            self._stream([StreamChunk(is_done=True, error="quota exceeded")])
        assert e.value.code == "LLM_API_ERROR"

    def test_no_session_id_means_no_bus(self):
        _, bus = self._stream([StreamChunk(text_delta="x"), StreamChunk(is_done=True)],
                              session_id="")
        assert not bus.push.called

    def test_delta_push_failure_is_swallowed(self):
        bus = MagicMock()
        bus.push.side_effect = RuntimeError("down")
        (text, _, _, _), _ = self._stream(
            [StreamChunk(text_delta="x"), StreamChunk(reasoning_delta="r"),
             StreamChunk(is_done=True)], bus=bus)
        assert text == "x"

    def test_bus_lookup_failure_is_tolerated(self):
        client = MagicMock()
        client.stream_message.return_value = iter([StreamChunk(text_delta="x"),
                                                   StreamChunk(is_done=True)])
        with patch("app.common.sse_bus.get_sse_bus", side_effect=RuntimeError("no bus")):
            text, _, _, _ = _stream_observer(client, [], "sys", [], "s1", "r")
        assert text == "x"


class TestResolveLLMClient:
    def test_uses_session_provider(self):
        ob = Observer(MagicMock(), MagicMock())
        registry = MagicMock()
        with patch("app.llm.registry.get_llm_registry", return_value=registry):
            ob._resolve_llm_client(_session(llm_provider="p", llm_model="m"))
        registry.get_client.assert_called_once_with("p", "m")

    def test_falls_back_to_default_provider(self):
        ob = Observer(MagicMock(), MagicMock())
        registry = MagicMock()
        with patch("app.llm.registry.get_llm_registry", return_value=registry), \
             patch("app.config.settings.get_settings",
                   return_value=SimpleNamespace(default_llm_provider="envp")):
            ob._resolve_llm_client(_session())
        registry.get_client.assert_called_once_with("envp", None)
