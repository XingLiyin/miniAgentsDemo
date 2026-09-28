"""LLM adapter tests: OpenAI + Anthropic, non-streaming and SSE streaming.

Replaces the previous `agent_framework`-based suite; that dependency was dropped
in favour of the in-tree adapters (app/llm/openai_adapter.py, anthropic_adapter.py).
Transports are faked, so no network is touched.
"""

from __future__ import annotations

import json

import pytest

from app.llm import anthropic_adapter as aa
from app.llm import openai_adapter as oa
from app.llm.anthropic_adapter import AnthropicAdapter
from app.llm.base import BaseAdapter, BaseChatClient, StreamTransport, Transport
from app.llm.mock_adapter import MockAdapter
from app.llm.openai_adapter import OpenAIAdapter
from app.llm.types import (
    DocumentPart, ImageBlock, ImagePart, InputSchema, LLMMessage, LLMRequest,
    LLMResponse, LLMTool, LLMUsage, TextBlock, TextPart, ToolCallBlock,
    content_from_raw, content_to_text,
)


# ── fakes ─────────────────────────────────────────────────────────────────────

class FakeTransport:
    """Records the last post() call and returns a canned JSON body."""

    def __init__(self, response: dict | None = None):
        self.response = response or {}
        self.calls: list[dict] = []

    def post(self, url, headers, json, timeout):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return self.response

    @property
    def last(self) -> dict:
        return self.calls[-1]


class FakeStreamTransport(FakeTransport):
    """Yields pre-baked SSE payload lines; optionally raises mid-stream."""

    def __init__(self, lines: list[str] | None = None, raise_at: int | None = None,
                 error: Exception | None = None):
        super().__init__()
        self.lines = lines or []
        self.raise_at = raise_at
        self.error = error or RuntimeError("boom")
        self.stream_calls: list[dict] = []

    def stream_post(self, url, headers, json, timeout):
        self.stream_calls.append({"url": url, "headers": headers, "json": json})
        for i, line in enumerate(self.lines):
            if self.raise_at is not None and i == self.raise_at:
                raise self.error
            yield line
        if self.raise_at is not None and self.raise_at >= len(self.lines):
            raise self.error


def _tool(name="read", desc="Read a file") -> LLMTool:
    return LLMTool(
        name=name, description=desc,
        input_schema=InputSchema(properties={"path": {"type": "string"}}, require=["path"]),
    )


def _sse(obj: dict) -> str:
    return json.dumps(obj)


# ── types.py helpers ──────────────────────────────────────────────────────────

class TestContentHelpers:
    def test_content_to_text_str(self):
        assert content_to_text("hello") == "hello"

    def test_content_to_text_none(self):
        assert content_to_text(None) == ""

    def test_content_to_text_parts(self):
        assert content_to_text([TextPart(text="a"), TextPart(text="b")]) == "a\nb"

    def test_content_to_text_skips_non_text_parts(self):
        content = [TextPart(text="a"), ImagePart(data="xx"), DocumentPart(data="yy")]
        assert content_to_text(content) == "a"

    def test_content_to_text_dicts(self):
        content = [{"type": "text", "text": "a"}, {"type": "image", "data": "x"}]
        assert content_to_text(content) == "a"

    def test_content_from_raw_str_passthrough(self):
        assert content_from_raw("hi") == "hi"

    def test_content_from_raw_non_list_stringified(self):
        assert content_from_raw(42) == "42"

    def test_content_from_raw_rebuilds_parts(self):
        parts = content_from_raw([
            {"type": "text", "text": "t"},
            {"type": "image", "data": "d", "media_type": "image/png", "source_type": "base64"},
            {"type": "document", "data": "pdf", "media_type": "application/pdf"},
            {"type": "unknown"},
        ])
        assert isinstance(parts[0], TextPart) and parts[0].text == "t"
        assert isinstance(parts[1], ImagePart) and parts[1].media_type == "image/png"
        assert isinstance(parts[2], DocumentPart) and parts[2].data == "pdf"
        assert len(parts) == 3  # unknown type dropped

    def test_content_from_raw_keeps_instances(self):
        tp = TextPart(text="x")
        assert content_from_raw([tp])[0] is tp

    def test_input_schema_to_dict(self):
        s = InputSchema(properties={"a": {"type": "string"}}, require=["a"])
        assert s.to_dict() == {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}


class TestLLMToolPromptText:
    def test_required_param(self):
        assert _tool().to_prompt_text() == "read(path: string) — Read a file"

    def test_no_description_omits_dash(self):
        t = LLMTool(name="f", input_schema=InputSchema(properties={}, require=[]))
        assert t.to_prompt_text() == "f()"

    def test_optional_param_gets_question_mark(self):
        t = LLMTool(name="f", input_schema=InputSchema(properties={"x": {"type": "integer"}}, require=[]))
        assert t.to_prompt_text() == "f(x?: integer)"

    def test_default_is_rendered(self):
        t = LLMTool(name="f", input_schema=InputSchema(
            properties={"x": {"type": "integer", "default": 3}}, require=[]))
        assert t.to_prompt_text() == "f(x: integer = 3)"

    def test_array_of_object_expands_fields(self):
        t = LLMTool(name="f", input_schema=InputSchema(properties={
            "items": {"type": "array", "items": {
                "type": "object", "properties": {"a": {}, "b": {}}, "required": ["a"]}},
        }, require=["items"]))
        assert t.to_prompt_text() == "f(items: [{a, b?}])"

    def test_plain_array(self):
        t = LLMTool(name="f", input_schema=InputSchema(
            properties={"xs": {"type": "array"}}, require=["xs"]))
        assert t.to_prompt_text() == "f(xs: array)"

    def test_missing_type_is_any(self):
        t = LLMTool(name="f", input_schema=InputSchema(properties={"x": {}}, require=["x"]))
        assert t.to_prompt_text() == "f(x: any)"


