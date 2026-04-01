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

from app.llm.base import BaseChatClient, LLMClient
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


class TestPlanner:
    def _make_ctx(self, goal: str = "Test goal"):
        from app.runtime.types import ReasoningContext
        return ReasoningContext(
            goal=goal,
            recent_messages=[],
            summary_text="",
            blackboard_snippets=[],
            relevant_tools=[],
            relevant_skills=[],
        )

    def _make_agent(self):
        from app.domain.models.agent import Agent
        return Agent(
            id="a1", session_id="s1", template_id=None,
            name="test", status="RUNNING",
        )

    def test_parse_submit_plan_returns_task_plan(self):
        from app.runtime.planner import Planner
        client = _ToolCallMockClient(
            "submit_plan",
            {
                "tasks": [
                    {
                        "type": "atomic",
                        "title": "Do something",
                        "description": "Accomplish X",
                        "skill_name": None,
                        "prompt": "",
                    }
                ]
            },
        )
        planner = Planner(llm_client=client)
        plan = planner.plan(self._make_ctx(), self._make_agent())
        assert len(plan.tasks) == 1
        assert plan.tasks[0].title == "Do something"
        assert plan.tasks[0].type == "atomic"
        assert plan.tasks[0].skill_name is None

    def test_skill_name_propagated(self):
        from app.runtime.planner import Planner
        client = _ToolCallMockClient(
            "submit_plan",
            {
                "tasks": [
                    {
                        "type": "atomic",
                        "title": "Review code",
                        "description": "Review PR",
                        "skill_name": "code_review",
                        "prompt": "",
                    }
                ]
            },
        )
        planner = Planner(llm_client=client)
        plan = planner.plan(self._make_ctx(), self._make_agent())
        assert plan.tasks[0].skill_name == "code_review"

    def test_no_tool_call_returns_empty_plan(self):
        from app.runtime.planner import Planner
        planner = Planner(llm_client=MockChatClient())
        plan = planner.plan(self._make_ctx(), self._make_agent())
        assert plan.tasks == []

    def test_unknown_task_type_defaults_to_atomic(self):
        from app.runtime.planner import Planner
        client = _ToolCallMockClient(
            "submit_plan",
            {
                "tasks": [
                    {
                        "type": "legacy_reasoning",
                        "title": "Think",
                        "description": "Think hard",
                        "skill_name": None,
                        "prompt": "",
                    }
                ]
            },
        )
        planner = Planner(llm_client=client)
        plan = planner.plan(self._make_ctx(), self._make_agent())
        assert plan.tasks[0].type == "atomic"


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

    def _make_ctx(self):
        from app.runtime.types import ReasoningContext
        return ReasoningContext(
            goal="Test goal",
            recent_messages=[],
            summary_text="Previous progress done.",
            blackboard_snippets=[],
            relevant_tools=[],
            relevant_skills=[],
        )

    def _make_result(self, success=True, output="done"):
        from app.runtime.types import ActorResult
        return ActorResult(task_id="t1", success=success, output=output)

    def test_llm_done_true(self):
        from app.runtime.observer import Observer
        client = _ToolCallMockClient(
            "submit_observation",
            {"done": True, "summary": "All done.", "reasoning": "Goal met."},
        )
        obs = Observer(llm_client=client)
        verdict = obs.observe(self._make_session(), [self._make_result()], self._make_ctx())
        assert verdict.done is True
        assert verdict.summary == "All done."

    def test_llm_done_false(self):
        from app.runtime.observer import Observer
        client = _ToolCallMockClient(
            "submit_observation",
            {"done": False, "summary": "Partial.", "reasoning": "More to do."},
        )
        obs = Observer(llm_client=client)
        verdict = obs.observe(self._make_session(), [self._make_result()], self._make_ctx())
        assert verdict.done is False

    def test_rule_fallback_on_llm_error(self):
        from app.runtime.observer import Observer
        bad_client = MagicMock()
        bad_client.send_message.side_effect = RuntimeError("network error")
        obs = Observer(llm_client=bad_client)
        verdict = obs.observe(self._make_session(), [self._make_result()], self._make_ctx())
        # Rule fallback should not raise and should return a verdict
        assert isinstance(verdict.done, bool)
        assert verdict.summary != ""

    def test_rule_fallback_forces_done_near_budget(self):
        from app.runtime.observer import Observer
        bad_client = MagicMock()
        bad_client.send_message.side_effect = RuntimeError("fail")
        obs = Observer(llm_client=bad_client)
        # token_used = 95%, token_budget = 100
        verdict = obs.observe(
            self._make_session(token_used=95, token_budget=100),
            [self._make_result()],
            self._make_ctx(),
        )
        assert verdict.done is True
