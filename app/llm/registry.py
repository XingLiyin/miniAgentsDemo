"""LLM 注册与管理模块。"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Optional

from app.llm.base import BaseChatClient
from app.llm.provider_registry import ProviderRegistry
from app.llm.transport_httpx import HttpxTransport
from app.storage.file.llm_config_store import LLMConfigStore

SUPPORTED_LLM_STYLES = {"openai", "anthropic"}
_DISABLE_FUNCTION_INVOCATION = {"enabled": False}

logger = logging.getLogger(__name__)


@dataclass
class LLMProviderConfig:
    """LLM Provider 配置。"""

    name: str
    style: str      # openai | anthropic
    api_key: str
    base_url: str
    model: str
    timeout_sec: int = 60


class LLMRegistry:
    """LLM 注册表：内存 + 文件持久化。

    - `register(config)` 注册并写入 data/llm_configs/{name}.json
    - `load_from_store()` 启动时从文件恢复所有已保存配置
    - `delete(name)` 删除内存注册及文件
    """

    def __init__(self, provider_registry: ProviderRegistry) -> None:
        self._provider_registry = provider_registry
        self._configs: dict[str, LLMProviderConfig] = {}
        # 延迟导入避免循环
        self._store: "LLMConfigStore | None" = None

    def _get_store(self) -> "LLMConfigStore":
        if self._store is None:
            from app.storage.file.llm_config_store import LLMConfigStore
            self._store = LLMConfigStore()
        return self._store

    # ── 注册 ──────────────────────────────────────────────────────────────

    def register(self, config: LLMProviderConfig, persist: bool = True) -> None:
        """注册 LLM Provider。

        Args:
            config: Provider 配置
            persist: 是否写入 data/llm_configs/（默认 True）
        """
        if config.name in self._configs:
            raise KeyError(f"LLM 已存在: {config.name}")

        if config.style == "openai":
            self._provider_registry.register_openai(
                name=config.name,
                api_key=config.api_key,
                base_url=config.base_url,
                timeout_sec=config.timeout_sec,
            )
        elif config.style == "anthropic":
            self._provider_registry.register_anthropic(
                name=config.name,
                api_key=config.api_key,
                base_url=config.base_url,
                timeout_sec=config.timeout_sec,
            )
        else:
            raise ValueError(f"不支持的 LLM 风格: {config.style}")

        self._configs[config.name] = config

        if persist:
            self._get_store().save(asdict(config))

    # ── 查询 ──────────────────────────────────────────────────────────────

    def get_client(self, name: str) -> BaseChatClient:
        """获取 LLMClient。"""
        if name not in self._configs:
            raise KeyError(f"未注册 LLM: {name}")
        cfg = self._configs[name]
        adapter = self._provider_registry.get(name)
        return BaseChatClient(adapter=adapter, model=cfg.model)

    def get_config(self, name: str) -> LLMProviderConfig:
        """获取 LLM 配置（含 api_key，注意不要直接暴露给外部）。"""
        if name not in self._configs:
            raise KeyError(f"未注册 LLM: {name}")
        return self._configs[name]

    def list_configs(self) -> list[LLMProviderConfig]:
        """列出所有已注册 Provider。"""
        return list(self._configs.values())

    def is_registered(self, name: str) -> bool:
        return name in self._configs

    # ── 删除 ──────────────────────────────────────────────────────────────

    def delete(self, name: str) -> None:
        """删除内存注册及持久化文件。"""
        if name not in self._configs:
            raise KeyError(f"未注册 LLM: {name}")
        del self._configs[name]
        self._get_store().delete(name)

    # ── 启动恢复 ──────────────────────────────────────────────────────────

    def load_from_store(self) -> int:
        """从 data/llm_configs/ 恢复所有已保存的 Provider 配置。

        Returns:
            成功加载的配置数量。
        """
        count = 0
        for data in self._get_store().list_all():
            name = data.get("name", "")
            if not name or name in self._configs:
                continue
            try:
                config = LLMProviderConfig(
                    name=name,
                    style=data["style"],
                    api_key=data["api_key"],
                    base_url=data["base_url"],
                    model=data["model"],
                    timeout_sec=data.get("timeout_sec", 60),
                )
                self.register(config, persist=False)   # 已在磁盘，无需再写
                count += 1
                logger.info("LLMRegistry: auto-loaded provider '%s' (%s/%s)", name, config.style, config.model)
            except Exception:
                logger.exception("LLMRegistry: failed to load provider '%s'", name)
        return count


# ── 全局单例 ──────────────────────────────────────────────────────────────

_registry: Optional[LLMRegistry] = None


def get_llm_registry() -> LLMRegistry:
    """获取全局 LLM 注册表（单例）。首次调用时自动从文件恢复已保存配置。"""
    global _registry
    if _registry is None:
        transport = HttpxTransport(timeout=60)
        provider_registry = ProviderRegistry(transport)
        _registry = LLMRegistry(provider_registry)
        loaded = _registry.load_from_store()
        if loaded:
            logger.info("LLMRegistry: restored %d provider(s) from data/llm_configs/", loaded)
    return _registry


def get_llm_registry_client(name: str) -> BaseChatClient:
    """使用单例注册表获取 BaseChatClient"""
    return get_llm_registry().get_client(name)
