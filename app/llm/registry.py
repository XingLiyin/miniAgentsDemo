"""LLM 注册与管理模块。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache

from app.llm.base import BaseChatClient
from app.llm.provider_registry import ProviderRegistry
from app.llm.transport_httpx import HttpxTransport
from app.storage.file.llm_config_store import LLMConfigStore

SUPPORTED_LLM_STYLES = {"openai", "anthropic"}

logger = logging.getLogger(__name__)


@dataclass
class ModelConfig:
    name: str
    context_limit: int
    max_output_tokens: int = 8192

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "context_limit": self.context_limit,
            "max_output_tokens": self.max_output_tokens,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ModelConfig":
        return cls(
            name=d["name"],
            context_limit=d["context_limit"],
            max_output_tokens=d.get("max_output_tokens", 8192),
        )


@dataclass
class LLMProvider:
    """LLM Provider 配置：一个 API 端点 + 多个可用模型。"""

    name: str
    style: str
    api_key: str
    base_url: str
    models: list[ModelConfig] = field(default_factory=list)
    default_model: str = ""
    timeout_sec: int = 60

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "style": self.style,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "models": [m.to_dict() for m in self.models],
            "default_model": self.default_model,
            "timeout_sec": self.timeout_sec,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LLMProvider":
        return cls(
            name=d["name"],
            style=d["style"],
            api_key=d["api_key"],
            base_url=d["base_url"],
            models=[ModelConfig.from_dict(m) for m in d.get("models", [])],
            default_model=d.get("default_model", ""),
            timeout_sec=d.get("timeout_sec", 60),
        )


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
            provider.default_model = provider.models[0].name

        self._connect(provider)
        self._providers[provider.name] = provider
        if persist:
            self._get_store().save(provider.to_dict())

    def delete(self, name: str) -> None:
        if name not in self._providers:
            raise KeyError(f"未注册 LLM: {name}")
        del self._providers[name]
        self._get_store().delete(name)

    # ── 模型管理 ──────────────────────────────────────────────────────────────

    def add_model(self, name: str, model: str, context_limit: int | None = None) -> LLMProvider:
        from app.config.settings import get_settings
        provider = self._get(name)
        if not self._find_model(provider, model):
            provider.models.append(ModelConfig(
                name=model,
                context_limit=context_limit if context_limit is not None else get_settings().default_context_limit,
            ))
        if not provider.default_model:
            provider.default_model = model
        self._get_store().save(provider.to_dict())
        return provider

    def remove_model(self, name: str, model: str) -> LLMProvider:
        provider = self._get(name)
        provider.models = [m for m in provider.models if m.name != model]
        if provider.default_model == model:
            provider.default_model = provider.models[0].name if provider.models else ""
        self._get_store().save(provider.to_dict())
        return provider

    def set_default_model(self, name: str, model: str) -> LLMProvider:
        provider = self._get(name)
        if not self._find_model(provider, model):
            raise ValueError(f"模型 '{model}' 不在 provider '{name}' 的列表中")
        provider.default_model = model
        self._get_store().save(provider.to_dict())
        return provider

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def get_client(self, name: str, model: str | None = None) -> BaseChatClient:
        from app.config.settings import get_settings
        provider = self._get(name)
        resolved_model = model or provider.default_model
        if not resolved_model:
            raise ValueError(f"Provider '{name}' 无可用模型")
        model_cfg = self._find_model(provider, resolved_model)
        context_limit = model_cfg.context_limit if model_cfg else get_settings().default_context_limit
        max_output_tokens = model_cfg.max_output_tokens if model_cfg else 8192
        adapter = self._provider_registry.get(name)
        return BaseChatClient(
            adapter=adapter,
            model=resolved_model,
            context_limit=context_limit,
            max_output_tokens=max_output_tokens,
        )

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
                provider = LLMProvider.from_dict(data)
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

    def _find_model(self, provider: LLMProvider, model_name: str) -> ModelConfig | None:
        return next((m for m in provider.models if m.name == model_name), None)

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

@lru_cache
def get_llm_registry() -> LLMRegistry:
    from app.config.settings import get_settings
    settings = get_settings()
    transport = HttpxTransport(timeout=settings.default_llm_timeout_sec)
    registry = LLMRegistry(ProviderRegistry(transport))
    loaded = registry.load_from_store()
    if loaded:
        logger.info("LLMRegistry: restored %d provider(s)", loaded)

    # Bootstrap default provider from env if not already registered via UI/storage
    name = settings.default_llm_provider
    if (name
            and not registry.is_registered(name)
            and settings.default_llm_api_key
            and settings.default_llm_base_url):
        models = (
            [ModelConfig(
                name=settings.default_llm_model,
                context_limit=settings.default_llm_context_limit,
                max_output_tokens=settings.default_llm_max_output_tokens,
            )]
            if settings.default_llm_model else []
        )
        provider = LLMProvider(
            name=name,
            style=settings.default_llm_style,
            api_key=settings.default_llm_api_key,
            base_url=settings.default_llm_base_url,
            models=models,
            default_model=settings.default_llm_model,
            timeout_sec=settings.default_llm_timeout_sec,
        )
        registry.register(provider, persist=False)
        logger.info("LLMRegistry: bootstrapped default provider '%s' from env", name)

    return registry


def get_llm_registry_client(name: str, model: str | None = None) -> BaseChatClient:
    return get_llm_registry().get_client(name, model)
