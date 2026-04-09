from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Literal

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

    def to_prompt_text(self) -> str:
        """生成带参数签名的单行描述，用于 system prompt 的工具感知段。

        格式：name(param: type, optional?: type, with_default: type = val) — description
        """
        params = []
        required = set(self.input_schema.require or [])
        for pname, pinfo in (self.input_schema.properties or {}).items():
            ptype   = pinfo.get("type", "any")
            default = pinfo.get("default")
            if pname in required:
                params.append(f"{pname}: {ptype}")
            elif default is not None:
                params.append(f"{pname}: {ptype} = {default!r}")
            else:
                params.append(f"{pname}?: {ptype}")
        sig = f"{self.name}({', '.join(params)})"
        return f"{sig} — {self.description}" if self.description else sig


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
    