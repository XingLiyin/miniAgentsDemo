"""OpenAI 适配器实现（非流式 + SSE 流式）。"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterator, Optional

from app.llm.base import (
    BaseAdapter,
    StreamTransport,
    Transport
)
from app.llm.types import LLMMessage, LLMRequest, LLMResponse, LLMTool, LLMUsage, ParsedResponse, StreamChunk, TextBlock, ToolCallBlock


class OpenAIAdapter(BaseAdapter):
    """OpenAI LLM 适配器。"""

    def __init__(self, api_key: str, base_url: str, transport: Transport, timeout_sec: int = 60) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip('/')
        self._transport = transport
        self._timeout_sec = timeout_sec

    # ── 非流式 ────────────────────────────────────────────────────────────

    def complete(self, req: LLMRequest) -> LLMResponse:
        """统一补全接口（OpenAI Chat Completions 风格）。"""
        payload = self._build_payload(req, stream=False)
        url = f"{self._base_url}/v1/chat/completions"
        resp = self._transport.post(url, headers=self._headers(), json=payload, timeout=self._timeout_sec)
        return LLMResponse(
            text=_extract_openai_text(resp),
            raw=resp,
            usage=_extract_openai_usage(resp),
        )

    def parse_response(self, response: LLMResponse) -> ParsedResponse:
        """解析 OpenAI 响应为统一内容块。"""
        raw = response.raw or {}
        message = _extract_openai_message(raw)
        blocks: list = []
        tool_calls: list[ToolCallBlock] = []

        for text in _extract_openai_text_parts(message):
            blocks.append(TextBlock(type='text', text=text))

        for call in _extract_openai_tool_calls(message):
            blocks.append(call)
            tool_calls.append(call)

        text = '\n'.join(b.text for b in blocks if isinstance(b, TextBlock)).strip()
        return ParsedResponse(text=text, blocks=blocks, tool_calls=tool_calls, raw=raw, usage=response.usage)

    # ── 流式 ──────────────────────────────────────────────────────────────

    def stream(self, req: LLMRequest) -> Iterator[StreamChunk]:
        """SSE 流式补全，逐 token 产出 StreamChunk。

        要求 transport 实现 StreamTransport 协议（如 HttpxTransport）。
        """
        if not isinstance(self._transport, StreamTransport):
            yield from super().stream(req)
            return

        payload = self._build_payload(req, stream=True)
        url = f"{self._base_url}/v1/chat/completions"

        # 聚合 tool_call 增量（按 index）
        tool_call_buffers: dict[int, dict[str, Any]] = {}

        for raw_line in self._transport.stream_post(url, self._headers(), payload, self._timeout_sec):
            try:
                data = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            choices = data.get('choices') or []
            if not choices:
                # 最后一个 chunk 可能只含 usage
                usage_data = data.get('usage')
                if usage_data:
                    yield StreamChunk(is_done=True, usage=_parse_usage(usage_data))
                continue

            choice = choices[0]
            delta = choice.get('delta') or {}
            finish_reason = choice.get('finish_reason')

            # 文本增量
            text_delta = delta.get('content') or ''
            if text_delta:
                yield StreamChunk(text_delta=text_delta)

            # tool_call 增量
            for tc_delta in delta.get('tool_calls') or []:
                idx = tc_delta.get('index', 0)
                buf = tool_call_buffers.setdefault(idx, {'id': '', 'name': '', 'arguments': ''})
                if tc_delta.get('id'):
                    buf['id'] = tc_delta['id']
                fn = tc_delta.get('function') or {}
                if fn.get('name'):
                    buf['name'] += fn['name']
                if fn.get('arguments'):
                    buf['arguments'] += fn['arguments']
                yield StreamChunk(tool_call_delta={'index': idx, **buf})

            if finish_reason:
                usage_data = data.get('usage')
                yield StreamChunk(
                    is_done=True,
                    usage=_parse_usage(usage_data) if usage_data else None,
                )
                return

    # ── 内部工具 ──────────────────────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        return {
            'Authorization': f"Bearer {self._api_key}",
            'Content-Type': 'application/json',
        }

    def _build_payload(self, req: LLMRequest, stream: bool) -> Dict[str, Any]:
        messages = _merge_system_prompt(req)
        payload: Dict[str, Any] = {
            'model': req.model,
            'messages': [{'role': m.role, 'content': m.content} for m in messages],
            'stream': stream,
        }
        if stream:
            # 请求服务端在结束 chunk 中携带 usage
            payload['stream_options'] = {'include_usage': True}
        if req.tools is not None:
            payload['tools'] = _map_openai_tools(req.tools)
        if req.temperature is not None:
            payload['temperature'] = req.temperature
        if req.max_tokens is not None:
            payload['max_tokens'] = req.max_tokens
        if req.top_p is not None:
            payload['top_p'] = req.top_p
        if req.stop is not None:
            payload['stop'] = req.stop
        return payload


# ── 私有解析函数 ──────────────────────────────────────────────────────────

def _parse_usage(usage: Dict[str, Any]) -> LLMUsage:
    return LLMUsage(
        prompt_tokens=usage.get('prompt_tokens'),
        completion_tokens=usage.get('completion_tokens'),
        total_tokens=usage.get('total_tokens'),
    )


def _extract_openai_text(resp: Dict[str, Any]) -> str:
    choices = resp.get('choices') or []
    if not choices:
        return ''
    message = choices[0].get('message') or {}
    return message.get('content') or ''


def _extract_openai_usage(resp: Dict[str, Any]) -> Optional[LLMUsage]:
    usage = resp.get('usage') or {}
    return _parse_usage(usage) if usage else None


def _extract_openai_message(resp: Dict[str, Any]) -> Dict[str, Any]:
    choices = resp.get('choices') or []
    if not choices:
        return {}
    return choices[0].get('message') or {}


def _extract_openai_text_parts(message: Dict[str, Any]) -> list[str]:
    content = message.get('content')
    if isinstance(content, str):
        return [content] if content else []
    if isinstance(content, list):
        return [item.get('text') or '' for item in content if item.get('type') == 'text' and item.get('text')]
    return []


def _extract_openai_tool_calls(message: Dict[str, Any]) -> list[ToolCallBlock]:
    blocks: list[ToolCallBlock] = []
    for call in message.get('tool_calls') or []:
        fn = call.get('function') or {}
        args_raw = fn.get('arguments') or ''
        try:
            args = json.loads(args_raw) if args_raw else {}
        except json.JSONDecodeError:
            args = {'_raw_arguments': args_raw}
        blocks.append(ToolCallBlock(
            type='tool_call',
            id=call.get('id') or '',
            name=fn.get('name') or '',
            input=args,
            tool_type=call.get('type') or 'function',
            raw=call,
        ))
    return blocks


def _merge_system_prompt(req: LLMRequest) -> list[LLMMessage]:
    messages = list(req.messages)
    if not req.system_prompt:
        return messages
    for i, m in enumerate(messages):
        if m.role == 'system':
            messages[i] = LLMMessage(role='system', content=f"{req.system_prompt}\n{m.content}".strip())
            return messages
    return [LLMMessage(role='system', content=req.system_prompt)] + messages


def _map_openai_tools(tools: list[LLMTool]) -> list[dict]:
    mapped: list[dict] = []
    for tool in tools:
        if tool.type != 'function':
            raise ValueError(f'OpenAI 仅支持 function 工具，当前: {tool.type}')
        fn: dict = {'name': tool.name, 'parameters': tool.input_schema.to_dict()}
        if tool.description:
            fn['description'] = tool.description
        mapped.append({'type': 'function', 'function': fn})
    return mapped