# ── OpenAI: payload building ──────────────────────────────────────────────────

class TestOpenAIPayload:
    def _adapter(self, base="https://api.openai.com/v1", resp=None):
        t = FakeTransport(resp or {"choices": [{"message": {"content": "hi"}}]})
        return OpenAIAdapter("sk-test", base, t), t

    def test_headers_carry_bearer_token(self):
        ad, t = self._adapter()
        ad.complete(LLMRequest(model="gpt-4", messages=[]))
        assert t.last["headers"]["Authorization"] == "Bearer sk-test"
        assert t.last["headers"]["Content-Type"] == "application/json"

    def test_all_optional_params_forwarded(self):
        ad, t = self._adapter()
        ad.complete(LLMRequest(
            model="gpt-4", messages=[LLMMessage(role="user", content="q")],
            tools=[_tool()], temperature=0.5, max_tokens=99, top_p=0.9, stop=["END"],
        ))
        p = t.last["json"]
        assert p["temperature"] == 0.5 and p["max_tokens"] == 99
        assert p["top_p"] == 0.9 and p["stop"] == ["END"]
        assert p["stream"] is False
        assert p["tools"][0]["function"]["name"] == "read"
        assert p["tools"][0]["function"]["parameters"]["required"] == ["path"]

    def test_omits_unset_params(self):
        ad, t = self._adapter()
        ad.complete(LLMRequest(model="gpt-4", messages=[]))
        p = t.last["json"]
        for k in ("temperature", "max_tokens", "top_p", "stop", "tools"):
            assert k not in p

    def test_non_function_tool_rejected(self):
        with pytest.raises(ValueError, match="function"):
            oa._map_openai_tools([LLMTool(name="x", type="web_search")])

    @pytest.mark.parametrize("base,expected", [
        ("https://api.openai.com/v1", "https://api.openai.com/v1/chat/completions"),
        ("https://api.openai.com", "https://api.openai.com/v1/chat/completions"),
        ("https://x.com/api/paas/v4", "https://x.com/api/paas/v4/chat/completions"),
        ("https://x.com/v1/chat/completions", "https://x.com/v1/chat/completions"),
        ("https://x.com/V2", "https://x.com/V2/chat/completions"),
        ("https://x.com/v12", "https://x.com/v12/chat/completions"),
    ])
    def test_chat_url_resolution(self, base, expected):
        assert oa._resolve_chat_url(base) == expected

    def test_trailing_slash_stripped(self):
        ad, t = self._adapter(base="https://api.openai.com/v1/")
        ad.complete(LLMRequest(model="m", messages=[]))
        assert t.last["url"] == "https://api.openai.com/v1/chat/completions"


class TestOpenAISystemPromptMerge:
    def test_prepends_when_no_system_message(self):
        msgs = oa._merge_system_prompt(LLMRequest(
            model="m", messages=[LLMMessage(role="user", content="u")], system_prompt="SYS"))
        assert msgs[0].role == "system" and msgs[0].content == "SYS"

    def test_merges_into_existing_system_message(self):
        msgs = oa._merge_system_prompt(LLMRequest(
            model="m",
            messages=[LLMMessage(role="system", content="OLD"), LLMMessage(role="user", content="u")],
            system_prompt="NEW"))
        assert msgs[0].content == "NEW\nOLD"
        assert len(msgs) == 2

    def test_no_system_prompt_returns_messages_unchanged(self):
        orig = [LLMMessage(role="user", content="u")]
        assert oa._merge_system_prompt(LLMRequest(model="m", messages=orig)) == orig


