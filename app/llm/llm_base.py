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
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Optional[Dict[str, Any]] = None
    type: str = 'function'


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
