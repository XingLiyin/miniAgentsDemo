"""OpenAI 适配器实现。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.llm.llm_base import BaseAdapter, LLMMessage, LLMRequest, LLMResponse, LLMTool, LLMUsage, Transport


class OpenAIAdapter(BaseAdapter):
    """OpenAI LLM 适配器。"""

    def __init__(self, api_key: str, base_url: str, transport: Transport, timeout_sec: int = 60) -> None:
        """创建 OpenAI 适配器。

        约束：transport 负责实际 HTTP 调用；本类仅组装/解析协议。
        """
        self._api_key = api_key
        self._base_url = base_url.rstrip('/')
        self._transport = transport
        self._timeout_sec = timeout_sec

    def complete(self, req: LLMRequest) -> LLMResponse:
        """统一补全接口（OpenAI Chat Completions 风格）。"""
        url = f"{self._base_url}/v1/chat/completions"
        headers = {
            'Authorization': f"Bearer {self._api_key}",
            'Content-Type': 'application/json',
        }
        messages = _merge_system_prompt(req)
        payload: Dict[str, Any] = {
            'model': req.model,
            'messages': [{'role': m.role, 'content': m.content} for m in messages],
        }
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

        resp = self._transport.post(url, headers=headers, json=payload, timeout=self._timeout_sec)
        text = _extract_openai_text(resp)
        usage = _extract_openai_usage(resp)
        return LLMResponse(text=text, raw=resp, usage=usage)


def _extract_openai_text(resp: Dict[str, Any]) -> str:
    """从 OpenAI 响应中提取文本。"""
    choices = resp.get('choices') or []
    if not choices:
        return ''
    message = choices[0].get('message') or {}
    return message.get('content') or ''


def _extract_openai_usage(resp: Dict[str, Any]) -> Optional[LLMUsage]:
    """从 OpenAI 响应中提取 usage。"""
    usage = resp.get('usage') or {}
    if not usage:
        return None
    return LLMUsage(
        prompt_tokens=usage.get('prompt_tokens'),
        completion_tokens=usage.get('completion_tokens'),
        total_tokens=usage.get('total_tokens'),
    )


def _merge_system_prompt(req: LLMRequest) -> list[LLMMessage]:
    """将 system_prompt 合并到消息列表。"""
    messages = list(req.messages)
    if not req.system_prompt:
        return messages
    for i, m in enumerate(messages):
        if m.role == 'system':
            merged = f"{req.system_prompt}\n{m.content}".strip()
            messages[i] = LLMMessage(role='system', content=merged)
            return messages
    return [LLMMessage(role='system', content=req.system_prompt)] + messages


def _map_openai_tools(tools: list[LLMTool]) -> list[dict]:
    """将统一工具结构映射为 OpenAI tools 结构。"""
    mapped: list[dict] = []
    for tool in tools:
        if tool.type != 'function':
            raise ValueError(f'OpenAI 仅支持 function 工具，当前: {tool.type}')
        fn: dict = {
            'name': tool.name,
            'parameters': tool.input_schema.to_dict(),
        }
        if tool.description:
            fn['description'] = tool.description
        mapped.append({'type': 'function', 'function': fn})
    return mapped
