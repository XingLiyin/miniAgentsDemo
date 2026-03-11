"""LLM Provider Registry。"""

from dataclasses import dataclass
from typing import Dict, List

from app.llm.anthropic_adapter import AnthropicAdapter
from app.llm.llm_base import BaseAdapter, Transport
from app.llm.openai_adapter import OpenAIAdapter


class ProviderRegistry:
    """LLM 提供方注册表（区分 OpenAI/Anthropic 风格注册）。"""

    def __init__(self, transport: Transport) -> None:
        """初始化注册表并注入 transport。"""
        self._transport = transport
        self._providers: Dict[str, BaseAdapter] = {}
        self._openai_names: set[str] = set()
        self._anthropic_names: set[str] = set()

    def register_openai(
        self,
        name: str,
        api_key: str,
        base_url: str = 'https://api.openai.com',
        timeout_sec: int = 60,
    ) -> None:
        """注册 OpenAI 风格 provider。"""
        if name in self._providers:
            raise KeyError(f'OpenAI provider 已存在: {name}')
        self._providers[name] = OpenAIAdapter(api_key, base_url, self._transport, timeout_sec)
        self._openai_names.add(name)

    def register_anthropic(
        self,
        name: str,
        api_key: str,
        base_url: str = 'https://api.anthropic.com',
        timeout_sec: int = 60,
    ) -> None:
        """注册 Anthropic 风格 provider。"""
        if name in self._providers:
            raise KeyError(f'Anthropic provider 已存在: {name}')
        self._providers[name] = AnthropicAdapter(api_key, base_url, self._transport, timeout_sec)
        self._anthropic_names.add(name)

    def get(self, name: str) -> BaseAdapter:
        """按名称获取 provider（风格在注册时确定）。"""
        if name not in self._providers:
            raise KeyError(f'未注册 provider: {name}')
        return self._providers[name]

    def list_providers(self) -> Dict[str, str]:
        """列出已注册 provider 与风格。"""
        result: List[ProviderInfo] = []
        for name in self._providers.keys():
            if name in self._openai_names:
                style = 'openai'
            elif name in self._anthropic_names:
                style = 'anthropic'
            else:
                style = 'custom'
            result.append(ProviderInfo(name=name, style=style))
        return result


@dataclass(frozen=True)
class ProviderInfo:
    """Provider 描述信息。"""

    name: str
    style: str
