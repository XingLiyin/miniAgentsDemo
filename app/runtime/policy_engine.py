"""权限策略引擎。"""


class PolicyEngine:
    """工具调用权限策略。"""

    def authorize(self, tool_name: str, context: dict) -> bool:
        """判断是否允许调用工具（TODO：基于 role/type/tool 规则）。"""
        # TODO: 读取策略并做决策。
        raise NotImplementedError('PolicyEngine.authorize 未实现')
