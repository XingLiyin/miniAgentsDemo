"""Mock 适配器，用于测试。"""

from app.llm.llm_base import BaseAdapter, LLMRequest, LLMResponse, ParsedResponse, TextBlock, ToolCallBlock


class MockAdapter(BaseAdapter):
    """Mock LLM 适配器。"""

    def complete(self, req: LLMRequest) -> LLMResponse:
        """返回 mock 补全（用于测试）。"""
        _ = req
        return LLMResponse(text='mock')

    def parse_response(self, response: LLMResponse) -> ParsedResponse:
        """解析 mock 响应为统一内容块。"""
        text = response.text
        blocks = [TextBlock(type='text', text=text)]
        return ParsedResponse(text=text, blocks=blocks, tool_calls=[], raw=response.raw, usage=response.usage)
