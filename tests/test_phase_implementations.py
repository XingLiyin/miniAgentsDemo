"""Phase implementation tests.

Covers:
  SecretString           (app/config/settings.py)
  TruncationStrategy     (app/runtime/compaction.py)
  SummarizationStrategy  (app/runtime/compaction.py)
  BaseChatClient ABC     (app/llm/client.py)
  MockChatClient         (app/llm/mock_client.py)
  LLMClient (AF-backed)  (app/llm/client.py)
  LLMRegistry            (app/llm/registry.py)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from unittest.mock import MagicMock

import pytest
from agent_framework import BaseChatClient as AFBaseChatClient
from agent_framework import ChatResponse, Content, Message

from app.llm.base import BaseChatClient
from app.llm.mock_client import MockChatClient
from app.llm.types import InputSchema, LLMMessage, LLMResponse, LLMTool, LLMUsage


# ── Shared AF stub ────────────────────────────────────────────────────────────

class DummyAFClient(AFBaseChatClient):
    """Minimal AF chat client used to validate the compatibility wrapper."""

    def __init__(self, response: ChatResponse) -> None:
        super().__init__()
        self._response = response
        self.last_messages: Sequence[Message] = []
        self.last_options: Mapping[str, Any] | None = None

    def get_response(
        self,
        messages: Sequence[Message],
        *,
        stream: bool = False,
        options: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ):
        self.last_messages = list(messages)
        self.last_options = options

        async def _resp() -> ChatResponse:
            return self._response

        return _resp()

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        return self.get_response(messages=messages, stream=stream, options=options, **kwargs)


# ── SecretString ──────────────────────────────────────────────────────────────

class TestSecretString:
    def test_repr_is_masked(self):
        from app.config.settings import SecretString
        s = SecretString("sk-secret-key")
        assert "sk-secret-key" not in repr(s)
        assert "*" in repr(s)

    def test_str_returns_actual_value(self):
        from app.config.settings import SecretString
        s = SecretString("sk-secret-key")
        assert str(s) == "sk-secret-key"
        assert s == "sk-secret-key"

    def test_settings_api_key_is_secret_string(self):
        from app.config.settings import SecretString, Settings
        s = Settings(llm_openai_api_key="my-key")
        assert isinstance(s.llm_openai_api_key, SecretString)
        assert s.llm_openai_api_key == "my-key"
        assert "my-key" not in repr(s.llm_openai_api_key)

    def test_api_key_not_leaked_in_settings_repr(self):
        from app.config.settings import Settings
        s = Settings(llm_openai_api_key="super-secret")
        assert "super-secret" not in repr(s)


# ── CompactionStrategy ────────────────────────────────────────────────────────

def _make_messages(n: int) -> list[dict]:
    return [{"role": "user", "content": f"msg {i}"} for i in range(n)]


class TestTruncationStrategy:
    def test_no_op_when_under_limit(self):
        from app.runtime.compaction import TruncationStrategy
        strategy = TruncationStrategy(keep_last=5)
        msgs = _make_messages(3)
        kept, summary = strategy.compact(msgs)
        assert kept == msgs
        assert summary == ""

    def test_keeps_last_n_messages(self):
        from app.runtime.compaction import TruncationStrategy
        strategy = TruncationStrategy(keep_last=3)
        msgs = _make_messages(7)
        kept, summary = strategy.compact(msgs)
        assert len(kept) == 3
        assert kept == msgs[-3:]

    def test_summary_reports_dropped_count(self):
        from app.runtime.compaction import TruncationStrategy
        strategy = TruncationStrategy(keep_last=2)
        msgs = _make_messages(5)
        _, summary = strategy.compact(msgs)
        assert "3" in summary

    def test_exact_limit_is_no_op(self):
        from app.runtime.compaction import TruncationStrategy
        strategy = TruncationStrategy(keep_last=5)
        msgs = _make_messages(5)
        kept, summary = strategy.compact(msgs)
        assert kept == msgs
        assert summary == ""


class TestSummarizationStrategy:
    def _make_mock_llm(self, summary_text: str = "Summary of history.") -> MagicMock:
        mock_llm = MagicMock()
        mock_llm.send_message.return_value = LLMResponse(
            text=summary_text,
            usage=LLMUsage(total_tokens=20),
        )
        return mock_llm

    def test_no_op_when_under_limit(self):
        from app.runtime.compaction import SummarizationStrategy
        mock_llm = self._make_mock_llm()
        strategy = SummarizationStrategy(llm_client=mock_llm, keep_last=10)
        msgs = _make_messages(5)
        kept, summary = strategy.compact(msgs)
        assert kept == msgs
        assert summary == ""
        mock_llm.send_message.assert_not_called()

    def test_reduces_messages_when_over_limit(self):
        from app.runtime.compaction import SummarizationStrategy
        mock_llm = self._make_mock_llm("Short summary.")
        strategy = SummarizationStrategy(llm_client=mock_llm, keep_last=2)
        msgs = _make_messages(5)
        kept, summary = strategy.compact(msgs)
        assert len(kept) <= len(msgs)
        assert summary != ""

    def test_fallback_on_llm_error(self):
        from app.runtime.compaction import SummarizationStrategy
        mock_llm = MagicMock()
        mock_llm.send_message.side_effect = RuntimeError("LLM down")
        strategy = SummarizationStrategy(llm_client=mock_llm, keep_last=2)
        msgs = _make_messages(5)
        kept, summary = strategy.compact(msgs)
        assert len(kept) <= len(msgs)
        assert summary != ""


# ── BaseChatClient ABC ────────────────────────────────────────────────────────

class TestBaseChatClient:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            BaseChatClient()  # type: ignore[abstract]

    def test_llm_client_is_subclass(self):
        assert issubclass(LLMClient, BaseChatClient)

    def test_mock_chat_client_is_subclass(self):
        assert issubclass(MockChatClient, BaseChatClient)


# ── MockChatClient ────────────────────────────────────────────────────────────

class TestMockChatClient:
    def test_send_message_returns_fixed_text(self):
        client = MockChatClient(response_text="pong")
        resp = client.send_message([LLMMessage(role="user", content="ping")])
        assert resp.text == "pong"
        assert resp.usage is not None
        assert resp.usage.total_tokens == 15

    def test_parse_response_returns_parsed_response(self):
        client = MockChatClient()
        resp = LLMResponse(text="test")
        parsed = client.parse_response(resp)
        assert parsed.text == "test"
        assert len(parsed.blocks) == 1


# ── LLMClient (AF-backed) ─────────────────────────────────────────────────────

class TestAFBackedLLMClient:
    def _make_client(self, response: ChatResponse | None = None):
        provider = DummyAFClient(
            response or ChatResponse(
                messages=[Message(role="assistant", contents=["ok"])],
                usage_details={
                    "input_token_count": 5,
                    "output_token_count": 3,
                    "total_token_count": 8,
                },
            )
        )
        return LLMClient(provider), provider

    def test_send_message_returns_text(self):
        client, _ = self._make_client()
        resp = client.send_message([LLMMessage(role="user", content="hi")])
        assert resp.text == "ok"
        assert resp.usage is not None
        assert resp.usage.total_tokens == 8

    def test_system_prompt_and_tools_mapped_to_af_options(self):
        client, provider = self._make_client()
        client.send_message(
            [LLMMessage(role="user", content="hi")],
            system_prompt="You are helpful.",
            tools=[
                LLMTool(
                    name="read_file",
                    description="Read a file",
                    input_schema=InputSchema(
                        properties={"path": {"type": "string"}},
                        require=["path"],
                    ),
                )
            ],
            max_tokens=128,
        )
        assert provider.last_options is not None
        assert provider.last_options["instructions"] == "You are helpful."
        assert provider.last_options["max_tokens"] == 128
        assert len(provider.last_options["tools"]) == 1
        assert provider.last_options["tools"][0].name == "read_file"

    def test_parse_response_extracts_tool_calls(self):
        client, _ = self._make_client(
            ChatResponse(
                messages=[
                    Message(
                        role="assistant",
                        contents=[
                            Content.from_text("let me check"),
                            Content.from_function_call(
                                call_id="call-1",
                                name="search_web",
                                arguments='{"query":"agent framework"}',
                            ),
                        ],
                    )
                ]
            )
        )
        parsed = client.parse_response(
            client.send_message([LLMMessage(role="user", content="search it")])
        )
        assert parsed.text == "let me check"
        assert len(parsed.tool_calls) == 1
        assert parsed.tool_calls[0].name == "search_web"
        assert parsed.tool_calls[0].input == {"query": "agent framework"}


# ── LLMRegistry ───────────────────────────────────────────────────────────────

class TestLLMRegistry:
    def _make_registry(self):
        from app.llm.registry import LLMRegistry
        return LLMRegistry()

    def _openai_config(self, name: str = "gpt"):
        from app.llm.registry import LLMProviderConfig
        return LLMProviderConfig(
            name=name, style="openai",
            api_key="sk-test", base_url="https://api.openai.com",
            model="gpt-4.1-mini",
        )

    def _anthropic_config(self, name: str = "claude"):
        from app.llm.registry import LLMProviderConfig
        return LLMProviderConfig(
            name=name, style="anthropic",
            api_key="ant-test", base_url="https://api.anthropic.com",
            model="claude-sonnet-4-6",
        )

    def _mock_client(self):
        return MockChatClient(response_text="test")

    def test_register_and_get_client(self):
        reg = self._make_registry()
        reg.register(self._openai_config(), self._mock_client(), persist=False)
        client = reg.get_client("gpt")
        assert isinstance(client, BaseChatClient)

    def test_duplicate_register_raises(self):
        reg = self._make_registry()
        reg.register(self._openai_config(), self._mock_client(), persist=False)
        with pytest.raises(KeyError, match="already"):
            reg.register(self._openai_config(), self._mock_client(), persist=False)

    def test_get_unregistered_raises(self):
        reg = self._make_registry()
        with pytest.raises(KeyError, match="not registered"):
            reg.get_client("nonexistent")

    def test_is_registered(self):
        reg = self._make_registry()
        assert not reg.is_registered("gpt")
        reg.register(self._openai_config(), self._mock_client(), persist=False)
        assert reg.is_registered("gpt")

    def test_delete_removes_entry(self):
        reg = self._make_registry()
        reg.register(self._openai_config(), self._mock_client(), persist=False)
        mock_store = MagicMock()
        reg._store = mock_store
        reg.delete("gpt")
        assert not reg.is_registered("gpt")
        mock_store.delete.assert_called_once_with("gpt")

    def test_list_configs(self):
        reg = self._make_registry()
        reg.register(self._openai_config("a"), self._mock_client(), persist=False)
        reg.register(self._anthropic_config("b"), self._mock_client(), persist=False)
        names = [c.name for c in reg.list_configs()]
        assert set(names) == {"a", "b"}

    def test_get_config_returns_config(self):
        reg = self._make_registry()
        cfg = self._openai_config()
        reg.register(cfg, self._mock_client(), persist=False)
        got = reg.get_config("gpt")
        assert got.model == "gpt-4.1-mini"
        assert got.style == "openai"


# ── LoopGuard ─────────────────────────────────────────────────────────────────

class TestLoopGuard:
    def test_default_actor_max_tool_rounds(self):
        from app.domain.models.agent import LoopGuard
        g = LoopGuard()
        assert g.actor_max_tool_rounds == 5

    def test_to_dict_includes_actor_max_tool_rounds(self):
        from app.domain.models.agent import LoopGuard
        g = LoopGuard(actor_max_tool_rounds=8)
        d = g.to_dict()
        assert d["actor_max_tool_rounds"] == 8

    def test_from_dict_restores_actor_max_tool_rounds(self):
        from app.domain.models.agent import LoopGuard
        g = LoopGuard.from_dict({"turns_used": 1, "max_turns": 10, "actor_max_tool_rounds": 3})
        assert g.actor_max_tool_rounds == 3

    def test_from_dict_defaults_actor_max_tool_rounds(self):
        from app.domain.models.agent import LoopGuard
        g = LoopGuard.from_dict({})
        assert g.actor_max_tool_rounds == 5


# ── Planner ───────────────────────────────────────────────────────────────────

class _ToolCallMockClient(MockChatClient):
    """MockChatClient that returns a synthetic tool call in parse_response."""

    def __init__(self, tool_name: str, tool_input: dict) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._tool_input = tool_input

    def parse_response(self, response):
        from app.llm.types import ParsedResponse, ToolCallBlock
        return ParsedResponse(
            text="",
            blocks=[],
            tool_calls=[
                ToolCallBlock(
                    type="tool_call",
                    id="tc-1",
                    name=self._tool_name,
                    input=self._tool_input,
                )
            ],
        )


class _MockTaskSvc:
    """Minimal TaskService stub that records create() and create_plan_task() calls."""

    def __init__(self) -> None:
        self.created: list[dict] = []
        self.plan_tasks: list[dict] = []
        self._n = 0

    def create(self, **kwargs):
        self._n += 1
        task_id = f"mock-t{self._n}"
        self.created.append({"id": task_id, **kwargs})
        m = MagicMock()
        m.id = task_id
        return m

    def create_plan_task(
        self,
        session_id: str,
        creator_agent_id: str,
        title: str,
        description: str = "",
        *,
        inherit_memory: bool = True,
    ):
        self._n += 1
        task_id = f"mock-plan-t{self._n}"
        self.plan_tasks.append({
            "id": task_id,
            "session_id": session_id,
            "creator_agent_id": creator_agent_id,
            "title": title,
            "description": description,
        })
        m = MagicMock()
        m.id = task_id
        return m

    def transition(self, task_id: str, status: str) -> None:
        pass

    def get(self, task_id: str):
        pass


class _MockSessionSvc:
    def transition(self, session_id: str, status: str) -> None:
        pass


class TestSubmitPlan:
    """测试 AgentController._handle_submit_plan（原 Planner 测试）。

    Planner 类已删除，submit_plan 逻辑完全在 AgentController 内，
    通过 dispatch("submit_plan", args, agent, task) 直接验证。
    """

    def _make_agent(self):
        from app.domain.models.agent import Agent
        return Agent(id="a1", session_id="s1", template_id=None, name="test", status="RUNNING")

    def _make_task(self):
        t = MagicMock()
        t.id = "plan-t1"
        t.session_id = "s1"
        t.assigned_agent_id = "a1"
        return t

    def _make_controller(self, task_svc=None):
        from app.runtime.agent_controller import AgentController
        svc = task_svc or _MockTaskSvc()
        return AgentController(task_svc=svc, session_svc=_MockSessionSvc()), svc

    def _dispatch(self, args, task_svc=None):
        controller, svc = self._make_controller(task_svc)
        ctrl = controller.dispatch("submit_plan", args, self._make_agent(), self._make_task())
        return ctrl, svc

    def test_submit_plan_creates_tasks_and_returns_task_complete(self):
        from app.runtime.agent_controller import ControlSignal
        ctrl, svc = self._dispatch({
            "tasks": [{"title": "Do something", "description": "Accomplish X", "skill_name": None}]
        })
        assert ctrl.signal == ControlSignal.TASK_COMPLETE
        assert len(ctrl.signal_data["planned_task_ids"]) == 1
        assert ctrl.signal_data["titles"][0] == "Do something"

    def test_skill_name_propagated(self):
        svc = _MockTaskSvc()
        self._dispatch(
            {"tasks": [{"title": "Review code", "description": "Review PR", "skill_name": "code_review"}]},
            task_svc=svc,
        )
        assert svc.created[0]["inputs"].get("skill_name") == "code_review"

    def test_use_subagent_propagated(self):
        svc = _MockTaskSvc()
        self._dispatch(
            {
                "tasks": [
                    {"title": "Heavy computation", "description": "Run long analysis", "skill_name": None, "use_subagent": True},
                    {"title": "Quick summary", "description": "Summarize results", "skill_name": None, "use_subagent": False},
                ]
            },
            task_svc=svc,
        )
        assert svc.created[0]["inputs"].get("use_subagent") is True
        assert "use_subagent" not in svc.created[1]["inputs"]

    def test_use_subagent_defaults_to_false(self):
        svc = _MockTaskSvc()
        self._dispatch(
            {"tasks": [{"title": "Simple step", "description": "Do X", "skill_name": None}]},
            task_svc=svc,
        )
        assert "use_subagent" not in svc.created[0]["inputs"]

    def test_empty_tasks_returns_task_complete_with_no_ids(self):
        from app.runtime.agent_controller import ControlSignal
        ctrl, _ = self._dispatch({"tasks": []})
        assert ctrl.signal == ControlSignal.TASK_COMPLETE
        assert ctrl.signal_data["planned_task_ids"] == []

    def test_task_title_in_signal_data(self):
        ctrl, _ = self._dispatch({
            "tasks": [{"title": "Think", "description": "Think hard", "skill_name": None}]
        })
        assert ctrl.signal_data["titles"][0] == "Think"


# ── Observer ──────────────────────────────────────────────────────────────────

class TestObserver:
    def _make_session(self, goal="Test goal", token_used=0, token_budget=100_000):
        from app.domain.models.session import Session
        return Session(
            id="s1",
            goal=goal,
            status="RUNNING",
            template_id=None,
            root_agent_id=None,
            token_budget=token_budget,
            token_used=token_used,
        )

    def _make_agent(self, role_md=""):
        from app.domain.models.agent import Agent
        return Agent(
            id="a1", session_id="s1", template_id=None,
            name="test", status="RUNNING", role_md=role_md,
        )

    def _make_ctx(self):
        from app.runtime.types import ReasoningContext
        return ReasoningContext(
            mode="act",
            goal="Test goal",
            recent_messages=[],
            summary_text="Previous progress done.",
            blackboard_snippets=[],
            resources=[],
        )

    def _make_result(self, success=True, output="done"):
        from app.runtime.types import ActorResult
        return ActorResult(task_id="t1", success=success, output=output)

    def _make_task(self):
        task = MagicMock()
        task.id = "t1"
        task.title = "Test task"
        task.description = "Do something"
        task.session_id = "s1"
        task.assigned_agent_id = "a1"
        return task

    def _make_observer(self, client, task_svc=None):
        from app.runtime.agent_controller import AgentController
        from app.runtime.observer import Observer
        svc = task_svc or _MockTaskSvc()
        controller = AgentController(task_svc=svc, session_svc=_MockSessionSvc())
        return Observer(llm_client=client, agent_controller=controller), svc

    def test_llm_done_true(self):
        client = _ToolCallMockClient(
            "submit_observation",
            {"task_complete": True, "task_result": "Task done.", "done": True, "summary": "All done.", "reasoning": "Goal met."},
        )
        obs, _ = self._make_observer(client)
        verdict = obs.observe(self._make_session(), self._make_result(), self._make_ctx(), self._make_task(), self._make_agent())
        assert verdict.done is True
        assert verdict.task_success is True
        assert verdict.summary == "All done."

    def test_llm_done_false(self):
        client = _ToolCallMockClient(
            "submit_observation",
            {"task_complete": True, "task_result": "Task done.", "done": False, "summary": "Partial.", "reasoning": "More to do."},
        )
        obs, _ = self._make_observer(client)
        verdict = obs.observe(self._make_session(), self._make_result(), self._make_ctx(), self._make_task(), self._make_agent())
        assert verdict.done is False
        assert verdict.task_success is True

    def test_llm_task_failed(self):
        client = _ToolCallMockClient(
            "submit_observation",
            {"task_complete": False, "task_result": "Could not complete.", "done": False, "summary": "Failed.", "reasoning": "Error."},
        )
        obs, _ = self._make_observer(client)
        verdict = obs.observe(self._make_session(), self._make_result(), self._make_ctx(), self._make_task(), self._make_agent())
        assert verdict.task_success is False
        assert verdict.task_result == "Could not complete."

    def test_spawn_planner_creates_plan_task(self):
        client = _ToolCallMockClient(
            "submit_observation",
            {
                "task_complete": True,
                "task_result": "Found extra work.",
                "done": False,
                "summary": "Task done, more needed.",
                "reasoning": "Discovered follow-up tasks.",
                "spawn_planner": True,
            },
        )
        svc = _MockTaskSvc()
        obs, _ = self._make_observer(client, task_svc=svc)
        verdict = obs.observe(self._make_session(), self._make_result(), self._make_ctx(), self._make_task(), self._make_agent())
        assert len(svc.plan_tasks) == 1
        assert svc.plan_tasks[0]["title"] == "Re-plan: discover follow-up tasks"
        assert svc.plan_tasks[0]["session_id"] == "s1"

    def test_spawn_planner_skipped_when_done(self):
        """spawn_planner=True but done=True → no plan task created."""
        client = _ToolCallMockClient(
            "submit_observation",
            {"task_complete": True, "task_result": "All done.", "done": True, "summary": "Done.", "reasoning": ".", "spawn_planner": True},
        )
        svc = _MockTaskSvc()
        obs, _ = self._make_observer(client, task_svc=svc)
        obs.observe(self._make_session(), self._make_result(), self._make_ctx(), self._make_task(), self._make_agent())
        assert len(svc.plan_tasks) == 0

    def test_rule_fallback_on_llm_error(self):
        bad_client = MagicMock()
        bad_client.send_message.side_effect = RuntimeError("network error")
        obs, _ = self._make_observer(bad_client)
        verdict = obs.observe(self._make_session(), self._make_result(), self._make_ctx(), self._make_task(), self._make_agent())
        assert isinstance(verdict.done, bool)
        assert isinstance(verdict.task_success, bool)
        assert verdict.summary != ""

    def test_rule_fallback_forces_done_near_budget(self):
        bad_client = MagicMock()
        bad_client.send_message.side_effect = RuntimeError("fail")
        obs, _ = self._make_observer(bad_client)
        verdict = obs.observe(
            self._make_session(token_used=95, token_budget=100),
            self._make_result(),
            self._make_ctx(),
            self._make_task(),
            self._make_agent(),
        )
        assert verdict.done is True
        assert verdict.task_success is True
