"""MockChatClient：测试用 LLM 客户端，返回固定文本响应。"""

from __future__ import annotations

from app.llm.base import BaseChatClient
from app.llm.mock_adapter import MockAdapter


class MockChatClient(BaseChatClient):
    """BaseChatClient 的测试实现，使用 MockAdapter 返回固定响应文本。"""

    def __init__(self, response_text: str = "mock") -> None:
        super().__init__(adapter=MockAdapter(response_text=response_text), model="mock")
