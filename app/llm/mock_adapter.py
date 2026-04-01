"""Mock 适配器，用于测试（支持流式）。"""

from __future__ import annotations

from typing import Iterator

from app.llm.base import (
    BaseAdapter
)
from app.llm.types import LLMRequest, LLMResponse, LLMUsage, ParsedResponse, StreamChunk, TextBlock


class MockAdapter(BaseAdapter):
    """Mock LLM 适配器，支持自定义响应文本。"""

    def __init__(self, response_text: str = 'mock') -> None:
        self._response_text = response_text

    def complete(self, req: LLMRequest) -> LLMResponse:
        _ = req
        return LLMResponse(
            text=self._response_text,
            usage=LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

    def parse_response(self, response: LLMResponse) -> ParsedResponse:
        text = response.text
        return ParsedResponse(
            text=text,
            blocks=[TextBlock(type='text', text=text)],
            tool_calls=[],
            raw=response.raw,
            usage=response.usage,
        )

    def stream(self, req: LLMRequest) -> Iterator[StreamChunk]:
        """逐词模拟流式输出。"""
        _ = req
        words = self._response_text.split()
        for i, word in enumerate(words):
            delta = word if i == 0 else f' {word}'
            yield StreamChunk(text_delta=delta)
        yield StreamChunk(
            is_done=True,
            usage=LLMUsage(prompt_tokens=10, completion_tokens=len(words), total_tokens=10 + len(words)),
        )
