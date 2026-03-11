"""Anthropic 适配器实现。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.llm.llm_base import BaseAdapter, LLMRequest, LLMResponse, LLMTool, LLMUsage, Transport


class AnthropicAdapter(BaseAdapter):
    """Anthropic LLM 适配器。"""

    def __init__(self, api_key: str, base_url: str, transport: Transport, timeout_sec: int = 60) -> None:
        """创建 Anthropic 适配器。

        约束：transport 负责实际 HTTP 调用；本类仅组装/解析协议。
        """
        self._api_key = api_key
        self._base_url = base_url.rstrip('/')
        self._transport = transport
        self._timeout_sec = timeout_sec

    def complete(self, req: LLMRequest) -> LLMResponse:
        """统一补全接口（Anthropic Messages 风格）。"""
        url = f"{self._base_url}/v1/messages"
        headers = {
            'x-api-key': self._api_key,
            'anthropic-version': '2023-06-01',
            'Content-Type': 'application/json',
        }
        system_text, messages = _split_system_messages(req)
        if req.system_prompt:
            system_text = f"{req.system_prompt}\n{system_text}".strip()
        payload: Dict[str, Any] = {
            'model': req.model,
            'messages': [{'role': m.role, 'content': m.content} for m in messages],
        }
        if system_text:
            payload['system'] = system_text
        if req.temperature is not None:
            payload['temperature'] = req.temperature
        if req.max_tokens is None:
            raise ValueError('Anthropic 请求必须提供 max_tokens')
        payload['max_tokens'] = req.max_tokens
        if req.top_p is not None:
            payload['top_p'] = req.top_p
        if req.stop is not None:
            payload['stop_sequences'] = req.stop
        if req.tools is not None:
            payload['tools'] = _map_anthropic_tools(req.tools)

        resp = self._transport.post(url, headers=headers, json=payload, timeout=self._timeout_sec)
        text = _extract_anthropic_text(resp)
        usage = _extract_anthropic_usage(resp)
        return LLMResponse(text=text, raw=resp, usage=usage)


def _extract_anthropic_text(resp: Dict[str, Any]) -> str:
    """从 Anthropic 响应中提取文本。"""
    content = resp.get('content') or []
    if not content:
        return ''
    first = content[0] or {}
    return first.get('text') or ''


def _extract_anthropic_usage(resp: Dict[str, Any]) -> Optional[LLMUsage]:
    """从 Anthropic 响应中提取 usage。"""
    usage = resp.get('usage') or {}
    if not usage:
        return None
    return LLMUsage(
        prompt_tokens=usage.get('input_tokens'),
        completion_tokens=usage.get('output_tokens'),
        total_tokens=None,
    )


def _split_system_messages(req: LLMRequest) -> tuple[str, list]:
    """将 system 消息提取为 Anthropic 的 system 字段。"""
    system_parts = []
    non_system = []
    for m in req.messages:
        if m.role == 'system':
            system_parts.append(m.content)
        else:
            non_system.append(m)
    return '\n'.join(system_parts).strip(), non_system


def _map_anthropic_tools(tools: list[LLMTool]) -> list[dict]:
    """将统一工具结构映射为 Anthropic tools 结构。"""
    mapped: list[dict] = []
    for tool in tools:
        if tool.type != 'function':
            raise ValueError(f'Anthropic 仅支持 function 工具，当前: {tool.type}')
        item: dict = {
            'name': tool.name,
            'input_schema': tool.input_schema,
        }
        if tool.description:
            item['description'] = tool.description
        mapped.append(item)
    return mapped