class TestOpenAISerializeMessages:
    def test_plain_roles(self):
        out = oa._serialize_messages_openai([
            LLMMessage(role="system", content="s"),
            LLMMessage(role="user", content="u"),
            LLMMessage(role="assistant", content="a"),
        ])
        assert out == [{"role": "system", "content": "s"},
                       {"role": "user", "content": "u"},
                       {"role": "assistant", "content": "a"}]

    def test_assistant_tool_calls_serialized(self):
        out = oa._serialize_messages_openai([LLMMessage(
            role="assistant", content="calling",
            tool_calls=[{"id": "tc1", "name": "read", "input": {"path": "f"}}])])
        entry = out[0]
        assert entry["content"] == "calling"
        assert entry["tool_calls"][0]["id"] == "tc1"
        assert entry["tool_calls"][0]["type"] == "function"
        assert json.loads(entry["tool_calls"][0]["function"]["arguments"]) == {"path": "f"}

    def test_assistant_tool_calls_with_reasoning_and_no_text(self):
        out = oa._serialize_messages_openai([LLMMessage(
            role="assistant", content="", reasoning_content="think",
            tool_calls=[{"id": "t", "name": "n", "input": {}}])])
        assert out[0]["reasoning_content"] == "think"
        assert "content" not in out[0]

    def test_tool_message_with_id(self):
        out = oa._serialize_messages_openai([
            LLMMessage(role="tool", content="res", tool_call_id="tc1")])
        assert out == [{"role": "tool", "tool_call_id": "tc1", "content": "res"}]

    def test_tool_message_without_id_downgrades_to_user(self):
        out = oa._serialize_messages_openai([LLMMessage(role="tool", content="res")])
        assert out == [{"role": "user", "content": "res"}]

    def test_multimodal_user_content(self):
        out = oa._serialize_messages_openai([LLMMessage(role="user", content=[
            TextPart(text="look"),
            ImagePart(data="B64", media_type="image/png"),
            ImagePart(data="https://i/x.png", source_type="url"),
            DocumentPart(data="PDF", media_type="application/pdf"),
            TextPart(text=""),   # empty text dropped
        ])])
        blocks = out[0]["content"]
        assert blocks[0] == {"type": "text", "text": "look"}
        assert blocks[1]["image_url"]["url"] == "data:image/png;base64,B64"
        assert blocks[2]["image_url"]["url"] == "https://i/x.png"
        assert blocks[3]["image_url"]["url"] == "data:application/pdf;base64,PDF"
        assert len(blocks) == 4

    def test_empty_part_list_becomes_empty_string(self):
        out = oa._serialize_messages_openai([LLMMessage(role="user", content=[])])
        assert out[0]["content"] == ""


class TestOpenAIParseResponse:
    def _parse(self, raw: dict, usage=None):
        ad = OpenAIAdapter("k", "https://x/v1", FakeTransport())
        return ad.parse_response(LLMResponse(text="", raw=raw, usage=usage))

    def test_text_content(self):
        p = self._parse({"choices": [{"message": {"content": "hello"}}]})
        assert p.text == "hello"
        assert isinstance(p.blocks[0], TextBlock)

    def test_no_choices_yields_empty(self):
        p = self._parse({})
        assert p.text == "" and p.blocks == [] and p.tool_calls == []

    def test_empty_raw(self):
        ad = OpenAIAdapter("k", "https://x/v1", FakeTransport())
        p = ad.parse_response(LLMResponse(text=""))
        assert p.text == ""

    def test_list_content_text_parts(self):
        p = self._parse({"choices": [{"message": {"content": [
            {"type": "text", "text": "a"},
            {"type": "text", "text": ""},
            {"type": "other"},
        ]}}]})
        assert p.text == "a"

    def test_non_str_non_list_content(self):
        p = self._parse({"choices": [{"message": {"content": None}}]})
        assert p.text == ""

    def test_tool_calls_parsed(self):
        p = self._parse({"choices": [{"message": {"tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read", "arguments": '{"path": "f"}'}}]}}]})
        assert len(p.tool_calls) == 1
        tc = p.tool_calls[0]
        assert tc.id == "c1" and tc.name == "read" and tc.input == {"path": "f"}

    def test_tool_call_bad_json_kept_raw(self):
        p = self._parse({"choices": [{"message": {"tool_calls": [
            {"id": "c1", "function": {"name": "n", "arguments": "{oops"}}]}}]})
        assert p.tool_calls[0].input == {"_raw_arguments": "{oops"}

    def test_tool_call_empty_arguments(self):
        p = self._parse({"choices": [{"message": {"tool_calls": [
            {"id": "c1", "function": {"name": "n", "arguments": ""}}]}}]})
        assert p.tool_calls[0].input == {}
        assert p.tool_calls[0].tool_type == "function"

    def test_image_data_url_and_plain_url(self):
        p = self._parse({"choices": [{"message": {"content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
            {"type": "image_url", "image_url": {"url": "https://i/x.png"}},
        ]}}]})
        assert p.images[0].media_type == "image/png"
        assert p.images[0].data == "AAA"
        assert p.images[1].source_type == "url"

    def test_usage_passthrough(self):
        u = LLMUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3)
        assert self._parse({"choices": []}, usage=u).usage is u

    @pytest.mark.parametrize("url,expected", [
        ("data:image/png;base64,XX", ("image/png", "XX")),
        ("nonsense", ("", "nonsense")),
    ])
    def test_parse_data_url(self, url, expected):
        assert oa._parse_data_url(url) == expected


