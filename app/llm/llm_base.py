"""LLM 统一接口与数据结构定义。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Protocol, Literal


# ── 基础数据结构 ───────────────────────────────────────────────────────────

@dataclass
class LLMMessage:
    """统一的消息结构。"""

    role: Literal['system', 'user', 'assistant']
    content: str


@dataclass
class LLMRequest:
    """统一的请求结构。"""

    model: str
    messages: List[LLMMessage]
    system_prompt: Optional[str] = None
    tools: Optional[List['LLMTool']] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    stop: Optional[List[str]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMUsage:
    """统一的 usage 结构。"""

    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


@dataclass
class LLMResponse:
    """统一的返回结构。"""

    text: str
    raw: Dict[str, Any] = field(default_factory=dict)
    usage: Optional[LLMUsage] = None


@dataclass
class LLMTool:
    """统一的工具定义结构（function-calling）。"""

    name: str
    description: Optional[str] = None
    input_schema: 'InputSchema' = field(default_factory=lambda: InputSchema())
    output_schema: Optional[Dict[str, Any]] = None
    type: str = 'function'


# ── 内容块 ────────────────────────────────────────────────────────────────

@dataclass
class LLMContentBlock:
    """统一的内容块基类。"""

    type: str


@dataclass
class TextBlock(LLMContentBlock):
    """文本内容块。"""

    text: str


@dataclass
class ToolCallBlock(LLMContentBlock):
    """工具调用内容块。"""

    id: str
    name: str
    input: Dict[str, Any]
    tool_type: str = 'function'
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedResponse:
    """统一的解析后响应。"""

    text: str
    blocks: List[LLMContentBlock]
    tool_calls: List[ToolCallBlock]
    raw: Dict[str, Any] = field(default_factory=dict)
    usage: Optional[LLMUsage] = None


# ── 流式数据结构 ──────────────────────────────────────────────────────────

@dataclass
class StreamChunk:
    """流式输出的单个增量块。

    - text_delta: 本块新增文本（可为空字符串）
    - tool_call_delta: 工具调用增量（id/name/arguments_delta），仅 tool-call 时非 None
    - is_done: 是否为终止块（收到后不再有更多块）
    - usage: 仅终止块携带完整用量统计
    """

    text_delta: str = ''
    tool_call_delta: Optional[Dict[str, Any]] = None
    is_done: bool = False
    usage: Optional[LLMUsage] = None


# ── Schema ────────────────────────────────────────────────────────────────

@dataclass
class InputSchema:
    """统一的输入 Schema（OpenAI/Anthropic 共同支持的子集）。"""

    type: str = 'object'
    properties: Dict[str, Any] = field(default_factory=dict)
    require: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """转换为 dict 结构以适配不同 provider。"""
        return {
            'type': self.type,
            'properties': self.properties,
            'required': self.require,
        }


# ── 传输层 ────────────────────────────────────────────────────────────────

class Transport(Protocol):
    """同步非流式传输层。"""

    def post(self, url: str, headers: Dict[str, str], json: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        """发送 POST 请求并返回 JSON 响应。"""
        ...


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

class LLMClient:
    """统一的 LLM 调用客户端。"""

    def __init__(self, adapter: BaseAdapter, model: str) -> None:
        self._adapter = adapter
        self._model = model

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
