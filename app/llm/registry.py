"""LLM 注册与管理模块。"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Optional

from app.llm.base import BaseChatClient
from app.llm.provider_registry import ProviderRegistry
from app.llm.transport_httpx import HttpxTransport
from app.storage.file.llm_config_store import LLMConfigStore

SUPPORTED_LLM_STYLES = {"openai", "anthropic"}

logger = logging.getLogger(__name__)


@dataclass
class LLMProvider:
    """LLM Provider 配置：一个 API 端点 + 多个可用模型。"""

    name: str
    style: str
    api_key: str
    base_url: str
    models: list[str] = field(default_factory=list)
    default_model: str = ""
    timeout_sec: int = 60
    max_tokens: int = 8096


class LLMRegistry:
    """LLM 注册表：内存 + 文件持久化。"""

    def __init__(self, provider_registry: ProviderRegistry) -> None:
        self._provider_registry = provider_registry
        self._providers: dict[str, LLMProvider] = {}
        self._store: LLMConfigStore | None = None

    def _get_store(self) -> LLMConfigStore:
        if self._store is None:
            self._store = LLMConfigStore()
        return self._store

    # ── 注册 / 删除 ───────────────────────────────────────────────────────────

    def register(self, provider: LLMProvider, persist: bool = True) -> None:
        if provider.name in self._providers:
            raise KeyError(f"LLM 已存在: {provider.name}")
        if provider.style not in SUPPORTED_LLM_STYLES:
            raise ValueError(f"不支持的 LLM 风格: {provider.style}")
        if not provider.default_model and provider.models:
            provider.default_model = provider.models[0]

        self._connect(provider)
        self._providers[provider.name] = provider
        if persist:
            self._get_store().save(asdict(provider))

    def delete(self, name: str) -> None:
        if name not in self._providers:
            raise KeyError(f"未注册 LLM: {name}")
        del self._providers[name]
        self._get_store().delete(name)

    # ── 模型管理 ──────────────────────────────────────────────────────────────

    def add_model(self, name: str, model: str) -> LLMProvider:
        provider = self._get(name)
        if model not in provider.models:
            provider.models.append(model)
        if not provider.default_model:
            provider.default_model = model
        self._get_store().save(asdict(provider))
        return provider

    def remove_model(self, name: str, model: str) -> LLMProvider:
        provider = self._get(name)
        if model in provider.models:
            provider.models.remove(model)
        if provider.default_model == model:
            provider.default_model = provider.models[0] if provider.models else ""
        self._get_store().save(asdict(provider))
        return provider

    def set_default_model(self, name: str, model: str) -> LLMProvider:
        provider = self._get(name)
        if model not in provider.models:
            raise ValueError(f"模型 '{model}' 不在 provider '{name}' 的列表中")
        provider.default_model = model
        self._get_store().save(asdict(provider))
        return provider

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def get_client(self, name: str, model: str | None = None) -> BaseChatClient:
        provider = self._get(name)
        resolved_model = model or provider.default_model
        if not resolved_model:
            raise ValueError(f"Provider '{name}' 无可用模型")
        adapter = self._provider_registry.get(name)
        return BaseChatClient(adapter=adapter, model=resolved_model, default_max_tokens=provider.max_tokens)

    def get_provider(self, name: str) -> LLMProvider:
        return self._get(name)

    def list_providers(self) -> list[LLMProvider]:
        return list(self._providers.values())

    def is_registered(self, name: str) -> bool:
        return name in self._providers

    # ── 启动恢复 ──────────────────────────────────────────────────────────────

    def load_from_store(self) -> int:
        count = 0
        for data in self._get_store().list_all():
            name = data.get("name", "")
            if not name or name in self._providers:
                continue
            try:
                provider = LLMProvider(
                    name=name,
                    style=data["style"],
                    api_key=data["api_key"],
                    base_url=data["base_url"],
                    models=data.get("models", []),
                    default_model=data.get("default_model", ""),
                    timeout_sec=data.get("timeout_sec", 60),
                    max_tokens=data.get("max_tokens", 8096),
                )
                self.register(provider, persist=False)
                count += 1
                logger.info("LLMRegistry: loaded provider '%s' (%s)", name, provider.style)
            except Exception:
                logger.exception("LLMRegistry: failed to load provider '%s'", name)
        return count

    # ── 内部辅助 ──────────────────────────────────────────────────────────────

    def _get(self, name: str) -> LLMProvider:
        if name not in self._providers:
            raise KeyError(f"未注册 LLM: {name}")
        return self._providers[name]

    def _connect(self, provider: LLMProvider) -> None:
        if provider.style == "openai":
            self._provider_registry.register_openai(
                name=provider.name,
                api_key=provider.api_key,
                base_url=provider.base_url,
                timeout_sec=provider.timeout_sec,
            )
        elif provider.style == "anthropic":
            self._provider_registry.register_anthropic(
                name=provider.name,
                api_key=provider.api_key,
                base_url=provider.base_url,
                timeout_sec=provider.timeout_sec,
            )


# ── 全局单例 ──────────────────────────────────────────────────────────────────

_registry: Optional[LLMRegistry] = None


def get_llm_registry() -> LLMRegistry:
    global _registry
    if _registry is None:
        from app.config.settings import get_settings
        transport = HttpxTransport(timeout=get_settings().default_llm_timeout_sec)
        provider_registry = ProviderRegistry(transport)
        _registry = LLMRegistry(provider_registry)
        loaded = _registry.load_from_store()
        if loaded:
            logger.info("LLMRegistry: restored %d provider(s)", loaded)
    return _registry


def get_llm_registry_client(name: str, model: str | None = None) -> BaseChatClient:
    return get_llm_registry().get_client(name, model)
