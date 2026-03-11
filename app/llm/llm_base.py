"""LLM 统一接口与数据结构定义。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Literal


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
    skills: Optional[List[Dict[str, Any]]] = None
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
    """统一的工具定义结构。"""

    name: str
    description: Optional[str] = None
    input_schema: 'InputSchema' = field(default_factory=lambda: InputSchema())
    output_schema: Optional[Dict[str, Any]] = None
    type: str = 'function'


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


class Transport(Protocol):
    """传输层抽象，用于对接 HTTP 客户端。"""

    def post(self, url: str, headers: Dict[str, str], json: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        """发送 POST 请求并返回 JSON 响应。"""
        ...


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


class LLMClient:
    """统一的 LLM 调用客户端。"""

    def __init__(self, adapter: BaseAdapter, model: str) -> None:
        """创建 LLM 客户端。"""
        self._adapter = adapter
        self._model = model

    def send_message(
        self,
        messages: List[LLMMessage],
        system_prompt: Optional[str] = None,
        tools: Optional[List[LLMTool]] = None,
        skills: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        stop: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        """统一发送消息并返回响应。"""
        req = LLMRequest(
            model=self._model,
            messages=messages,
            system_prompt=system_prompt,
            tools=tools,
            skills=skills,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            stop=stop,
            metadata=metadata or {},
        )
        return self._adapter.complete(req)

    def parse_response(self, response: LLMResponse) -> ParsedResponse:
        """统一解析 LLM 响应。"""
        return self._adapter.parse_response(response)
