"""Tests for the prompt builders (app/runtime/prompt_builder.py).

The tool-pair reconciliation, timeline merge and prior-progress paths already have
focused suites; this covers message sanitising/merging, the non-resume user message,
the resources section, and the observer's transcript / task-list rendering.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.models.task import Task
from app.llm.types import ImagePart, InputSchema, LLMMessage, LLMTool, TextPart
from app.runtime.prompt_builder import PromptBuilderFactory
from app.runtime.types import (
    ActorResult, ContextResource, ConversationTurn, ReasoningContext, ToolCallRecord,
)


def _task(**kw) -> Task:
    base = dict(id="t1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                status="ACTIVE", user_prompt="do X", title="Do X", description="details",
                created_at="", updated_at="")
    base.update(kw)
    return Task(**base)


def _ctx(**kw) -> ReasoningContext:
    base = dict(goal="g", recent_messages=[], blackboard_snippets=[],
                current_task=_task())
    base.update(kw)
    return ReasoningContext(**base)


def _actor():
    return PromptBuilderFactory.for_actor()


def _observer():
    return PromptBuilderFactory.for_observer()


def _tool_resource(name="read", description="Read a file") -> ContextResource:
    return ContextResource(
        name=name, description=description, kind="tool",
        llm_tool=LLMTool(name=name, description=description,
                         input_schema=InputSchema(properties={}, require=[])),
    )


# ── factory ───────────────────────────────────────────────────────────────────

class TestFactory:
    def test_builders_are_distinct_types(self):
        assert type(_actor()) is not type(_observer())

    def test_both_expose_sanitize_messages(self):
        assert callable(_actor().sanitize_messages)
        assert callable(_observer().sanitize_messages)


# ── sanitize_messages ─────────────────────────────────────────────────────────

class TestSanitizeMessages:
    def test_blank_messages_are_dropped(self):
        out = _actor().sanitize_messages([
            LLMMessage(role="user", content="keep"),
            LLMMessage(role="user", content="   "),
            LLMMessage(role="user", content=""),
        ])
        assert [m.content for m in out] == ["keep"]

    def test_tool_messages_survive_even_when_empty(self):
        # needs its matching assistant tool_call, else reconciliation drops the orphan
        out = _actor().sanitize_messages([
            LLMMessage(role="assistant", content="calling",
                       tool_calls=[{"id": "tc1", "name": "read", "input": {}}]),
            LLMMessage(role="tool", content="", tool_call_id="tc1"),
        ])
        assert [m.role for m in out] == ["assistant", "tool"]

    def test_orphan_tool_result_is_dropped(self):
        out = _actor().sanitize_messages([LLMMessage(role="tool", content="r",
                                                    tool_call_id="tc1")])
        assert out == []

    def test_consecutive_user_messages_are_merged(self):
        out = _actor().sanitize_messages([
            LLMMessage(role="user", content="first"),
            LLMMessage(role="user", content="second"),
        ])
        assert len(out) == 1 and out[0].content == "first\n\nsecond"

    def test_assistant_messages_are_never_merged(self):
        out = _actor().sanitize_messages([
            LLMMessage(role="assistant", content="a"),
            LLMMessage(role="assistant", content="b"),
        ])
        assert len(out) == 2

    def test_tool_messages_are_never_merged(self):
        out = _actor().sanitize_messages([
            LLMMessage(role="assistant", content="calling", tool_calls=[
                {"id": "tc1", "name": "read", "input": {}},
                {"id": "tc2", "name": "write", "input": {}},
            ]),
            LLMMessage(role="tool", content="r1", tool_call_id="tc1"),
            LLMMessage(role="tool", content="r2", tool_call_id="tc2"),
        ])
        assert [m.role for m in out] == ["assistant", "tool", "tool"]

    def test_different_roles_are_not_merged(self):
        out = _actor().sanitize_messages([
            LLMMessage(role="user", content="u"),
            LLMMessage(role="system", content="s"),
        ])
        assert len(out) == 2

    def test_merging_preserves_images_from_both_sides(self):
        first = [ImagePart(data="A", media_type="image/png"), TextPart(text="one")]
        second = [ImagePart(data="B", media_type="image/png"), TextPart(text="two")]
        out = _actor().sanitize_messages([
            LLMMessage(role="user", content=first),
            LLMMessage(role="user", content=second),
        ])
        assert len(out) == 1
        images = [p for p in out[0].content if isinstance(p, ImagePart)]
        assert [p.data for p in images] == ["A", "B"]
        text = [p for p in out[0].content if isinstance(p, TextPart)][0]
        assert text.text == "one\n\ntwo"

    def test_merging_text_with_an_image_message(self):
        out = _actor().sanitize_messages([
            LLMMessage(role="user", content="plain"),
            LLMMessage(role="user", content=[ImagePart(data="B", media_type="image/png"),
                                             TextPart(text="captioned")]),
        ])
        assert any(isinstance(p, ImagePart) for p in out[0].content)

    def test_merging_two_plain_texts_stays_a_string(self):
        out = _actor().sanitize_messages([
            LLMMessage(role="user", content="a"),
            LLMMessage(role="user", content="b"),
        ])
        assert isinstance(out[0].content, str)

    def test_fields_are_carried_over(self):
        out = _actor().sanitize_messages([
            LLMMessage(role="assistant", content="t", reasoning_content="why",
                       tool_calls=[{"id": "tc", "name": "n", "input": {}}]),
            LLMMessage(role="tool", content="r", tool_call_id="tc"),
        ])
        assert out[0].reasoning_content == "why"
        assert out[1].tool_call_id == "tc"

    def test_empty_input(self):
        assert _actor().sanitize_messages([]) == []


# ── build_messages (non-resume path) ──────────────────────────────────────────

class TestBuildMessagesFreshTask:
    def test_goal_and_message_sections(self):
        task = _task(user_prompt_in_memory=False, title="T", description="D")
        out = _actor().build_messages(task, _ctx(current_task=task))
        content = out[-1].content
        assert "## Current Goal\nT\nD" in content
        assert "## Current Message\ndo X" in content

    def test_blackboard_section_is_included(self):
        task = _task(user_prompt_in_memory=False)
        ctx = _ctx(current_task=task, blackboard_snippets=["from a sibling"])
        out = _actor().build_messages(task, ctx)
        assert "## Task Background" in out[-1].content
        assert "from a sibling" in out[-1].content

    def test_title_without_description_omits_the_goal(self):
        task = _task(user_prompt_in_memory=False, title="T", description="")
        out = _actor().build_messages(task, _ctx(current_task=task))
        assert "## Current Goal" not in out[-1].content

    def test_no_parts_yields_no_user_message(self):
        task = _task(user_prompt_in_memory=False, user_prompt="", title="", description="")
        out = _actor().build_messages(task, _ctx(current_task=task))
        assert all(m.role != "user" for m in out)

    def test_multimodal_prompt_keeps_its_images(self):
        prompt = [{"type": "image", "data": "D", "media_type": "image/png"},
                  {"type": "text", "text": "look at this"}]
        task = _task(user_prompt_in_memory=False, user_prompt=prompt)
        out = _actor().build_messages(task, _ctx(current_task=task))
        content = out[-1].content
        assert any(isinstance(p, ImagePart) for p in content)
        assert any(isinstance(p, TextPart) for p in content)

    def test_multimodal_prompt_without_images_stays_text(self):
        prompt = [{"type": "text", "text": "just text"}]
        task = _task(user_prompt_in_memory=False, user_prompt=prompt)
        out = _actor().build_messages(task, _ctx(current_task=task))
        assert isinstance(out[-1].content, str)


class TestBuildMessagesResume:
    def test_blackboard_is_appended_when_the_tail_is_not_user(self):
        task = _task(user_prompt_in_memory=True)
        ctx = _ctx(current_task=task, blackboard_snippets=["child result"],
                   recent_messages=[{"role": "assistant", "content": "done", "task_id": "t1"}])
        out = _actor().build_messages(task, ctx)
        assert out[-1].role == "user" and "child result" in out[-1].content

    def test_no_blackboard_appends_nothing(self):
        task = _task(user_prompt_in_memory=True)
        ctx = _ctx(current_task=task,
                   recent_messages=[{"role": "assistant", "content": "done", "task_id": "t1"}])
        out = _actor().build_messages(task, ctx)
        assert out[-1].role == "assistant"

    def test_user_tail_is_left_alone(self):
        task = _task(user_prompt_in_memory=True)
        ctx = _ctx(current_task=task, blackboard_snippets=["bb"],
                   recent_messages=[{"role": "user", "content": "ask", "task_id": "t1"}])
        out = _actor().build_messages(task, ctx)
        assert len([m for m in out if m.role == "user"]) == 1

    def test_empty_timeline_appends_nothing(self):
        task = _task(user_prompt_in_memory=True)
        out = _actor().build_messages(task, _ctx(current_task=task,
                                                blackboard_snippets=["bb"]))
        assert out == []


class TestBuildInitialUserContent:
    def test_goal_and_message(self):
        out = _actor().build_initial_user_content(_task(title="T", description="D"))
        assert "## Current Goal\nT\nD" in out and "## Current Message\ndo X" in out

    def test_without_a_description(self):
        out = _actor().build_initial_user_content(_task(title="T", description=""))
        assert "## Current Goal" not in out and "## Current Message" in out

    def test_without_a_prompt(self):
        assert "## Current Message" not in _actor().build_initial_user_content(
            _task(user_prompt=""))

    def test_multimodal_prompt_returns_blocks(self):
        prompt = [{"type": "image", "data": "D", "media_type": "image/png"},
                  {"type": "text", "text": "hi"}]
        out = _actor().build_initial_user_content(_task(user_prompt=prompt))
        assert isinstance(out, list)
        assert any(isinstance(p, ImagePart) for p in out)

    def test_text_only_list_prompt_returns_a_string(self):
        out = _actor().build_initial_user_content(
            _task(user_prompt=[{"type": "text", "text": "hi"}]))
        assert isinstance(out, str)


# ── resources section ─────────────────────────────────────────────────────────

class TestResourcesSection:
    def _section(self, **ctx_kw):
        return _actor()._build_resources_section(_ctx(**ctx_kw))

    def test_tools_are_rendered_with_signatures(self):
        out = self._section(actor_resources=[_tool_resource()])
        assert "## Available Tools" in out
        assert "read() — Read a file" in out

    def test_skills_are_rendered_when_no_instructions_are_set(self):
        skill = ContextResource(name="pptx", description="makes decks", kind="skill")
        out = self._section(actor_resources=[skill])
        assert "## Available Skills" in out and "pptx: makes decks" in out

    def test_skills_are_hidden_when_instructions_are_present(self):
        skill = ContextResource(name="pptx", description="d", kind="skill")
        out = self._section(actor_resources=[skill], skill_instructions="already inlined")
        assert "## Available Skills" not in out

    def test_agents_are_rendered(self):
        agent = ContextResource(name="planner", description="plans", kind="agent")
        out = self._section(actor_resources=[agent])
        assert "## Available Sub-Agents" in out and "planner: plans" in out

    def test_tool_without_an_llm_tool_is_skipped(self):
        bare = ContextResource(name="x", description="d", kind="tool", llm_tool=None)
        assert "## Available Tools" not in self._section(actor_resources=[bare])

    def test_all_three_sections_together(self):
        out = self._section(actor_resources=[
            ContextResource(name="sk", description="ds", kind="skill"),
            _tool_resource(),
            ContextResource(name="ag", description="da", kind="agent"),
        ])
        assert out.index("Available Skills") < out.index("Available Tools")
        assert out.index("Available Tools") < out.index("Available Sub-Agents")

    def test_no_resources_is_empty(self):
        assert self._section(actor_resources=[]) == ""


class TestActorSystemPrompt:
    def test_soul_is_included(self):
        assert "MY SOUL" in _actor().build_system_prompt(_ctx(soul="MY SOUL"))

    def test_skill_instructions_are_included(self):
        out = _actor().build_system_prompt(_ctx(skill_instructions="do step 1"))
        assert "do step 1" in out

    def test_project_background_is_included(self):
        out = _actor().build_system_prompt(_ctx(project_background="repo notes"))
        assert "repo notes" in out

    def test_tools_reach_the_prompt(self):
        out = _actor().build_system_prompt(_ctx(actor_resources=[_tool_resource()]))
        assert "read() — Read a file" in out


# ── observer prompt ───────────────────────────────────────────────────────────

def _result(output="the output", turns=None) -> ActorResult:
    return ActorResult(task_id="t1", success=True, output=output,
                       conversation_turns=turns if turns is not None else [])


def _session():
    return SimpleNamespace(id="s1", user_prompt="session prompt", goal="g")


class TestObserverSystemPrompt:
    def test_role_is_used(self):
        assert "MY ROLE" in _observer().build_system_prompt(_ctx(role="MY ROLE"))

    def test_fallback_role_when_empty(self):
        out = _observer().build_system_prompt(_ctx(role=""))
        assert "objective observer" in out


class TestObserverMessages:
    def _messages(self, task=None, result=None, ctx=None, task_list=None):
        task = task if task is not None else _task()
        return _observer().build_messages(
            _session(), result if result is not None else _result(),
            ctx if ctx is not None else _ctx(current_task=task),
            task, task_list or [])

    def test_task_title_and_description(self):
        out = self._messages()
        assert "Current task: Do X" in out[0].content
        assert "Task description: details" in out[0].content

    def test_description_falls_back_to_the_title(self):
        out = self._messages(task=_task(description=""))
        assert "Task description: Do X" in out[0].content

    def test_blackboard_snippets_are_included(self):
        task = _task()
        ctx = _ctx(current_task=task, blackboard_snippets=["child said hi"])
        out = self._messages(task=task, ctx=ctx)
        assert "Sub-task results:" in out[0].content and "child said hi" in out[0].content

    def test_user_requirements_come_from_the_task(self):
        out = self._messages()
        assert "User requirements: do X" in out[0].content

    def test_user_requirements_fall_back_to_the_session(self):
        out = self._messages(task=_task(user_prompt=""))
        assert "User requirements: session prompt" in out[0].content

    def test_first_round_says_no_prior_progress(self):
        out = self._messages()
        assert "Prior progress: none (first round)." in out[0].content

    def test_prior_rounds_are_rendered(self):
        task = _task(execution_rounds=[{
            "turns": [{"llm_text": "tried something"}],
            "process_report": "made progress",
            "user_answer": "yes go on",
        }])
        out = self._messages(task=task)
        body = out[0].content
        assert "=== Round 1 ===" in body
        assert "[Agent reply]\ntried something" in body
        assert "[Process report]\nmade progress" in body
        assert "[User reply]\nyes go on" in body

    def test_round_with_only_a_report(self):
        task = _task(execution_rounds=[{"turns": [], "process_report": "just a report"}])
        assert "[Agent reply]" not in self._messages(task=task)[0].content

    def test_task_list_section_is_appended(self):
        # only rendered when at least one sibling is still PENDING
        finished = _task(id="f1", title="Done thing", status="FINISHED",
                         process_report="all good")
        pending = _task(id="p1", title="Todo", status="PENDING")
        out = self._messages(task_list=[finished, pending])
        assert "Session task list:" in out[0].content
        assert "[FINISHED] Done thing | result: all good" in out[0].content

    def test_pending_task_has_no_result_hint(self):
        pending = _task(id="p1", title="Todo", status="PENDING")
        out = self._messages(task_list=[pending])
        assert "[PENDING] Todo" in out[0].content and "result:" not in out[0].content

    def test_finished_without_a_report_has_no_hint(self):
        done = _task(id="d1", title="Done", status="FINISHED", process_report=None)
        pending = _task(id="p1", title="Todo", status="PENDING")
        out = self._messages(task_list=[done, pending])
        assert "[FINISHED] Done" in out[0].content and "result:" not in out[0].content

    def test_no_pending_sibling_means_no_task_list(self):
        done = _task(id="d1", title="Done", status="FINISHED", process_report="r")
        assert "Session task list:" not in self._messages(task_list=[done])[0].content

    def test_the_current_task_is_excluded_from_the_list(self):
        current = _task(id="t1", title="Do X", status="ACTIVE")
        pending = _task(id="p1", title="Todo", status="PENDING")
        out = self._messages(task=current, task_list=[current, pending])
        assert out[0].content.count("Todo") == 1


class TestObserverTranscript:
    def _transcript(self, result):
        return _observer()._build_transcript(result)

    def test_no_turns_but_output(self):
        out = self._transcript(_result(output="just text"))
        assert out == "[No tool calls] Agent response: just text"

    def test_no_turns_and_no_output(self):
        assert self._transcript(_result(output="")) == "[No conversation recorded]"

    def test_rounds_are_numbered_from_one(self):
        turns = [ConversationTurn(round=0, messages_sent=[], llm_text="said it")]
        out = self._transcript(_result(turns=turns))
        assert "--- Round 1 ---" in out and "Agent reply: said it" in out

    def test_tool_calls_are_rendered_with_status(self):
        turns = [ConversationTurn(round=0, messages_sent=[], llm_text="", tool_calls=[
            ToolCallRecord(tool_name="read", arguments={"p": 1}, result="body"),
            ToolCallRecord(tool_name="write", arguments={}, result="nope", is_error=True),
        ])]
        out = self._transcript(_result(turns=turns))
        assert "Tool call: read({'p': 1})" in out
        assert "Result [OK]: body" in out
        assert "Result [ERROR]: nope" in out

    def test_long_tool_results_are_truncated(self):
        turns = [ConversationTurn(round=0, messages_sent=[], llm_text="", tool_calls=[
            ToolCallRecord(tool_name="read", arguments={}, result="x" * 900)])]
        out = self._transcript(_result(turns=turns))
        assert "x" * 500 in out and "x" * 501 not in out

    def test_turn_without_text_or_tools(self):
        turns = [ConversationTurn(round=0, messages_sent=[], llm_text="")]
        assert self._transcript(_result(turns=turns)) == "--- Round 1 ---"
