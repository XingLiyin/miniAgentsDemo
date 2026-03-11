"""ToolCall 领域模型。"""


class ToolCall:
    """工具调用记录领域对象。"""

    def __init__(self, call_id: str, tool_name: str, status: str) -> None:
        """创建工具调用对象。"""
        self.id = call_id
        self.tool_name = tool_name
        self.status = status
