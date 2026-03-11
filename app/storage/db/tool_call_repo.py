"""ToolCall 持久化仓储。"""


class ToolCallRepo:
    """工具调用仓储。"""

    def get(self, tool_call_id: str) -> dict:
        """按 id 获取工具调用（TODO：查询数据库）。"""
        raise NotImplementedError('ToolCallRepo.get 未实现')
