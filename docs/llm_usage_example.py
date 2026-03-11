"""LLM 用法示例（文档）。"""

from app.llm.llm_base import LLMMessage, LLMRequest
from app.llm.provider_registry import ProviderRegistry
from app.llm.transport_httpx import HttpxTransport


# 1. 初始化 Transport 与 ProviderRegistry
transport = HttpxTransport(timeout=60)
registry = ProviderRegistry(transport)

# 2. 注册 Provider（生产环境请从配置读取 key）
registry.register_openai(name='openai', api_key='YOUR_OPENAI_KEY')
registry.register_anthropic(name='anthropic', api_key='YOUR_ANTHROPIC_KEY')

# 3. 构造统一请求
req = LLMRequest(
    model='gpt-4.1-mini',
    messages=[
        LLMMessage(role='system', content='你是一个严谨的助手。'),
        LLMMessage(role='user', content='写一句问候语。'),
    ],
    temperature=0.2,
    max_tokens=64,
)

# 4. 选择 provider 并调用
adapter = registry.get('openai')
resp = adapter.complete(req)
print(resp.text)
