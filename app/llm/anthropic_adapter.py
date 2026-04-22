"""Anthropic 适配器实现（非流式 + SSE 流式）。"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterator, Optional

from app.llm.base import BaseAdapter, StreamTransport, Transport
from app.llm.types import (
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMTool,
    LLMUsage,
    ParsedResponse,
    StreamChunk,
    TextBlock,
    ToolCallBlock
)


class AnthropicAdapter(BaseAdapter):
    """Anthropic LLM 适配器。"""

    def __init__(self, api_key: str, base_url: str, transport: Transport, timeout_sec: int = 60) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip('/')
        self._transport = transport
        self._timeout_sec = timeout_sec

    # ── 非流式 ────────────────────────────────────────────────────────────

    def complete(self, req: LLMRequest) -> LLMResponse:
        """统一补全接口（Anthropic Messages 风格）。"""
        payload = self._build_payload(req, stream=False)
        url = f"{self._base_url}/v1/messages"
        resp = self._transport.post(url, headers=self._headers(), json=payload, timeout=self._timeout_sec)
        return LLMResponse(
            text=_extract_anthropic_text(resp),
            raw=resp,
            usage=_extract_anthropic_usage(resp),
        )

    def parse_response(self, response: LLMResponse) -> ParsedResponse:
        """解析 Anthropic 响应为统一内容块。"""
        raw = response.raw or {}
        blocks: list = []
        tool_calls: list[ToolCallBlock] = []
        text_parts: list[str] = []

        for block in raw.get('content') or []:
            block_type = block.get('type')
            if block_type == 'text':
                text = block.get('text') or ''
                if text:
                    text_parts.append(text)
                    blocks.append(TextBlock(type='text', text=text))
            elif block_type == 'tool_use':
                tool_block = ToolCallBlock(
                    type='tool_call',
                    id=block.get('id') or '',
                    name=block.get('name') or '',
                    input=block.get('input') or {},
                    tool_type=block_type,
                    raw=block,
                )
                blocks.append(tool_block)
                tool_calls.append(tool_block)

        text = '\n'.join(text_parts).strip()
        return ParsedResponse(text=text, blocks=blocks, tool_calls=tool_calls, raw=raw, usage=response.usage)

    # ── 流式 ──────────────────────────────────────────────────────────────

    def stream(self, req: LLMRequest) -> Iterator[StreamChunk]:
        """SSE 流式补全，逐 token 产出 StreamChunk。

        Anthropic SSE 事件类型参考：
          content_block_start / content_block_delta / content_block_stop
          message_delta（含 stop_reason + usage）/ message_stop
        """
        if not isinstance(self._transport, StreamTransport):
            yield from super().stream(req)
            return

        payload = self._build_payload(req, stream=True)
        url = f"{self._base_url}/v1/messages"
        headers = self._headers()

        # tool_use 块按 index 聚合输入 JSON
        tool_blocks: dict[int, dict[str, Any]] = {}
        # 当前处于哪个 content block
        current_block_type: str = ''
        current_block_index: int = -1

        for raw_line in self._transport.stream_post(url, headers, payload, self._timeout_sec):
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            event_type = event.get('type', '')

            if event_type == 'content_block_start':
                block = event.get('content_block') or {}
                current_block_index = event.get('index', 0)
                current_block_type = block.get('type', '')
                if current_block_type == 'tool_use':
                    tool_blocks[current_block_index] = {
                        'id': block.get('id', ''),
                        'name': block.get('name', ''),
                        'arguments': '',
                    }

            elif event_type == 'content_block_delta':
                delta = event.get('delta') or {}
                delta_type = delta.get('type', '')

                if delta_type == 'text_delta':
                    text = delta.get('text') or ''
                    if text:
                        yield StreamChunk(text_delta=text)

                elif delta_type == 'input_json_delta':
                    partial = delta.get('partial_json') or ''
                    idx = event.get('index', current_block_index)
                    if idx in tool_blocks:
                        tool_blocks[idx]['arguments'] += partial
                        yield StreamChunk(tool_call_delta={
                            'index': idx,
                            **tool_blocks[idx],
                        })

            elif event_type == 'message_delta':
                # 包含 stop_reason 和最终 usage
                usage_data = event.get('usage') or {}
                usage = LLMUsage(
                    completion_tokens=usage_data.get('output_tokens'),
                ) if usage_data else None
                yield StreamChunk(is_done=True, usage=usage)
                return

    # ── 内部工具 ──────────────────────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        return {
            'x-api-key': self._api_key,
            'anthropic-version': '2023-06-01',
            'Content-Type': 'application/json',
        }

    def _build_payload(self, req: LLMRequest, stream: bool) -> Dict[str, Any]:
        system_text, messages = _split_system_messages(req)
        if req.system_prompt:
            system_text = f"{req.system_prompt}\n{system_text}".strip()

        payload: Dict[str, Any] = {
            'model': req.model,
            'messages': _serialize_messages_anthropic(messages),
            'stream': stream,
        }
        if system_text:
            payload['system'] = system_text
        if req.max_tokens is None:
            raise ValueError('Anthropic 请求必须提供 max_tokens')
        payload['max_tokens'] = req.max_tokens
        if req.temperature is not None:
            payload['temperature'] = req.temperature
        if req.top_p is not None:
            payload['top_p'] = req.top_p
        if req.stop is not None:
            payload['stop_sequences'] = req.stop
        all_tools = []
        if req.tools:
            all_tools.extend(_map_anthropic_tools(req.tools))
        if all_tools:
            payload['tools'] = all_tools
        return payload


# ── 私有解析函数 ──────────────────────────────────────────────────────────

def _extract_anthropic_text(resp: Dict[str, Any]) -> str:
    content = resp.get('content') or []
    if not content:
        return ''
    return content[0].get('text') or ''


def _extract_anthropic_usage(resp: Dict[str, Any]) -> Optional[LLMUsage]:
    usage = resp.get('usage') or {}
    if not usage:
        return None
    input_tokens = usage.get('input_tokens')
    output_tokens = usage.get('output_tokens')
    total = (input_tokens or 0) + (output_tokens or 0) or None
    return LLMUsage(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=total,
    )


def _serialize_messages_anthropic(messages: list[LLMMessage]) -> list[dict]:
    """将内部 LLMMessage 列表序列化为 Anthropic Messages API 格式。

    - role="assistant" + tool_calls → content 块列表（text + tool_use）
    - role="tool" → 合并连续 tool 消息为单条 role="user" 的 tool_result 块列表
    - role="tool" 且 tool_call_id 为空 → 降级为普通 user 文本（历史记忆回放场景）
    - 其他 role 原样传递（content 保持字符串）
    """
    result: list[dict] = []
    i = 0
    while i < len(messages):
        m = messages[i]

        if m.role == "assistant":
            content_blocks: list[dict] = []
            if m.content:
                content_blocks.append({"type": "text", "text": m.content})
            for tc in (m.tool_calls or []):
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["input"],
                })
            result.append({"role": "assistant", "content": content_blocks or m.content})
            i += 1

        elif m.role == "tool":
            tool_result_blocks: list[dict] = []
            fallback_texts: list[str] = []
            while i < len(messages) and messages[i].role == "tool":
                tm = messages[i]
                if tm.tool_call_id:
                    tool_result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tm.tool_call_id,
                        "content": tm.content,
                    })
                else:
                    fallback_texts.append(tm.content)
                i += 1
            if tool_result_blocks:
                result.append({"role": "user", "content": tool_result_blocks})
            if fallback_texts:
                result.append({"role": "user", "content": "\n\n".join(fallback_texts)})

        else:
            result.append({"role": m.role, "content": m.content})
            i += 1

    return result


def _split_system_messages(req: LLMRequest) -> tuple[str, list]:
    system_parts = []
    non_system = []
    for m in req.messages:
        if m.role == 'system':
            system_parts.append(m.content)
        else:
            non_system.append(m)
    return '\n'.join(system_parts).strip(), non_system


def _map_anthropic_tools(tools: list[LLMTool]) -> list[dict]:
    mapped: list[dict] = []
    for tool in tools:
        if tool.type != 'function':
            raise ValueError(f'_map_anthropic_tools 仅处理 function 工具，当前: {tool.type}')
        item: dict = {
            'type': 'custom',
            'name': tool.name,
            'input_schema': tool.input_schema.to_dict(),
        }
        if tool.description:
            item['description'] = tool.description
        mapped.append(item)
    return mapped
