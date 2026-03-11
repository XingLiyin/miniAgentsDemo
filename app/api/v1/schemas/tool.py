"""ToolCall API Schema 定义。"""

from typing import Optional

from pydantic import BaseModel


class ToolCallObject(BaseModel):
    """工具调用对象的 API Schema。"""

    id: str
    tool_name: str
    status: str
    latency_ms: Optional[int] = None
