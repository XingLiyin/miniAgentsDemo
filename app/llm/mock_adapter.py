"""Mock 适配器，用于测试。"""

from app.llm.llm_base import BaseAdapter, LLMRequest, LLMResponse


class MockAdapter(BaseAdapter):
    """Mock LLM 适配器。"""

    def complete(self, req: LLMRequest) -> LLMResponse:
        """返回 mock 补全（用于测试）。"""
        _ = req
        return LLMResponse(text='mock')
