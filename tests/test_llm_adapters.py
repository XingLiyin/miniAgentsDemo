"""LLM client compatibility tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent_framework import BaseChatClient as AFBaseChatClient
from agent_framework import ChatResponse, Content, Message

from app.llm.base import LLMClient
from app.llm.types import InputSchema, LLMMessage, LLMTool


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

        async def _response() -> ChatResponse:
            return self._response

        return _response()

    def _inner_get_response(
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ):
        return self.get_response(messages=messages, stream=stream, options=options, **kwargs)


def test_llm_client_send_message_bridges_messages_and_tools() -> None:
    provider = DummyAFClient(
        ChatResponse(
            messages=[Message(role="assistant", contents=["ok"])],
            usage_details={
                "input_token_count": 1,
                "output_token_count": 2,
                "total_token_count": 3,
            },
        )
    )
    client = LLMClient(provider)

    resp = client.send_message(
        messages=[LLMMessage(role="user", content="hi")],
        system_prompt="You are helpful.",
        tools=[
            LLMTool(
                name="read_file",
                description="Read a file.",
                input_schema=InputSchema(
                    properties={"path": {"type": "string"}},
                    require=["path"],
                ),
            )
        ],
        temperature=0.2,
        max_tokens=64,
    )

    assert resp.text == "ok"
    assert resp.usage is not None
    assert resp.usage.total_tokens == 3
    assert list(provider.last_messages)[0].role == "user"
    assert list(provider.last_messages)[0].text == "hi"
    assert provider.last_options is not None
    assert provider.last_options["instructions"] == "You are helpful."
    assert provider.last_options["temperature"] == 0.2
    assert provider.last_options["max_tokens"] == 64
    assert len(provider.last_options["tools"]) == 1
    assert provider.last_options["tools"][0].name == "read_file"


def test_llm_client_parse_response_extracts_tool_calls() -> None:
    provider = DummyAFClient(
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
    client = LLMClient(provider)

    resp = client.send_message([LLMMessage(role="user", content="search it")])
    parsed = client.parse_response(resp)

    assert parsed.text == "let me check"
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].id == "call-1"
    assert parsed.tool_calls[0].name == "search_web"
    assert parsed.tool_calls[0].input == {"query": "agent framework"}