class TestOpenAICompleteExtraction:
    def test_text_and_usage(self):
        t = FakeTransport({
            "choices": [{"message": {"content": "answer"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        })
        resp = OpenAIAdapter("k", "https://x/v1", t).complete(LLMRequest(model="m", messages=[]))
        assert resp.text == "answer"
        assert resp.usage.total_tokens == 10

    def test_no_usage_is_none(self):
        t = FakeTransport({"choices": [{"message": {"content": "a"}}]})
        assert OpenAIAdapter("k", "https://x/v1", t).complete(
            LLMRequest(model="m", messages=[])).usage is None

    def test_no_choices_empty_text(self):
        t = FakeTransport({})
        assert OpenAIAdapter("k", "https://x/v1", t).complete(
            LLMRequest(model="m", messages=[])).text == ""


class TestOpenAIStream:
    def _stream(self, lines, **kw):
        t = FakeStreamTransport(lines, **kw)
        ad = OpenAIAdapter("k", "https://x/v1", t)
        return list(ad.stream(LLMRequest(model="m", messages=[]))), t

    def test_non_stream_transport_falls_back_to_complete(self):
        t = FakeTransport({"choices": [{"message": {"content": "whole"}}]})
        chunks = list(OpenAIAdapter("k", "https://x/v1", t).stream(
            LLMRequest(model="m", messages=[])))
        assert chunks[0].text_delta == "whole"
        assert chunks[-1].is_done

    def test_stream_payload_includes_usage_option(self):
        _, t = self._stream([_sse({"choices": [{"delta": {}, "finish_reason": "stop"}]})])
        assert t.stream_calls[0]["json"]["stream"] is True
        assert t.stream_calls[0]["json"]["stream_options"] == {"include_usage": True}

    def test_text_deltas(self):
        chunks, _ = self._stream([
            _sse({"choices": [{"delta": {"content": "he"}}]}),
            _sse({"choices": [{"delta": {"content": "llo"}}]}),
            _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        ])
        assert "".join(c.text_delta for c in chunks) == "hello"
        assert chunks[-1].is_done and chunks[-1].finish_reason == "stop"

    def test_malformed_json_line_skipped(self):
        chunks, _ = self._stream(["not json", _sse({"choices": [{"delta": {"content": "x"}}]})])
        assert "".join(c.text_delta for c in chunks) == "x"

    def test_reasoning_delta_separate_from_text(self):
        chunks, _ = self._stream([
            _sse({"choices": [{"delta": {"reasoning_content": "think"}}]}),
            _sse({"choices": [{"delta": {"content": "say"}}]}),
        ])
        assert [c.reasoning_delta for c in chunks if c.reasoning_delta] == ["think"]
        assert [c.text_delta for c in chunks if c.text_delta] == ["say"]

    def test_tool_call_accumulation(self):
        chunks, _ = self._stream([
            _sse({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c1", "function": {"name": "re", "arguments": '{"p"'}}]}}]}),
            _sse({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"name": "ad", "arguments": ':1}'}}]}}]}),
            _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        ])
        deltas = [c.tool_call_delta for c in chunks if c.tool_call_delta]
        assert deltas[-1]["id"] == "c1"
        assert deltas[-1]["name"] == "read"
        assert deltas[-1]["arguments"] == '{"p":1}'

    def test_usage_only_trailing_chunk(self):
        chunks, _ = self._stream([
            _sse({"choices": [{"delta": {"content": "a"}, "finish_reason": "stop"}]}),
            _sse({"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}}),
        ])
        done = [c for c in chunks if c.is_done]
        assert done[-1].usage.total_tokens == 3

    def test_finish_reason_with_inline_usage(self):
        chunks, _ = self._stream([_sse({
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})])
        assert chunks[-1].usage.total_tokens == 2

    def test_implicit_done_when_no_finish_reason(self):
        chunks, _ = self._stream([_sse({"choices": [{"delta": {"content": "a"}}]})])
        assert chunks[-1].is_done and chunks[-1].finish_reason == "stop"

    def test_error_object_in_stream(self):
        chunks, _ = self._stream([_sse({"choices": [], "error": {"code": "E1", "message": "blocked"}})])
        assert chunks[-1].finish_reason == "api_error"
        assert "blocked" in chunks[-1].error

    def test_error_as_string(self):
        chunks, _ = self._stream([_sse({"choices": [], "error": "plain failure"})])
        assert "plain failure" in chunks[-1].error

    def test_top_level_code_message_error(self):
        chunks, _ = self._stream([_sse({"choices": [], "code": "C", "message": "m"})])
        assert chunks[-1].finish_reason == "api_error"

    def test_empty_choices_no_error_no_usage_is_skipped(self):
        chunks, _ = self._stream([_sse({"choices": []}),
                                  _sse({"choices": [{"delta": {"content": "x"}}]})])
        assert "".join(c.text_delta for c in chunks) == "x"

    def test_transport_runtime_error_becomes_api_error_chunk(self):
        chunks, _ = self._stream([], raise_at=0, error=RuntimeError('HTTP 400 {"error": {"message": "bad key"}}'))
        assert chunks[-1].finish_reason == "api_error"
        assert "bad key" in chunks[-1].error

    def test_content_delta_dict_form(self):
        chunks, _ = self._stream([_sse({"choices": [{"delta": {"content": {"text": "d"}}}]})])
        assert chunks[0].text_delta == "d"

    def test_content_delta_list_form(self):
        chunks, _ = self._stream([_sse({"choices": [{"delta": {"content": [
            {"type": "text", "text": "a"}, {"type": "output_text", "content": "b"},
            {"type": "junk"}, "notadict"]}}]})])
        assert "".join(c.text_delta for c in chunks if c.text_delta) == "ab"

    def test_image_delta(self):
        chunks, _ = self._stream([_sse({"choices": [{"delta": {"content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,Z"}},
            {"type": "image_url", "image_url": {"url": "https://i/y.png"}},
            {"type": "image_url", "image_url": {"url": ""}},
        ]}}]})])
        imgs = [c.image for c in chunks if c.image]
        assert imgs[0].data == "Z" and imgs[1].source_type == "url"
        assert len(imgs) == 2


