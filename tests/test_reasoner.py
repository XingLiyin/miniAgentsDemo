"""Tests for Reasoner (app/runtime/reasoner.py): context assembly, no LLM calls.

Covers memory/blackboard gathering, token estimation, compaction triggering,
skill + tool + sub-agent resource retrieval, description compression, and the
BACKGROUND.md / skill-instruction lookups.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.domain.models.agent import Agent, AgentCapability, LoopGuard
from app.domain.models.session import Session
from app.domain.models.task import Task
from app.llm.types import InputSchema, LLMTool
from app.runtime import reasoner as reasoner_mod
from app.runtime.reasoner import Reasoner
from app.runtime.types import ContextResource
from app.tools.types import CallContext


# ── builders ──────────────────────────────────────────────────────────────────

def _task(**kw) -> Task:
    base = dict(id="t1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                status="ACTIVE", user_prompt="do X", title="T", created_at="", updated_at="")
    base.update(kw)
    return Task(**base)


def _agent(**kw) -> Agent:
    base = dict(id="a1", session_id="s1", template_id="tpl", name="ag", status="RUNNING",
                actor=AgentCapability(instruction_md="SOUL", tools=["read"]),
                observer=AgentCapability(instruction_md="ROLE", tools=["assess"]),
                loop_guard=LoopGuard())
    base.update(kw)
    return Agent(**base)


def _session(**kw) -> Session:
    base = dict(id="s1", user_prompt="p", goal="the goal", status="RUNNING",
                template_id=None, root_agent_id="a1")
    base.update(kw)
    return Session(**base)


def _llm_tool(name="read", description="Read a file") -> LLMTool:
    return LLMTool(name=name, description=description,
                   input_schema=InputSchema(properties={}, require=[]))


def _memory(messages=None, should_summarize=False, count=0):
    mem = MagicMock()
    mem.get_all_messages.return_value = messages if messages is not None else []
    mem.get_window.return_value = messages if messages is not None else []
    mem.should_summarize.return_value = should_summarize
    mem.count_messages.return_value = count
    return mem


def _blackboard(entries=None):
    bb = MagicMock()
    bb.pull.return_value = entries or []
    return bb


def _tool_registry(tools=None, server_tools=None):
    reg = MagicMock()
    reg.to_llm_tools.side_effect = lambda names: [
        t for t in (tools if tools is not None else [_llm_tool()]) if t.name in set(names)
    ]
    reg.get_server_tool_names.side_effect = lambda s: (server_tools or {}).get(s, [])
    return reg


def _reasoner(**kw) -> Reasoner:
    defaults = dict(
        memory_svc=_memory(), blackboard_svc=_blackboard(),
        tool_registry=_tool_registry(), skill_registry=None, task_svc=None,
        agent_template_loader=None, compaction_agent=None, agent_store=None,
        resource_summarizer=None,
    )
    defaults.update(kw)
    return Reasoner(**defaults)


@pytest.fixture(autouse=True)
def _no_settings_side_effects(monkeypatch):
    """Keep get_settings / resolve_working_dir deterministic and filesystem-free."""
    monkeypatch.setattr("app.config.settings.get_settings",
                        lambda: SimpleNamespace(default_llm_provider="envp",
                                                bash_exec_cwd=""))
    monkeypatch.setattr("app.config.settings.resolve_working_dir", lambda raw: raw)


# ── reason(): overall shape ───────────────────────────────────────────────────

class TestReason:
    def test_builds_context_from_the_pieces(self):
        r = _reasoner(memory_svc=_memory([{"role": "user", "content": "hi"}]))
        ctx = r.reason(_session(), _agent(), _task())
        assert ctx.goal == "the goal"
        assert ctx.recent_messages == [{"role": "user", "content": "hi"}]
        assert ctx.soul == "SOUL" and ctx.role == "ROLE"
        assert ctx.current_task.id == "t1"
        assert [res.name for res in ctx.actor_resources] == ["read"]

    def test_observer_resources_built_separately(self):
        reg = _tool_registry(tools=[_llm_tool("read"), _llm_tool("assess", "Assess it")])
        ctx = _reasoner(tool_registry=reg).reason(_session(), _agent(), _task())
        assert [res.name for res in ctx.observer_resources] == ["assess"]

    def test_blackboard_snippets_collected_per_tracked_task(self):
        bb = MagicMock()
        bb.pull.side_effect = lambda sid, tid, aid: [SimpleNamespace(content=f"from-{tid}")]
        r = _reasoner(blackboard_svc=bb)
        ctx = r.reason(_session(), _agent(tracking_tasks=["tA", "tB"]), _task())
        assert ctx.blackboard_snippets == ["from-tA", "from-tB"]

    def test_no_tracked_tasks_means_no_snippets(self):
        ctx = _reasoner().reason(_session(), _agent(), _task())
        assert ctx.blackboard_snippets == []

    def test_no_tool_registry_yields_empty_observer_resources(self):
        r = Reasoner(_memory(), _blackboard(), tool_registry=None)
        # _build_actor_resources needs a registry, so exercise the observer path directly
        assert r._build_observer_resources(_agent(), _task()) == []

    def test_compaction_refetches_the_base(self):
        mem = _memory([{"role": "user", "content": "old"}], should_summarize=True)
        r = _reasoner(memory_svc=mem, compaction_agent=None)
        r.reason(_session(), _agent(), _task())
        assert mem.get_all_messages.call_count == 2      # once, compact, once more

    def test_no_compaction_fetches_once(self):
        mem = _memory(should_summarize=False)
        _reasoner(memory_svc=mem).reason(_session(), _agent(), _task())
        assert mem.get_all_messages.call_count == 1


# ── token estimation ──────────────────────────────────────────────────────────

class TestTokenEstimate:
    def test_cold_estimate_covers_goal_messages_and_blackboard(self):
        mem = _memory([{"role": "user", "content": "some text here"}])
        bb = MagicMock()
        bb.pull.return_value = [SimpleNamespace(content="board text")]
        r = _reasoner(memory_svc=mem, blackboard_svc=bb)
        ctx = r.reason(_session(), _agent(tracking_tasks=["tA"]), _task())
        assert ctx.token_estimate > 0

    def test_warm_estimate_only_counts_new_messages(self):
        msgs = [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]
        guard = LoopGuard(context_tokens=500, context_message_count=1)
        r = _reasoner(memory_svc=_memory(msgs))
        ctx = r.reason(_session(), _agent(loop_guard=guard), _task())
        assert ctx.token_estimate >= 500

    def test_multimodal_content_is_flattened_for_estimation(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "words"}]}]
        ctx = _reasoner(memory_svc=_memory(msgs)).reason(_session(), _agent(), _task())
        assert ctx.token_estimate > 0

    def test_non_text_content_is_ignored(self):
        msgs = [{"role": "user", "content": 42}]
        ctx = _reasoner(memory_svc=_memory(msgs)).reason(_session(), _agent(), _task())
        assert ctx.token_estimate >= 0


# ── compaction ────────────────────────────────────────────────────────────────

class TestMaybeCompact:
    def test_context_limit_read_from_the_registry(self):
        mem = _memory(should_summarize=False)
        registry = MagicMock()
        registry.get_client.return_value = SimpleNamespace(context_limit=12345)
        r = _reasoner(memory_svc=mem)
        with patch("app.llm.registry.get_llm_registry", return_value=registry):
            r._maybe_compact(_session(llm_provider="p", llm_model="m"), _agent(), 100)
        assert mem.should_summarize.call_args.kwargs["context_limit"] == 12345

    def test_registry_failure_falls_back_to_zero_limit(self):
        mem = _memory(should_summarize=False)
        r = _reasoner(memory_svc=mem)
        with patch("app.llm.registry.get_llm_registry", side_effect=RuntimeError("no reg")):
            assert r._maybe_compact(_session(), _agent(), 100) is False
        assert mem.should_summarize.call_args.kwargs["context_limit"] == 0

    def test_returns_true_when_compaction_runs(self):
        r = _reasoner(memory_svc=_memory(should_summarize=True))
        with patch("app.llm.registry.get_llm_registry", side_effect=RuntimeError()):
            assert r._maybe_compact(_session(), _agent(), 100) is True


class TestDoCompact:
    def test_writes_summary_and_resets_guard(self):
        mem = _memory([{"role": "user", "content": "m"}], count=1)
        store = MagicMock()
        r = _reasoner(memory_svc=mem, agent_store=store)
        agent = _agent(loop_guard=LoopGuard(context_tokens=9999))
        r._do_compact(_session(), agent)
        summary = mem.save_summary.call_args.args[1]
        assert summary.covered_up_to == 1 and summary.summary_text == ""
        assert agent.loop_guard.context_tokens == 0
        assert store.save.called

    def test_without_agent_store_is_fine(self):
        r = _reasoner(memory_svc=_memory(), agent_store=None)
        r._do_compact(_session(), _agent())

    def test_compaction_agent_summary_is_prepended(self):
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(5)]
        mem = _memory(msgs, count=3)
        comp = MagicMock()
        comp.compact.return_value = ([msgs[-1]], "the summary")
        store = MagicMock()
        store.get.return_value = {"settings": {"working_dir": "/w"}}
        r = _reasoner(memory_svc=mem, compaction_agent=comp, agent_store=store)
        r._do_compact(_session(), _agent())
        rewritten = mem.rewrite_messages.call_args.args[1]
        assert rewritten[0]["role"] == "assistant"
        assert "[Context so far]:\nthe summary" in rewritten[0]["content"]
        assert mem.save_summary.call_args.args[1].summary_text == "the summary"
        assert comp.compact.call_args.kwargs["working_dir"] == "/w"
        assert comp.compact.call_args.kwargs["session_goal"] == "the goal"

    def test_no_summary_still_rewrites_when_shorter(self):
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(3)]
        mem = _memory(msgs)
        comp = MagicMock()
        comp.compact.return_value = ([msgs[-1]], "")
        r = _reasoner(memory_svc=mem, compaction_agent=comp)
        r._do_compact(_session(), _agent())
        assert mem.rewrite_messages.call_args.args[1] == [msgs[-1]]

    def test_same_length_result_is_not_rewritten(self):
        msgs = [{"role": "user", "content": "m"}]
        mem = _memory(msgs)
        comp = MagicMock()
        comp.compact.return_value = (msgs, "")
        r = _reasoner(memory_svc=mem, compaction_agent=comp)
        r._do_compact(_session(), _agent())
        assert not mem.rewrite_messages.called

    def test_compaction_failure_is_swallowed(self):
        mem = _memory([{"role": "user", "content": "m"}])
        comp = MagicMock()
        comp.compact.side_effect = RuntimeError("compaction blew up")
        r = _reasoner(memory_svc=mem, compaction_agent=comp)
        r._do_compact(_session(), _agent())
        assert mem.save_summary.called          # still records a summary row

    def test_missing_agent_data_is_tolerated(self):
        store = MagicMock()
        store.get.return_value = None
        comp = MagicMock()
        comp.compact.return_value = ([], "s")
        r = _reasoner(memory_svc=_memory(), compaction_agent=comp, agent_store=store)
        r._do_compact(_session(), _agent())
        assert comp.compact.call_args.kwargs["working_dir"] == ""


# ── delegated-child filtering ─────────────────────────────────────────────────

class TestFilterDelegatedChildMessages:
    def test_without_task_service_nothing_is_filtered(self):
        r = _reasoner(task_svc=None)
        msgs = [{"task_id": "other"}]
        assert r._filter_delegated_child_messages(msgs, _task(), "s1") == msgs

    def test_no_other_task_ids_short_circuits(self):
        svc = MagicMock()
        r = _reasoner(task_svc=svc)
        msgs = [{"task_id": "t1"}, {"role": "user"}]
        assert r._filter_delegated_child_messages(msgs, _task(), "s1") == msgs
        assert not svc.get.called

    def test_delegated_child_messages_dropped(self):
        svc = MagicMock()
        svc.get.return_value = _task(id="child", parent_tool_call_id="tc1")
        r = _reasoner(task_svc=svc)
        msgs = [{"task_id": "t1"}, {"task_id": "child"}]
        assert r._filter_delegated_child_messages(msgs, _task(), "s1") == [{"task_id": "t1"}]

    def test_non_delegated_other_task_kept(self):
        svc = MagicMock()
        svc.get.return_value = _task(id="sibling", parent_tool_call_id=None)
        r = _reasoner(task_svc=svc)
        msgs = [{"task_id": "sibling"}]
        assert r._filter_delegated_child_messages(msgs, _task(), "s1") == msgs

    def test_lookup_failure_keeps_the_messages(self):
        svc = MagicMock()
        svc.get.side_effect = RuntimeError("gone")
        r = _reasoner(task_svc=svc)
        msgs = [{"task_id": "unknown"}]
        assert r._filter_delegated_child_messages(msgs, _task(), "s1") == msgs

    def test_messages_without_task_id_are_kept(self):
        svc = MagicMock()
        svc.get.return_value = _task(id="child", parent_tool_call_id="tc")
        r = _reasoner(task_svc=svc)
        msgs = [{"role": "user"}, {"task_id": "child"}]
        assert r._filter_delegated_child_messages(msgs, _task(), "s1") == [{"role": "user"}]


# ── BACKGROUND.md ─────────────────────────────────────────────────────────────

class TestLoadBackground:
    def test_reads_the_file(self, tmp_path, monkeypatch):
        (tmp_path / "BACKGROUND.md").write_text("project notes", encoding="utf-8")
        monkeypatch.setattr("app.config.settings.resolve_working_dir", lambda raw: str(tmp_path))
        r = _reasoner()
        assert r._load_background(_agent(settings={"working_dir": str(tmp_path)}), _task()) \
            == "project notes"

    def test_missing_file_is_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.config.settings.resolve_working_dir", lambda raw: str(tmp_path))
        assert _reasoner()._load_background(_agent(), _task()) == ""

    def test_no_working_dir_is_empty(self):
        assert _reasoner()._load_background(_agent(), _task()) == ""

    def test_task_setting_takes_priority(self, tmp_path, monkeypatch):
        (tmp_path / "BACKGROUND.md").write_text("from task dir", encoding="utf-8")
        seen = {}

        def resolve(raw):
            seen["raw"] = raw
            return str(tmp_path)

        monkeypatch.setattr("app.config.settings.resolve_working_dir", resolve)
        r = _reasoner()
        r._load_background(_agent(settings={"working_dir": "/agent"}),
                           _task(settings={"working_dir": "/task"}))
        assert seen["raw"] == "/task"

    def test_read_error_is_swallowed(self, tmp_path, monkeypatch):
        bg = tmp_path / "BACKGROUND.md"
        bg.write_text("x", encoding="utf-8")
        monkeypatch.setattr("app.config.settings.resolve_working_dir", lambda raw: str(tmp_path))
        from pathlib import Path as P
        monkeypatch.setattr(P, "read_text",
                            lambda self, **k: (_ for _ in ()).throw(OSError("locked")))
        assert _reasoner()._load_background(_agent(), _task()) == ""


# ── agent identity / skill instructions ───────────────────────────────────────

class TestExtractAgentIdentity:
    def test_soul_and_role_from_capabilities(self):
        soul, role, instructions = _reasoner()._extract_agent_identity(_agent(), _task())
        assert soul == "SOUL" and role == "ROLE" and instructions == ""

    def test_empty_instruction_md_becomes_empty_string(self):
        agent = _agent(actor=AgentCapability(), observer=AgentCapability())
        soul, role, _ = _reasoner()._extract_agent_identity(agent, _task())
        assert soul == "" and role == ""

    def test_no_skill_registry_means_no_instructions(self):
        r = _reasoner(skill_registry=None)
        _, _, instructions = r._extract_agent_identity(
            _agent(), _task(settings={"skill_name": "pptx"}))
        assert instructions == ""

    def test_skill_instructions_loaded_and_prefixed(self):
        reg = MagicMock()
        reg.load_definition.return_value = SimpleNamespace(instructions="step 1")
        r = _reasoner(skill_registry=reg)
        _, _, instructions = r._extract_agent_identity(
            _agent(), _task(settings={"skill_name": "pptx"}))
        assert instructions == "Instructions for skill 'pptx':\nstep 1"

    def test_instructions_are_cached_on_the_task(self):
        reg = MagicMock()
        reg.load_definition.return_value = SimpleNamespace(instructions="body")
        svc = MagicMock()
        task = _task(settings={"skill_name": "pptx"})
        r = _reasoner(skill_registry=reg, task_svc=svc)
        r._extract_agent_identity(_agent(), task)
        assert "_skill_instructions_cache" in task.settings
        svc.save.assert_called_once_with(task)

    def test_unchanged_cache_is_not_resaved(self):
        reg = MagicMock()
        reg.load_definition.return_value = SimpleNamespace(instructions="body")
        svc = MagicMock()
        cached = "Instructions for skill 'pptx':\nbody"
        task = _task(settings={"skill_name": "pptx", "_skill_instructions_cache": cached})
        r = _reasoner(skill_registry=reg, task_svc=svc)
        r._extract_agent_identity(_agent(), task)
        assert not svc.save.called

    def test_cache_save_failure_is_swallowed(self):
        reg = MagicMock()
        reg.load_definition.return_value = SimpleNamespace(instructions="body")
        svc = MagicMock()
        svc.save.side_effect = RuntimeError("store down")
        r = _reasoner(skill_registry=reg, task_svc=svc)
        _, _, instructions = r._extract_agent_identity(
            _agent(), _task(settings={"skill_name": "pptx"}))
        assert instructions.startswith("Instructions for skill")

    def test_unavailable_skill_uses_the_cache(self):
        reg = MagicMock()
        reg.load_definition.return_value = None
        task = _task(settings={"skill_name": "pptx",
                               "_skill_instructions_cache": "cached body"})
        r = _reasoner(skill_registry=reg)
        _, _, instructions = r._extract_agent_identity(_agent(), task)
        assert instructions == "cached body"

    def test_unavailable_skill_without_cache_is_empty(self):
        reg = MagicMock()
        reg.load_definition.return_value = None
        r = _reasoner(skill_registry=reg)
        _, _, instructions = r._extract_agent_identity(
            _agent(), _task(settings={"skill_name": "pptx"}))
        assert instructions == ""

    def test_definition_without_instructions(self):
        reg = MagicMock()
        reg.load_definition.return_value = SimpleNamespace(instructions="")
        r = _reasoner(skill_registry=reg)
        _, _, instructions = r._extract_agent_identity(
            _agent(), _task(settings={"skill_name": "pptx"}))
        assert instructions == ""


# ── tool name resolution ──────────────────────────────────────────────────────

class TestToolNameResolution:
    def test_act_names_include_mcp_server_tools(self):
        reg = _tool_registry(server_tools={"srv": ["remote_a", "remote_b"]})
        r = _reasoner(tool_registry=reg)
        agent = _agent(actor=AgentCapability(tools=["read"], mcp_servers=["srv"]))
        assert r._resolve_act_tool_names(agent) == {"read", "remote_a", "remote_b"}

    def test_act_names_without_registry(self):
        r = Reasoner(_memory(), _blackboard(), tool_registry=None)
        agent = _agent(actor=AgentCapability(tools=["read"], mcp_servers=["srv"]))
        assert r._resolve_act_tool_names(agent) == {"read"}

    def test_observe_names_include_mcp_server_tools(self):
        reg = _tool_registry(server_tools={"osrv": ["o1"]})
        r = _reasoner(tool_registry=reg)
        agent = _agent(observer=AgentCapability(tools=["assess"], mcp_servers=["osrv"]))
        assert r._resolve_observe_tool_names(agent) == {"assess", "o1"}

    def test_empty_tool_lists(self):
        r = _reasoner()
        agent = _agent(actor=AgentCapability(), observer=AgentCapability())
        assert r._resolve_act_tool_names(agent) == set()
        assert r._resolve_observe_tool_names(agent) == set()

    def test_skill_executor_tools_dropped_for_skill_less_tasks(self):
        exec_names = Reasoner._skill_executor_tool_names()
        assert exec_names                     # the module does define some
        tools = [_llm_tool("read")] + [_llm_tool(n) for n in exec_names]
        reg = _tool_registry(tools=tools)
        agent = _agent(actor=AgentCapability(tools=["read"] + list(exec_names)))
        ctx = _reasoner(tool_registry=reg).reason(_session(), agent, _task())
        assert {res.name for res in ctx.actor_resources} == {"read"}

    def test_skill_executor_tools_kept_when_a_skill_is_assigned(self):
        exec_names = Reasoner._skill_executor_tool_names()
        tools = [_llm_tool("read")] + [_llm_tool(n) for n in exec_names]
        reg = _tool_registry(tools=tools)
        agent = _agent(actor=AgentCapability(tools=["read"] + list(exec_names)))
        skill_reg = MagicMock()
        skill_reg.fetch_skills.return_value = []
        skill_reg.load_definition.return_value = None
        r = _reasoner(tool_registry=reg, skill_registry=skill_reg)
        ctx = r.reason(_session(), agent, _task(settings={"skill_name": "pptx"}))
        assert {res.name for res in ctx.actor_resources} >= exec_names


# ── skill retrieval + cache ───────────────────────────────────────────────────

class TestRetrieveSkills:
    def _skill(self, name, desc="d"):
        return SimpleNamespace(name=name, description=desc)

    def test_no_registry_returns_empty(self):
        assert _reasoner(skill_registry=None)._retrieve_skills("g", _agent()) == []

    def test_returns_name_description_pairs(self):
        reg = MagicMock()
        reg.fetch_skills.return_value = [self._skill("a", "da"), self._skill("b", "db")]
        r = _reasoner(skill_registry=reg)
        assert r._retrieve_skills("g", _agent()) == [("a", "da"), ("b", "db")]

    def test_allowlist_filters(self):
        reg = MagicMock()
        reg.fetch_skills.return_value = [self._skill("a"), self._skill("b")]
        r = _reasoner(skill_registry=reg)
        agent = _agent(actor=AgentCapability(skills=["b"]))
        assert [n for n, _ in r._retrieve_skills("g", agent)] == ["b"]

    def test_empty_allowlist_means_all(self):
        reg = MagicMock()
        reg.fetch_skills.return_value = [self._skill("a"), self._skill("b")]
        r = _reasoner(skill_registry=reg)
        assert len(r._retrieve_skills("g", _agent())) == 2

    def test_result_is_cached(self):
        reg = MagicMock()
        reg.fetch_skills.return_value = [self._skill("a")]
        r = _reasoner(skill_registry=reg)
        r._retrieve_skills("g", _agent())
        r._retrieve_skills("g", _agent())
        assert reg.fetch_skills.call_count == 1

    def test_cache_expires_after_ttl(self, monkeypatch):
        reg = MagicMock()
        reg.fetch_skills.return_value = [self._skill("a")]
        r = _reasoner(skill_registry=reg)
        r._retrieve_skills("g", _agent())
        monkeypatch.setattr(time, "monotonic",
                            lambda: time.perf_counter() + reasoner_mod._SKILL_CACHE_TTL + 1)
        r._retrieve_skills("g", _agent())
        assert reg.fetch_skills.call_count == 2

    def test_cache_is_keyed_by_working_dir(self):
        reg = MagicMock()
        reg.fetch_skills.return_value = [self._skill("a")]
        r = _reasoner(skill_registry=reg)
        r._retrieve_skills("g", _agent(), CallContext(session_id="s1", working_dir="/w1"))
        r._retrieve_skills("g", _agent(), CallContext(session_id="s1", working_dir="/w2"))
        assert reg.fetch_skills.call_count == 2

    def test_ctx_is_forwarded(self):
        reg = MagicMock()
        reg.fetch_skills.return_value = []
        r = _reasoner(skill_registry=reg)
        ctx = CallContext(session_id="s1", agent_id="a1", working_dir="/w")
        r._retrieve_skills("g", _agent(), ctx)
        assert reg.fetch_skills.call_args.args[0] is ctx


class TestEvictSession:
    def test_drops_only_the_named_session(self):
        r = _reasoner()
        r._skill_cache[("s1", "a1", "")] = (0.0, [])
        r._skill_cache[("s2", "a1", "")] = (0.0, [])
        r.evict_session("s1")
        assert list(r._skill_cache) == [("s2", "a1", "")]

    def test_unknown_session_is_a_noop(self):
        r = _reasoner()
        r.evict_session("ghost")


# ── description compression ───────────────────────────────────────────────────

class TestCompress:
    def test_no_summarizer_returns_empty_map(self):
        assert _reasoner(resource_summarizer=None)._compress([("a", "d")], "p", "m") == {}

    def test_no_items_returns_empty_map(self):
        assert _reasoner(resource_summarizer=MagicMock())._compress([], "p", "m") == {}

    def test_delegates_to_the_summarizer(self):
        s = MagicMock()
        s.compress_many.return_value = {("a", "d"): "short"}
        r = _reasoner(resource_summarizer=s)
        assert r._compress([("a", "d")], "prov", "mod") == {("a", "d"): "short"}
        assert s.compress_many.call_args.kwargs == {"provider": "prov", "model": "mod"}

    def test_compressed_description_replaces_the_llm_tool_description(self):
        s = MagicMock()
        s.compress_many.return_value = {("read", "Read a file"): "reads files"}
        reg = _tool_registry(tools=[_llm_tool("read", "Read a file")])
        r = _reasoner(tool_registry=reg, resource_summarizer=s)
        ctx = r.reason(_session(), _agent(), _task())
        res = ctx.actor_resources[0]
        assert res.description == "reads files"
        assert res.llm_tool.description == "reads files"

    def test_uncompressed_tool_keeps_its_original_object(self):
        tool = _llm_tool("read", "Read a file")
        s = MagicMock()
        s.compress_many.return_value = {}
        reg = _tool_registry(tools=[tool])
        r = _reasoner(tool_registry=reg, resource_summarizer=s)
        ctx = r.reason(_session(), _agent(), _task())
        assert ctx.actor_resources[0].llm_tool is tool

    def test_tool_without_description(self):
        reg = _tool_registry(tools=[LLMTool(name="read",
                                            input_schema=InputSchema(properties={}, require=[]))])
        ctx = _reasoner(tool_registry=reg).reason(_session(), _agent(), _task())
        assert ctx.actor_resources[0].description == ""


# ── sub-agent metadata ────────────────────────────────────────────────────────

class TestCollectAgentMetas:
    def _loader(self, templates, own_subagents=None):
        loader = MagicMock()
        loader.list_details.return_value = [
            SimpleNamespace(name=n, description=d) for n, d in templates]
        loader.get_details_by_id.return_value = SimpleNamespace(
            actor_capability=SimpleNamespace(subagents=own_subagents or []))
        return loader

    def test_no_spawn_permission_means_none(self):
        r = _reasoner(agent_template_loader=self._loader([("planner", "d")]))
        assert r._collect_agent_metas(_agent(has_spawn_permission=False)) == []

    def test_no_loader_means_none(self):
        r = _reasoner(agent_template_loader=None)
        assert r._collect_agent_metas(_agent(has_spawn_permission=True)) == []

    def test_all_templates_visible_by_default(self):
        loader = self._loader([("planner", "dp"), ("worker", "dw")])
        r = _reasoner(agent_template_loader=loader)
        metas = r._collect_agent_metas(_agent(has_spawn_permission=True))
        assert metas == [("planner", "dp"), ("worker", "dw")]

    def test_allowlist_restricts_visibility(self):
        loader = self._loader([("planner", "dp"), ("worker", "dw")],
                              own_subagents=["worker"])
        r = _reasoner(agent_template_loader=loader)
        metas = r._collect_agent_metas(_agent(has_spawn_permission=True))
        assert metas == [("worker", "dw")]

    def test_workspace_dir_forwarded(self):
        loader = self._loader([])
        r = _reasoner(agent_template_loader=loader)
        r._collect_agent_metas(_agent(has_spawn_permission=True,
                                      settings={"working_dir": "/w"}))
        loader.list_details.assert_called_once_with("/w")

    def test_agent_without_template_id_skips_the_allowlist_lookup(self):
        loader = self._loader([("planner", "d")])
        r = _reasoner(agent_template_loader=loader)
        r._collect_agent_metas(_agent(has_spawn_permission=True, template_id=""))
        assert not loader.get_details_by_id.called

    def test_agent_resources_appear_in_the_context(self):
        loader = self._loader([("planner", "plans things")])
        r = _reasoner(agent_template_loader=loader)
        ctx = r.reason(_session(), _agent(has_spawn_permission=True), _task())
        agents = [res for res in ctx.actor_resources if res.kind == "agent"]
        assert [(a.name, a.description) for a in agents] == [("planner", "plans things")]


class TestResourceOrdering:
    def test_skills_then_tools_then_agents(self):
        skill_reg = MagicMock()
        skill_reg.fetch_skills.return_value = [SimpleNamespace(name="sk", description="ds")]
        loader = MagicMock()
        loader.list_details.return_value = [SimpleNamespace(name="ag", description="da")]
        loader.get_details_by_id.return_value = SimpleNamespace(
            actor_capability=SimpleNamespace(subagents=[]))
        r = _reasoner(skill_registry=skill_reg, agent_template_loader=loader)
        ctx = r.reason(_session(), _agent(has_spawn_permission=True), _task())
        assert [res.kind for res in ctx.actor_resources] == ["skill", "tool", "agent"]
