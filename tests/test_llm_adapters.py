"""LLM 适配器单元测试。"""

from typing import Any, Dict

from app.llm.anthropic_adapter import AnthropicAdapter
from app.llm.llm_base import LLMMessage, LLMRequest, Transport
from app.llm.openai_adapter import OpenAIAdapter


class DummyTransport(Transport):
    """测试用 Transport，返回固定响应。"""

    def __init__(self, response: Dict[str, Any]) -> None:
        self._response = response
        self.last_request: Dict[str, Any] = {}

    def post(self, url: str, headers: Dict[str, str], json: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        self.last_request = {
            'url': url,
            'headers': headers,
            'json': json,
            'timeout': timeout,
        }
        return self._response


def test_openai_adapter_extract_text() -> None:
    """OpenAI 响应提取文本。"""
    transport = DummyTransport({
        'choices': [{'message': {'content': 'ok'}}],
        'usage': {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3},
    })
    adapter = OpenAIAdapter('k', 'https://api.openai.com', transport)
    req = LLMRequest(model='gpt-4.1-mini', messages=[LLMMessage(role='user', content='hi')])
    resp = adapter.complete(req)
    assert resp.text == 'ok'
    assert resp.usage is not None
    assert resp.usage.total_tokens == 3


def test_anthropic_adapter_extract_text() -> None:
    """Anthropic 响应提取文本。"""
    transport = DummyTransport({
        'content': [{'text': 'ok'}],
        'usage': {'input_tokens': 2, 'output_tokens': 3},
    })
    adapter = AnthropicAdapter('k', 'https://api.anthropic.com', transport)
    req = LLMRequest(model='claude-3-5-sonnet', messages=[
        LLMMessage(role='system', content='you are a helper'),
        LLMMessage(role='user', content='hi'),
    ])
    resp = adapter.complete(req)
    assert resp.text == 'ok'
    assert resp.usage is not None
    assert resp.usage.prompt_tokens == 2
    assert resp.usage.completion_tokens == 3
    assert transport.last_request['json'].get('system') == 'you are a helper'
    assert transport.last_request['json']['messages'][0]['role'] == 'user'