class TestOpenAIDeltaIterators:
    def test_content_delta_ignores_non_list(self):
        assert list(oa._iter_openai_content_delta({"content": 5})) == []

    def test_content_delta_empty_string(self):
        assert list(oa._iter_openai_content_delta({"content": ""})) == []

    def test_content_delta_empty_dict(self):
        assert list(oa._iter_openai_content_delta({"content": {}})) == []

    def test_reasoning_delta_variants(self):
        assert list(oa._iter_openai_reasoning_delta({"reasoning_content": "a"})) == ["a"]
        assert list(oa._iter_openai_reasoning_delta({"reasoning_content": ""})) == []
        assert list(oa._iter_openai_reasoning_delta({"reasoning_content": {"content": "b"}})) == ["b"]
        assert list(oa._iter_openai_reasoning_delta({"reasoning_content": {}})) == []
        assert list(oa._iter_openai_reasoning_delta({"reasoning_content": 7})) == []
        assert list(oa._iter_openai_reasoning_delta({"reasoning_content": [
            {"type": "reasoning_text", "text": "c"}, {"type": "x"}, "str"]})) == ["c"]

    def test_delta_images_ignores_non_list(self):
        assert list(oa._iter_openai_delta_images({"content": "s"})) == []


class TestOpenAIFormatApiError:
    def test_extracts_message_and_request_id(self):
        exc = RuntimeError('HTTP 401 {"error": {"message": "no auth", "request_id": "r1"}}')
        out = oa._format_api_error(exc, "m", "u")
        assert "request_id='r1'" in out and "model='m'" in out

    def test_error_as_string_body(self):
        # message is literally present in raw, so it is not repeated
        out = oa._format_api_error(RuntimeError('x {"error": "Not Found"}'), "m", "u")
        assert "api_message" not in out

    def test_top_level_message_supplies_request_id(self):
        out = oa._format_api_error(RuntimeError('x {"message": "mm", "request_id": "r"}'), "m", "u")
        assert "request_id='r'" in out

    def test_escaped_message_is_appended(self):
        # the body carries a \u escape, so the decoded message is absent from raw
        # and does get appended as api_message=
        out = oa._format_api_error(RuntimeError('x {"error": {"message": "\\u4f60\\u597d"}}'), "m", "u")
        assert "api_message='你好'" in out

    def test_unparseable_body(self):
        out = oa._format_api_error(RuntimeError("plain failure"), "m", "u")
        assert out.startswith("plain failure") and "model='m'" in out

    def test_non_dict_json_body(self):
        out = oa._format_api_error(RuntimeError('x {"a": 1}'), "m", "u")
        assert "api_message" not in out

    def test_message_already_in_raw_not_duplicated(self):
        out = oa._format_api_error(RuntimeError('{"error": {"message": "dup"}}'), "m", "u")
        assert out.count("dup") == 1


# ── Anthropic ─────────────────────────────────────────────────────────────────

class TestAnthropicPayload:
    def _adapter(self, resp=None):
        t = FakeTransport(resp or {"content": [{"type": "text", "text": "hi"}]})
        return AnthropicAdapter("sk-ant", "https://api.anthropic.com/", t), t

    def test_headers(self):
        ad, t = self._adapter()
        ad.complete(LLMRequest(model="m", messages=[]))
        assert t.last["headers"]["x-api-key"] == "sk-ant"
        assert t.last["headers"]["anthropic-version"] == "2023-06-01"

    def test_url_and_trailing_slash(self):
        ad, t = self._adapter()
        ad.complete(LLMRequest(model="m", messages=[]))
        assert t.last["url"] == "https://api.anthropic.com/v1/messages"

    def test_max_tokens_defaults_to_4096(self):
        ad, t = self._adapter()
        ad.complete(LLMRequest(model="m", messages=[]))
        assert t.last["json"]["max_tokens"] == 4096

    def test_all_params(self):
        ad, t = self._adapter()
        ad.complete(LLMRequest(model="m", messages=[], temperature=0.1, top_p=0.4,
                               max_tokens=10, stop=["S"], tools=[_tool()]))
        p = t.last["json"]
        assert p["temperature"] == 0.1 and p["top_p"] == 0.4
        assert p["max_tokens"] == 10 and p["stop_sequences"] == ["S"]
        assert p["tools"][0]["type"] == "custom"
        assert p["tools"][0]["description"] == "Read a file"

    def test_tool_without_description(self):
        mapped = aa._map_anthropic_tools([LLMTool(name="n")])
        assert "description" not in mapped[0]

    def test_non_function_tool_rejected(self):
        with pytest.raises(ValueError):
            aa._map_anthropic_tools([LLMTool(name="n", type="custom")])

    def test_system_messages_hoisted_and_prompt_prepended(self):
        ad, t = self._adapter()
        ad.complete(LLMRequest(model="m", system_prompt="SYS", messages=[
            LLMMessage(role="system", content="s1"),
            LLMMessage(role="system", content="s2"),
            LLMMessage(role="user", content="u"),
        ]))
        p = t.last["json"]
        assert p["system"] == "SYS\ns1\ns2"
        assert [m["role"] for m in p["messages"]] == ["user"]

    def test_no_system_key_when_empty(self):
        ad, t = self._adapter()
        ad.complete(LLMRequest(model="m", messages=[LLMMessage(role="user", content="u")]))
        assert "system" not in t.last["json"]


