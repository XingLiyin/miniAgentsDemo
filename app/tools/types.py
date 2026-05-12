"""工具共享数据类型：ToolDefinition、ToolResult、CallContext。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from app.llm.types import InputSchema, LLMTool

if TYPE_CHECKING:
    from app.domain.models.task import Task


@dataclass
class CallContext:
    """工具调用上下文，由调用方构造并传入每个 handler。

    - MCP handler 读 session_id 注入 _meta
    - 控制工具直接读写 task 对象（mutation pattern）
    - 内置工具可忽略所有字段
    """

    session_id: str
    agent_id: str = ""
    task: "Task | None" = field(default=None, repr=False)
    working_dir: str = ""  # 解析后的绝对路径，空 = 进程 cwd


# 工具处理器签名：接收 arguments 字典和调用上下文，返回 ToolResult
ToolHandler = Callable[[dict[str, Any], "CallContext"], "ToolResult"]


@dataclass
class ToolResult:
    """工具调用结果。content 支持纯文本或多模态 list[ContentPart dict]。"""

    content: str | list = ""
    is_error: bool = False
    error_code: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolDefinition:
    """单个工具的完整描述。"""

    name: str
    description: str
    input_schema: InputSchema
    handler: ToolHandler

    def to_llm_tool(self) -> LLMTool:
        """转换为可传给 LLMRequest.tools 的 LLMTool 对象。"""
        return LLMTool(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            type="function",
        )

    def to_prompt_text(self) -> str:
        """生成带参数签名的单行描述，用于 system prompt 的工具感知段。"""
        return self.to_llm_tool().to_prompt_text()
