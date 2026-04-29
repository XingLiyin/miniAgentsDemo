"""OpenAI 适配器实现（非流式 + SSE 流式）。"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterator, Optional

from app.llm.base import (
    BaseAdapter,
    StreamTransport,
    Transport
)
from app.llm.types import (
    DocumentPart,
    ImageBlock,
    ImagePart,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMTool,
    LLMUsage,
    MessageContent,
    ParsedResponse,
    StreamChunk,
    TextBlock,
    TextPart,
    ToolCallBlock,
    content_to_text,
)


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
        images: list[ImageBlock] = []

        for text in _extract_openai_text_parts(message):
            blocks.append(TextBlock(type='text', text=text))

        for img in _extract_openai_image_parts(message):
            blocks.append(img)
            images.append(img)

        for call in _extract_openai_tool_calls(message):
            blocks.append(call)
            tool_calls.append(call)

        text = '\n'.join(b.text for b in blocks if isinstance(b, TextBlock)).strip()
        return ParsedResponse(text=text, blocks=blocks, tool_calls=tool_calls, images=images, raw=raw, usage=response.usage)

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

            # 正文 / 推理 / 图片增量分开处理，避免 reasoning_content 混入最终回复文本。
            for text_delta in _iter_openai_content_delta(delta):
                yield StreamChunk(text_delta=text_delta)

            for reasoning_delta in _iter_openai_reasoning_delta(delta):
                yield StreamChunk(reasoning_delta=reasoning_delta)

            for image in _iter_openai_delta_images(delta):
                yield StreamChunk(image=image)

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
                    finish_reason=finish_reason,
                    usage=_parse_usage(usage_data) if usage_data else None,
                )
                # don't return: let the loop continue so the trailing usage-only
                # chunk (choices=[]) can be captured when stream_options is used

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
            'messages': _serialize_messages_openai(messages),
            'stream': stream,
        }
        if stream:
            payload['stream_options'] = {'include_usage': True}
        if req.tools:
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


def _iter_openai_content_delta(delta: Dict[str, Any]) -> Iterator[str]:
    """从流式 delta.content 中提取正文增量。

    兼容形态：
    - {"content": "..."}
    - {"content": [{"type":"text","text":"..."}]}
    """
    value = delta.get('content')
    if isinstance(value, str):
        if value:
            yield value
        return

    if isinstance(value, dict):
        text = value.get('text') or value.get('content') or ''
        if text:
            yield text
        return

    if not isinstance(value, list):
        return

    for item in value:
        if not isinstance(item, dict):
            continue
        item_type = item.get('type')
        if item_type in ('text', 'output_text'):
            text = item.get('text') or item.get('content') or ''
            if text:
                yield text


def _iter_openai_reasoning_delta(delta: Dict[str, Any]) -> Iterator[str]:
    """从流式 delta.reasoning_content 中提取推理增量。"""
    value = delta.get('reasoning_content')
    if isinstance(value, str):
        if value:
            yield value
        return

    if isinstance(value, dict):
        text = value.get('text') or value.get('content') or ''
        if text:
            yield text
        return

    if not isinstance(value, list):
        return

    for item in value:
        if not isinstance(item, dict):
            continue
        item_type = item.get('type')
        if item_type in ('text', 'reasoning_text', 'output_text'):
            text = item.get('text') or item.get('content') or ''
            if text:
                yield text


def _iter_openai_delta_images(delta: Dict[str, Any]) -> Iterator[ImageBlock]:
    """从流式 delta 中提取图片块。"""
    content_delta = delta.get('content')
    if not isinstance(content_delta, list):
        return

    for item in content_delta:
        if not isinstance(item, dict) or item.get('type') != 'image_url':
            continue
        url = (item.get('image_url') or {}).get('url', '')
        if url.startswith('data:'):
            media_type, data = _parse_data_url(url)
            yield ImageBlock(type='image', media_type=media_type, source_type='base64', data=data)
        elif url:
            yield ImageBlock(type='image', media_type='', source_type='url', data=url)


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


def _parse_data_url(url: str) -> tuple[str, str]:
    """解析 data URL，返回 (media_type, base64_data)。"""
    # data:<media_type>;base64,<data>
    try:
        header, data = url.split(',', 1)
        media_type = header.split(':')[1].split(';')[0]
        return media_type, data
    except Exception:
        return '', url


def _extract_openai_image_parts(message: Dict[str, Any]) -> list[ImageBlock]:
    content = message.get('content')
    if not isinstance(content, list):
        return []
    images: list[ImageBlock] = []
    for item in content:
        if item.get('type') != 'image_url':
            continue
        url = (item.get('image_url') or {}).get('url', '')
        if url.startswith('data:'):
            media_type, data = _parse_data_url(url)
            images.append(ImageBlock(type='image', media_type=media_type, source_type='base64', data=data))
        else:
            images.append(ImageBlock(type='image', media_type='', source_type='url', data=url))
    return images


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


def _content_to_text(content: MessageContent) -> str:
    return content_to_text(content)


def _content_to_openai_blocks(content: MessageContent) -> 'list[dict] | str':
    """将 MessageContent 转为 OpenAI content blocks。
    纯字符串直接返回（向后兼容），part 列表逐项转换。
    DocumentPart 转为 data URL（OpenAI 不支持文档原生格式）。
    """
    if isinstance(content, str):
        return content
    blocks: list[dict] = []
    for part in content:
        if isinstance(part, TextPart):
            if part.text:
                blocks.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            url = part.data if part.source_type == "url" else f"data:{part.media_type};base64,{part.data}"
            blocks.append({"type": "image_url", "image_url": {"url": url}})
        elif isinstance(part, DocumentPart):
            url = f"data:{part.media_type};base64,{part.data}"
            blocks.append({"type": "image_url", "image_url": {"url": url}})
    return blocks or ""


def _serialize_messages_openai(messages: list[LLMMessage]) -> list[dict]:
    """将内部 LLMMessage 列表序列化为 OpenAI Chat Completions API 格式。

    - role="assistant" + tool_calls → tool_calls 数组（arguments JSON 序列化）
    - role="tool" + tool_call_id → {role:"tool", tool_call_id:..., content:...}
    - role="tool" 且 tool_call_id 为空 → 降级为 role="user" 文本（历史记忆回放）
    - 其他 role → content 经 _content_to_openai_blocks 转换（支持多模态）
    """
    result: list[dict] = []
    for m in messages:
        if m.role == "assistant" and m.tool_calls:
            entry: dict = {"role": "assistant"}
            if m.reasoning_content:
                entry["reasoning_content"] = m.reasoning_content
            text = _content_to_text(m.content)
            if text:
                entry["content"] = text
            entry["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["input"], ensure_ascii=False),
                    },
                }
                for tc in m.tool_calls
            ]
            result.append(entry)

        elif m.role == "tool":
            if m.tool_call_id:
                result.append({
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "content": _content_to_text(m.content),
                })
            else:
                result.append({"role": "user", "content": _content_to_text(m.content)})

        else:
            result.append({"role": m.role, "content": _content_to_openai_blocks(m.content)})

    return result


def _merge_system_prompt(req: LLMRequest) -> list[LLMMessage]:
    messages = list(req.messages)
    if not req.system_prompt:
        return messages
    for i, m in enumerate(messages):
        if m.role == 'system':
            existing = _content_to_text(m.content)
            messages[i] = LLMMessage(role='system', content=f"{req.system_prompt}\n{existing}".strip())
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