class TestAnthropicSerializeMessages:
    def test_assistant_text_only(self):
        out = aa._serialize_messages_anthropic([LLMMessage(role="assistant", content="a")])
        assert out[0]["content"] == [{"type": "text", "text": "a"}]

    def test_assistant_empty_content_falls_back_to_text(self):
        out = aa._serialize_messages_anthropic([LLMMessage(role="assistant", content="")])
        assert out[0]["content"] == ""

    def test_assistant_with_tool_use(self):
        out = aa._serialize_messages_anthropic([LLMMessage(
            role="assistant", content="doing",
            tool_calls=[{"id": "t1", "name": "read", "input": {"p": 1}}])])
        blocks = out[0]["content"]
        assert blocks[0]["type"] == "text"
        assert blocks[1] == {"type": "tool_use", "id": "t1", "name": "read", "input": {"p": 1}}

    def test_consecutive_tool_messages_merged(self):
        out = aa._serialize_messages_anthropic([
            LLMMessage(role="tool", content="r1", tool_call_id="t1"),
            LLMMessage(role="tool", content="r2", tool_call_id="t2"),
            LLMMessage(role="user", content="next"),
        ])
        assert len(out) == 2
        assert [b["tool_use_id"] for b in out[0]["content"]] == ["t1", "t2"]
        assert out[1]["role"] == "user"

    def test_tool_without_id_becomes_user_text(self):
        out = aa._serialize_messages_anthropic([
            LLMMessage(role="tool", content="a"), LLMMessage(role="tool", content="b")])
        assert out == [{"role": "user", "content": "a\n\nb"}]

    def test_mixed_tool_messages_produce_both_entries(self):
        out = aa._serialize_messages_anthropic([
            LLMMessage(role="tool", content="withid", tool_call_id="t1"),
            LLMMessage(role="tool", content="noid"),
        ])
        assert len(out) == 2
        assert out[0]["content"][0]["type"] == "tool_result"
        assert out[1]["content"] == "noid"

    def test_multimodal_blocks(self):
        out = aa._serialize_messages_anthropic([LLMMessage(role="user", content=[
            TextPart(text="t"), TextPart(text=""),
            ImagePart(data="B", media_type="image/png"),
            ImagePart(data="https://i/x", source_type="url"),
            DocumentPart(data="P", media_type="application/pdf"),
        ])])
        blocks = out[0]["content"]
        assert blocks[0] == {"type": "text", "text": "t"}
        assert blocks[1]["source"] == {"type": "base64", "media_type": "image/png", "data": "B"}
        assert blocks[2]["source"] == {"type": "url", "url": "https://i/x"}
        assert blocks[3]["type"] == "document"

    def test_empty_parts_become_empty_string(self):
        out = aa._serialize_messages_anthropic([LLMMessage(role="user", content=[])])
        assert out[0]["content"] == ""


class TestAnthropicParseResponse:
    def _parse(self, raw, usage=None):
        ad = AnthropicAdapter("k", "https://x", FakeTransport())
        return ad.parse_response(LLMResponse(text="", raw=raw, usage=usage))

    def test_text_blocks_joined(self):
        p = self._parse({"content": [{"type": "text", "text": "a"},
                                     {"type": "text", "text": "b"},
                                     {"type": "text", "text": ""}]})
        assert p.text == "a\nb"
        assert len(p.blocks) == 2

    def test_tool_use(self):
        p = self._parse({"content": [{"type": "tool_use", "id": "t1", "name": "read",
                                      "input": {"p": "f"}}]})
        assert p.tool_calls[0].id == "t1"
        assert p.tool_calls[0].tool_type == "tool_use"

    def test_tool_use_defaults(self):
        p = self._parse({"content": [{"type": "tool_use"}]})
        assert p.tool_calls[0].id == "" and p.tool_calls[0].input == {}

    def test_image_base64_and_url(self):
        p = self._parse({"content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "D"}},
            {"type": "image", "source": {"type": "url", "url": "https://i/x"}},
            {"type": "image"},
        ]})
        assert p.images[0].data == "D"
        assert p.images[1].data == "https://i/x"
        assert p.images[2].data == ""

    def test_document_block(self):
        p = self._parse({"content": [
            {"type": "document", "source": {"media_type": "application/pdf", "data": "P"}}]})
        assert p.blocks[0].data == "P"

    def test_unknown_block_ignored(self):
        p = self._parse({"content": [{"type": "thinking"}]})
        assert p.blocks == []

    def test_empty_raw(self):
        ad = AnthropicAdapter("k", "https://x", FakeTransport())
        assert ad.parse_response(LLMResponse(text="")).text == ""


class TestAnthropicComplete:
    def test_text_and_usage(self):
        t = FakeTransport({"content": [{"type": "text", "text": "hey"}],
                           "usage": {"input_tokens": 4, "output_tokens": 6}})
        r = AnthropicAdapter("k", "https://x", t).complete(LLMRequest(model="m", messages=[]))
        assert r.text == "hey"
        assert r.usage.total_tokens == 10

    def test_no_content(self):
        t = FakeTransport({})
        r = AnthropicAdapter("k", "https://x", t).complete(LLMRequest(model="m", messages=[]))
        assert r.text == "" and r.usage is None

    def test_zero_tokens_total_is_none(self):
        assert aa._extract_anthropic_usage(
            {"usage": {"input_tokens": 0, "output_tokens": 0}}).total_tokens is None


