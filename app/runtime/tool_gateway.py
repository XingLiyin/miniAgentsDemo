"""工具调用网关。"""


class ToolGateway:
    """MCP 工具调用网关。"""

    def call(self, tool_name: str, payload: dict) -> dict:
        """调用工具并返回结果（TODO：鉴权/审计/脱敏）。"""
        # TODO: PolicyEngine.authorize。
        # TODO: 记录 tool_calls。
        raise NotImplementedError('ToolGateway.call 未实现')
