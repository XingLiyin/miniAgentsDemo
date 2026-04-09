"""工具共享数据类型：ToolDefinition、ToolResult。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.llm.types import InputSchema, LLMTool

# 工具处理器签名：接收 arguments 字典，返回 ToolResult
ToolHandler = Callable[[dict[str, Any]], "ToolResult"]


@dataclass
class ToolResult:
    """工具调用结果（MCP 格式）。"""

    content: str
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
