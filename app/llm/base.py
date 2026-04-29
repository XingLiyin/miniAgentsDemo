"""LLM 统一接口与数据结构定义。"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterator, List, Optional, Protocol, runtime_checkable

from app.llm.types import (
    ImageBlock, LLMMessage, LLMRequest, LLMResponse, LLMTool, LLMUsage,
    ParsedResponse, StreamChunk, TextBlock, ToolCallBlock,
)


# ── 传输层 ────────────────────────────────────────────────────────────────

@runtime_checkable
class Transport(Protocol):
    """同步非流式传输层。"""

    def post(self, url: str, headers: Dict[str, str], json: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        """发送 POST 请求并返回 JSON 响应。"""
        ...


@runtime_checkable
class StreamTransport(Protocol):
    """支持 SSE 流式传输层。"""

    def stream_post(
        self,
        url: str,
        headers: Dict[str, str],
        json: Dict[str, Any],
        timeout: int,
    ) -> Iterator[str]:
        """发送 POST 请求，以迭代器逐行产出 SSE data 行（已去除 'data: ' 前缀）。"""
        ...


# ── 适配器基类 ────────────────────────────────────────────────────────────

class BaseAdapter(ABC):
    """LLM 适配器统一接口（抽象基类）。"""

    @abstractmethod
    def complete(self, req: LLMRequest) -> LLMResponse:
        """执行一次补全并返回统一响应。"""
        raise NotImplementedError

    @abstractmethod
    def parse_response(self, response: LLMResponse) -> ParsedResponse:
        """解析响应为统一的内容块结构。"""
        raise NotImplementedError

    def stream(self, req: LLMRequest) -> Iterator[StreamChunk]:
        """流式补全，逐块产出 StreamChunk。

        默认实现：调用 complete() 后将完整响应包装为单块返回，
        子类可覆写以实现真正的 token 级流式。
        """
        response = self.complete(req)
        yield StreamChunk(text_delta=response.text, usage=response.usage)
        yield StreamChunk(is_done=True, usage=response.usage)


# ── 统一客户端 ────────────────────────────────────────────────────────────

class BaseChatClient:
    """统一的 LLM 调用客户端。"""

    def __init__(self, adapter: BaseAdapter, model: str, context_limit: int = 200_000) -> None:
        self._adapter = adapter
        self._model = model
        self._context_limit = context_limit

    @property
    def context_limit(self) -> int:
        return self._context_limit

    def send_message(
        self,
        messages: List[LLMMessage],
        system_prompt: Optional[str] = None,
        tools: Optional[List[LLMTool]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        stop: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        """统一发送消息并返回完整响应。"""
        req = self._build_request(
            messages, system_prompt, tools,
            temperature, max_tokens, top_p, stop, metadata,
        )
        return self._adapter.complete(req)

    def stream_message(
        self,
        messages: List[LLMMessage],
        system_prompt: Optional[str] = None,
        tools: Optional[List[LLMTool]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        stop: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Iterator[StreamChunk]:
        """流式发送消息，逐块产出 StreamChunk。"""
        req = self._build_request(
            messages, system_prompt, tools,
            temperature, max_tokens, top_p, stop, metadata,
        )
        return self._adapter.stream(req)

    def parse_response(self, response: LLMResponse) -> ParsedResponse:
        """统一解析 LLM 响应。"""
        return self._adapter.parse_response(response)

    def parse_stream_acc(
        self,
        full_text: str,
        tool_call_acc: Dict[int, Dict[str, Any]],
        images: Optional[List[ImageBlock]] = None,
        usage: Optional[LLMUsage] = None,
    ) -> ParsedResponse:
        """将流式累积结果解析为 ParsedResponse。

        tool_call_acc 格式：{index: {id, name, arguments(JSON 字符串片段)}}
        """
        blocks: List = []
        tool_calls: List[ToolCallBlock] = []

        if full_text:
            blocks.append(TextBlock(type='text', text=full_text))

        for img in (images or []):
            blocks.append(img)

        for idx in sorted(tool_call_acc):
            buf = tool_call_acc[idx]
            args_raw = buf.get('arguments') or ''
            try:
                args = json.loads(args_raw) if args_raw else {}
            except json.JSONDecodeError:
                args = {'_raw': args_raw}
            tc = ToolCallBlock(
                type='tool_call',
                id=buf.get('id', ''),
                name=buf.get('name', ''),
                input=args,
            )
            blocks.append(tc)
            tool_calls.append(tc)

        return ParsedResponse(
            text=full_text,
            blocks=blocks,
            tool_calls=tool_calls,
            images=images or [],
            usage=usage,
        )

    def _build_request(
        self,
        messages: List[LLMMessage],
        system_prompt: Optional[str],
        tools: Optional[List[LLMTool]],
        temperature: Optional[float],
        max_tokens: Optional[int],
        top_p: Optional[float],
        stop: Optional[List[str]],
        metadata: Optional[Dict[str, Any]],
    ) -> LLMRequest:
        return LLMRequest(
            model=self._model,
            messages=messages,
            system_prompt=system_prompt,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            stop=stop,
            metadata=metadata or {},
        )
