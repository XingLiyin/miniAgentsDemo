from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Literal, Union


# ── 多模态内容 part ───────────────────────────────────────────────────────

@dataclass
class TextPart:
    text: str
    type: Literal["text"] = "text"


@dataclass
class ImagePart:
    data: str                                         # base64 字符串或 HTTP URL
    media_type: str = "image/jpeg"                    # image/png, image/gif, image/webp
    source_type: Literal["base64", "url"] = "base64"
    type: Literal["image"] = "image"


@dataclass
class DocumentPart:                                   # Anthropic 原生支持；OpenAI 降级
    data: str                                         # base64 字符串
    media_type: str = "application/pdf"
    type: Literal["document"] = "document"


ContentPart = Union[TextPart, ImagePart, DocumentPart]
MessageContent = Union[str, List[ContentPart]]


def content_to_text(content: 'MessageContent') -> str:
    """从 MessageContent 提取纯文本（支持 str、ContentPart 实例列表、dict 列表）。"""
    if isinstance(content, str):
        return content
    parts = []
    for p in content:
        if isinstance(p, TextPart):
            parts.append(p.text)
        elif isinstance(p, dict) and p.get('type') == 'text':
            parts.append(p.get('text', ''))
    return '\n'.join(parts)


def content_from_raw(v: 'str | list') -> 'MessageContent':
    """将 JSON 反序列化后的原始值（str 或 list[dict]）还原为 MessageContent。

    memory/blackboard 从 JSON 读回的 list content 是 list[dict]，
    需经此函数转换才能被适配器正确识别为 ContentPart 对象。
    """
    if isinstance(v, str):
        return v
    if not isinstance(v, list):
        return str(v)
    parts: List[ContentPart] = []
    for item in v:
        if not isinstance(item, dict):
            parts.append(item)   # 已经是 ContentPart 实例，直接保留
            continue
        t = item.get('type')
        if t == 'text':
            parts.append(TextPart(text=item.get('text', '')))
        elif t == 'image':
            parts.append(ImagePart(
                data=item.get('data', ''),
                media_type=item.get('media_type', ''),
                source_type=item.get('source_type', 'base64'),
            ))
        elif t == 'document':
            parts.append(DocumentPart(
                data=item.get('data', ''),
                media_type=item.get('media_type', ''),
            ))
    return parts


@dataclass
class LLMMessage:
    """统一的消息结构。content 支持纯文本或多模态 part 列表。"""

    role: Literal['system', 'user', 'assistant', 'tool']
    content: MessageContent
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    reasoning_content: Optional[str] = None


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
class ImageBlock(LLMContentBlock):
    """图片内容块（响应侧）。"""

    media_type: str
    source_type: Literal["base64", "url"]
    data: str                                    # base64 字符串或 URL


@dataclass
class DocumentBlock(LLMContentBlock):
    """文档内容块（响应侧，Anthropic）。"""

    media_type: str
    data: str                                    # base64 字符串


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
    images: List[ImageBlock] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)
    usage: Optional[LLMUsage] = None


# ── 流式数据结构 ──────────────────────────────────────────────────────────

@dataclass
class StreamChunk:
    """流式输出的单个增量块。

     - text_delta: 本块新增文本（可为空字符串）
    - reasoning_delta: 本块新增推理文本（可为空字符串）
    - tool_call_delta: 工具调用增量（id/name/arguments_delta），仅 tool-call 时非 None
    - image: 完整图片块（图片不分片，在 content_block_start 时一次性 yield）
    - is_done: 是否为终止块（收到后不再有更多块）
    - finish_reason: provider 返回的终止原因（如 stop / length / tool_calls）
    - usage: 仅终止块携带完整用量统计
    """

    text_delta: str = ''
    reasoning_delta: str = ''
    tool_call_delta: Optional[Dict[str, Any]] = None
    image: Optional['ImageBlock'] = None
    is_done: bool = False
    finish_reason: Optional[str] = None
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
    
