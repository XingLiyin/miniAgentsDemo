"""LLM 用法示例（文档，按调用链）。"""

import pathlib
import sys

# 允许直接运行脚本时找到项目根目录
ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from app.llm.llm_base import LLMMessage
from app.llm.registry import LLMProviderConfig, get_llm_registry, get_llm_registry_client


def main() -> None:
    """示例入口。"""
    # 1. 通过 API 或代码注册 LLM（内存保存）
    llm_registry = get_llm_registry()
    llm_registry.register(
        LLMProviderConfig(
            name='MS/MiniMax/MiniMax-M2.5',
            style='openai',
            api_key='ms-b7b41a2f-30d3-47e7-b8da-d335d1b2ff02',
            base_url='https://api-inference.modelscope.cn',
            model='MiniMax/MiniMax-M2.5',
            timeout_sec=60,
        )
    )

    # 2. 获取 LLMClient
    client = get_llm_registry_client('MS/MiniMax/MiniMax-M2.5')

    # 3. 调用并解析
    resp = client.send_message(
        messages=[
            LLMMessage(role='system', content='你是一个严谨的助手。'),
            LLMMessage(role='user', content='写一句问候语。'),
        ],
        temperature=0.2,
        max_tokens=64,
    )
    parsed = client.parse_response(resp)
    print(parsed.text)


if __name__ == '__main__':
    main()