class TestAnthropicStream:
    def _stream(self, lines, **kw):
        t = FakeStreamTransport(lines, **kw)
        ad = AnthropicAdapter("k", "https://x", t)
        return list(ad.stream(LLMRequest(model="m", messages=[]))), t

    def test_non_stream_transport_fallback(self):
        t = FakeTransport({"content": [{"type": "text", "text": "w"}]})
        chunks = list(AnthropicAdapter("k", "https://x", t).stream(LLMRequest(model="m", messages=[])))
        assert chunks[0].text_delta == "w" and chunks[-1].is_done

    def test_text_deltas_and_usage(self):
        chunks, _ = self._stream([
            _sse({"type": "message_start", "message": {"usage": {"input_tokens": 5}}}),
            _sse({"type": "content_block_start", "index": 0, "content_block": {"type": "text"}}),
            _sse({"type": "content_block_delta", "index": 0,
                  "delta": {"type": "text_delta", "text": "hel"}}),
            _sse({"type": "content_block_delta", "index": 0,
                  "delta": {"type": "text_delta", "text": "lo"}}),
            _sse({"type": "content_block_delta", "index": 0,
                  "delta": {"type": "text_delta", "text": ""}}),
            _sse({"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                  "usage": {"output_tokens": 3}}),
        ])
        assert "".join(c.text_delta for c in chunks) == "hello"
        assert chunks[-1].is_done and chunks[-1].finish_reason == "end_turn"
        assert chunks[-1].usage.prompt_tokens == 5
        assert chunks[-1].usage.total_tokens == 8

    def test_malformed_line_skipped(self):
        chunks, _ = self._stream(["{bad", _sse({"type": "message_delta", "delta": {}})])
        assert chunks[-1].is_done

    def test_tool_use_input_json_accumulated(self):
        chunks, _ = self._stream([
            _sse({"type": "content_block_start", "index": 0,
                  "content_block": {"type": "tool_use", "id": "t1", "name": "read"}}),
            _sse({"type": "content_block_delta", "index": 0,
                  "delta": {"type": "input_json_delta", "partial_json": '{"p"'}}),
            _sse({"type": "content_block_delta", "index": 0,
                  "delta": {"type": "input_json_delta", "partial_json": ':1}'}}),
            _sse({"type": "message_delta", "delta": {"stop_reason": "tool_use"}}),
        ])
        deltas = [c.tool_call_delta for c in chunks if c.tool_call_delta]
        assert deltas[-1]["id"] == "t1" and deltas[-1]["arguments"] == '{"p":1}'

    def test_input_json_delta_for_unknown_index_ignored(self):
        chunks, _ = self._stream([
            _sse({"type": "content_block_delta", "index": 9,
                  "delta": {"type": "input_json_delta", "partial_json": "x"}}),
            _sse({"type": "message_delta", "delta": {}}),
        ])
        assert not any(c.tool_call_delta for c in chunks)

    def test_image_block_start(self):
        chunks, _ = self._stream([
            _sse({"type": "content_block_start", "index": 0, "content_block": {
                "type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "D"}}}),
            _sse({"type": "message_delta", "delta": {}}),
        ])
        assert chunks[0].image.data == "D"

    def test_stop_reason_at_top_level(self):
        chunks, _ = self._stream([_sse({"type": "message_delta", "stop_reason": "max_tokens"})])
        assert chunks[-1].finish_reason == "max_tokens"

    def test_no_usage_gives_none(self):
        chunks, _ = self._stream([_sse({"type": "message_delta", "delta": {}})])
        assert chunks[-1].usage is None

    def test_unknown_delta_type_ignored(self):
        chunks, _ = self._stream([
            _sse({"type": "content_block_delta", "delta": {"type": "signature_delta"}}),
            _sse({"type": "message_delta", "delta": {}}),
        ])
        assert len(chunks) == 1

    def test_runtime_error_becomes_api_error(self):
        chunks, _ = self._stream(
            [], raise_at=0,
            error=RuntimeError('HTTP 400 {"error": {"message": "bad", "type": "invalid_request"}}'))
        assert chunks[-1].finish_reason == "api_error"
        assert "bad" in chunks[-1].error and "invalid_request" in chunks[-1].error


class TestAnthropicFormatApiError:
    def test_string_error_not_duplicated(self):
        out = aa._format_api_error(RuntimeError('x {"error": "nf"}'), "m", "u")
        assert "api_message" not in out and out.startswith("x ")

    def test_escaped_message_is_appended(self):
        out = aa._format_api_error(
            RuntimeError('x {"error": {"message": "\\u4f60", "type": "bad_req"}}'), "m", "u")
        assert "api_message='你'" in out and "error_type='bad_req'" in out

    def test_top_level_message_path(self):
        out = aa._format_api_error(RuntimeError('x {"message": "\\u597d"}'), "m", "u")
        assert "api_message='好'" in out

    def test_unparseable(self):
        out = aa._format_api_error(RuntimeError("raw only"), "m", "u")
        assert out.startswith("raw only")

    def test_non_dict_body(self):
        assert "api_message" not in aa._format_api_error(RuntimeError('x [1,2]'), "m", "u")


# ── BaseChatClient / BaseAdapter ──────────────────────────────────────────────

class TestBaseAdapterContract:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            BaseAdapter()

    def test_transport_protocols_runtime_checkable(self):
        assert isinstance(FakeTransport(), Transport)
        assert isinstance(FakeStreamTransport(), StreamTransport)
        assert not isinstance(FakeTransport(), StreamTransport)


class TestBaseChatClient:
    def _client(self, text="mock"):
        return BaseChatClient(MockAdapter(text), "m", context_limit=1000, max_output_tokens=50)

    def test_limits_exposed(self):
        c = self._client()
        assert c.context_limit == 1000 and c.max_output_tokens == 50

    def test_defaults(self):
        c = BaseChatClient(MockAdapter(), "m")
        assert c.context_limit == 200_000 and c.max_output_tokens == 8192

    def test_send_message_returns_response(self):
        r = self._client("answer").send_message([LLMMessage(role="user", content="q")])
        assert r.text == "answer"
        assert r.usage.total_tokens == 15

    def test_send_message_forwards_all_kwargs(self):
        captured = {}

        class Spy(BaseAdapter):
            def complete(self, req):
                captured["req"] = req
                return LLMResponse(text="")

            def parse_response(self, response):
                return None

        BaseChatClient(Spy(), "the-model").send_message(
            [LLMMessage(role="user", content="q")], system_prompt="S", tools=[_tool()],
            temperature=0.2, max_tokens=5, top_p=0.3, stop=["X"], metadata={"k": "v"})
        req = captured["req"]
        assert req.model == "the-model" and req.system_prompt == "S"
        assert req.temperature == 0.2 and req.max_tokens == 5
        assert req.top_p == 0.3 and req.stop == ["X"] and req.metadata == {"k": "v"}

    def test_metadata_defaults_to_empty_dict(self):
        captured = {}

        class Spy(BaseAdapter):
            def complete(self, req):
                captured["req"] = req
                return LLMResponse(text="")

            def parse_response(self, response):
                return None

        BaseChatClient(Spy(), "m").send_message([])
        assert captured["req"].metadata == {}

    def test_stream_message(self):
        chunks = list(self._client("a b c").stream_message([LLMMessage(role="user", content="q")]))
        assert "".join(c.text_delta for c in chunks) == "a b c"
        assert chunks[-1].is_done and chunks[-1].usage.completion_tokens == 3

    def test_parse_response_delegates(self):
        c = self._client("x")
        p = c.parse_response(LLMResponse(text="x"))
        assert p.text == "x" and isinstance(p.blocks[0], TextBlock)


class TestParseStreamAcc:
    def _c(self):
        return BaseChatClient(MockAdapter(), "m")

    def test_text_only(self):
        p = self._c().parse_stream_acc("hello", {})
        assert p.text == "hello" and isinstance(p.blocks[0], TextBlock)

    def test_no_text_no_blocks(self):
        assert self._c().parse_stream_acc("", {}).blocks == []

    def test_tool_calls_sorted_and_parsed(self):
        p = self._c().parse_stream_acc("", {
            1: {"id": "b", "name": "second", "arguments": '{"y": 2}'},
            0: {"id": "a", "name": "first", "arguments": '{"x": 1}'},
        })
        assert [tc.name for tc in p.tool_calls] == ["first", "second"]
        assert p.tool_calls[0].input == {"x": 1}

    def test_bad_json_arguments_kept_raw(self):
        p = self._c().parse_stream_acc("", {0: {"id": "a", "name": "n", "arguments": "{oops"}})
        assert p.tool_calls[0].input == {"_raw": "{oops"}

    def test_empty_arguments(self):
        p = self._c().parse_stream_acc("", {0: {"id": "a", "name": "n", "arguments": ""}})
        assert p.tool_calls[0].input == {}

    def test_missing_keys_default(self):
        p = self._c().parse_stream_acc("", {0: {}})
        assert p.tool_calls[0].id == "" and p.tool_calls[0].name == ""

    def test_images_and_usage_included(self):
        img = ImageBlock(type="image", media_type="image/png", source_type="base64", data="D")
        usage = LLMUsage(total_tokens=9)
        p = self._c().parse_stream_acc("t", {}, images=[img], usage=usage)
        assert img in p.blocks and p.images == [img] and p.usage is usage


class TestMockAdapter:
    def test_complete(self):
        r = MockAdapter("m1").complete(LLMRequest(model="x", messages=[]))
        assert r.text == "m1" and r.usage.prompt_tokens == 10

    def test_parse_response(self):
        p = MockAdapter().parse_response(LLMResponse(text="t", raw={"r": 1}))
        assert p.text == "t" and p.raw == {"r": 1} and p.tool_calls == []

    def test_stream_word_by_word(self):
        chunks = list(MockAdapter("one two").stream(LLMRequest(model="x", messages=[])))
        assert [c.text_delta for c in chunks[:-1]] == ["one", " two"]
        assert chunks[-1].is_done

    def test_default_stream_wraps_complete(self):
        class Simple(BaseAdapter):
            def complete(self, req):
                return LLMResponse(text="whole", usage=LLMUsage(total_tokens=1))

            def parse_response(self, response):
                return None

        chunks = list(Simple().stream(LLMRequest(model="m", messages=[])))
        assert chunks[0].text_delta == "whole"
        assert chunks[1].is_done and chunks[1].usage.total_tokens == 1


class TestToolCallBlockShape:
    def test_defaults(self):
        tc = ToolCallBlock(type="tool_call", id="i", name="n", input={})
        assert tc.tool_type == "function" and tc.raw == {}
