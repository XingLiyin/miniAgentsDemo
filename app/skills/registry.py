"""SkillRegistry：管理四类 skill 来源的 provider 对象。

来源优先级从高到低：
  1. workspace 文件 skill：working_dir/.skills/（LocalDirSkillProvider，TTL 缓存）
  2. workspace remote skill：working_dir/.skills/sources.json（RemoteMCPSkillProvider，按需创建）
  3. local skill：settings.skills_dir（LocalDirSkillProvider，启动时创建）
  4. global remote skill：API 注册的 MCP source（RemoteMCPSkillProvider，持久化）

Registry 持有 provider 实例，连接管理、列举逻辑封装在 provider 内部。
Skill 缓存由 Reasoner 负责（per agent + TTL），Registry 不缓存。
"""

from __future__ import annotations

import json
import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Callable

from app.skills.definition import RemoteSkillSourceConfig, SkillDefinition, SkillMetadata
from app.skills.loader import SkillLoader
from app.skills.skill import Skill
from app.skills.sources import LocalDirSkillProvider, RemoteMCPSkillProvider
from app.tools.types import CallContext

logger = logging.getLogger(__name__)

_WORKSPACE_CACHE_TTL = 5.0  # 秒，workspace provider 对象缓存时间


class SkillRegistry:
    """持有四类来源的 provider 对象，提供 fetch_skills / fetch_skill / load_definition。"""

    def __init__(self) -> None:
        self._loader = SkillLoader()
        # 描述预热回调（duck-typed，避免 skills 层依赖 runtime 层）
        self._warm_hook: "Callable[[list[tuple[str, str]]], None] | None" = None

        # ── local ──────────────────────────────────────────────────────────────
        self._local_provider: LocalDirSkillProvider | None = None

        # ── global remote ──────────────────────────────────────────────────────
        self._global_providers: dict[str, RemoteMCPSkillProvider] = {}

        # ── workspace file (.skills/ 子目录) ───────────────────────────────────
        # working_dir → (timestamp, LocalDirSkillProvider)
        self._workspace_cache: dict[str, tuple[float, LocalDirSkillProvider]] = {}

        # ── workspace remote (.skills/sources.json) ────────────────────────────
        # working_dir → (timestamp, list[config])
        self._ws_source_config_cache: dict[str, tuple[float, list[RemoteSkillSourceConfig]]] = {}
        # working_dir → {source_name → RemoteMCPSkillProvider}
        self._ws_remote_providers: dict[str, dict[str, RemoteMCPSkillProvider]] = {}

    # ── 本地 skill (skills_dir) ───────────────────────────────────────────────

    def load_from_dir(self, skills_dir: Path) -> None:
        resolved = skills_dir.resolve()
        self._local_provider = LocalDirSkillProvider(resolved, self._loader, label="local")
        metadatas = [meta for meta, _ in self._loader.scan(resolved)]
        if metadatas:
            self._sync_added(metadatas)
            self._warm_metadatas(metadatas)
        logger.info(
            "SkillRegistry: watching local skill dir '%s' (%d skill(s))",
            resolved, len(metadatas),
        )

    def set_warm_hook(self, hook: "Callable[[list[tuple[str, str]]], None] | None") -> None:
        """注册描述预热回调；skill 元数据就绪时以 [(name, description)] 调用。"""
        self._warm_hook = hook

    def _warm_metadatas(self, metadatas: list[SkillMetadata]) -> None:
        if not self._warm_hook or not metadatas:
            return
        try:
            self._warm_hook([(m.name, m.description or "") for m in metadatas])
        except Exception:
            logger.debug("SkillRegistry: warm hook failed", exc_info=True)

    # ── 全局 remote skill source ──────────────────────────────────────────────

    def register_remote_source(self, config: RemoteSkillSourceConfig) -> None:
        from app.common.errors import AppError
        if config.source_name in self._global_providers:
            raise AppError(
                "SKILL_SOURCE_ALREADY_EXISTS",
                f"Remote skill source '{config.source_name}' already registered",
            )
        provider = RemoteMCPSkillProvider(config)
        self._global_providers[config.source_name] = provider
        logger.info("SkillRegistry: registered remote source '%s'", config.source_name)
        provider.ensure_connected()

    def unregister_remote_source(self, source_name: str) -> None:
        provider = self._global_providers.pop(source_name, None)
        if provider:
            provider.stop()
        logger.info("SkillRegistry: unregistered remote source '%s'", source_name)

    def get_conn(self, source_name: str):
        provider = self._global_providers.get(source_name)
        return provider.get_conn() if provider else None

    # ── 查询 ─────────────────────────────────────────────────────────────────

    def fetch_skills(self, ctx: CallContext | None = None) -> list[Skill]:
        """四源合并，按优先级去重：ws文件 > ws remote > local > global remote。"""
        seen: set[str] = set()
        result: list[Skill] = []

        if ctx and ctx.working_dir:
            for skill in self._get_workspace_provider(ctx.working_dir).list_skills(ctx):
                if skill.name not in seen:
                    seen.add(skill.name)
                    result.append(skill)
            for provider in self._get_ws_remote_providers(ctx.working_dir):
                provider.ensure_connected(reset_failures=True)
                for skill in provider.list_skills(ctx):
                    if skill.name not in seen:
                        seen.add(skill.name)
                        result.append(skill)

        if self._local_provider:
            for skill in self._local_provider.list_skills(ctx):
                if skill.name not in seen:
                    seen.add(skill.name)
                    result.append(skill)

        for provider in self._global_providers.values():
            provider.ensure_connected(reset_failures=True)
            for skill in provider.list_skills(ctx):
                if skill.name not in seen:
                    seen.add(skill.name)
                    result.append(skill)

        return result

    def fetch_skill(self, name: str, ctx: CallContext | None = None) -> Skill | None:
        """按优先级查找单个 Skill。"""
        if ctx and ctx.working_dir:
            for skill in self._get_workspace_provider(ctx.working_dir).list_skills(ctx):
                if skill.name == name:
                    return skill
            for provider in self._get_ws_remote_providers(ctx.working_dir):
                provider.ensure_connected(reset_failures=True)
                for skill in provider.list_skills(ctx):
                    if skill.name == name:
                        return skill
        if self._local_provider:
            for skill in self._local_provider.list_skills(ctx):
                if skill.name == name:
                    return skill
        for provider in self._global_providers.values():
            provider.ensure_connected(reset_failures=True)
            for skill in provider.list_skills(ctx):
                if skill.name == name:
                    return skill
        return None

    # ── Level 2 按需加载 ──────────────────────────────────────────────────────

    def load_definition(self, name: str, ctx: CallContext | None = None) -> SkillDefinition | None:
        """加载 SKILL.md 主体，委托给 Skill.load_definition() 自动路由。"""
        skill = self.fetch_skill(name, ctx)
        if skill is None:
            logger.warning("SkillRegistry: skill '%s' not found", name)
            return None
        try:
            return skill.load_definition(ctx)
        except Exception as e:
            logger.warning("SkillRegistry: failed to load skill '%s': %s", name, e)
            return None

    # ── 内部工具：workspace 文件 ──────────────────────────────────────────────

    def _get_workspace_provider(self, working_dir: str) -> LocalDirSkillProvider:
        """返回 working_dir/.skills/ 对应的 provider，TTL 内复用。"""
        now = time.monotonic()
        cached = self._workspace_cache.get(working_dir)
        if cached and now - cached[0] < _WORKSPACE_CACHE_TTL:
            return cached[1]
        provider = LocalDirSkillProvider(
            Path(working_dir) / ".skills", self._loader, label="workspace"
        )
        self._workspace_cache[working_dir] = (now, provider)
        return provider

    # ── 内部工具：workspace remote ────────────────────────────────────────────

    def _load_ws_source_configs(self, working_dir: str) -> list[RemoteSkillSourceConfig]:
        """读取 working_dir/.skills/sources.json，TTL 内缓存。"""
        now = time.monotonic()
        cached = self._ws_source_config_cache.get(working_dir)
        if cached and now - cached[0] < _WORKSPACE_CACHE_TTL:
            return cached[1]

        sources_file = Path(working_dir) / ".skills" / "sources.json"
        configs: list[RemoteSkillSourceConfig] = []
        if sources_file.exists():
            try:
                raw = json.loads(sources_file.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    for item in raw:
                        if not isinstance(item, dict) or not item.get("source_name"):
                            continue
                        configs.append(RemoteSkillSourceConfig(
                            source_name=item["source_name"],
                            mcp_type=item.get("mcp_type", "http"),
                            mcp_url=item.get("mcp_url"),
                            mcp_timeout=item.get("mcp_timeout", 30),
                            mcp_command=item.get("mcp_command"),
                            mcp_args=item.get("mcp_args") or [],
                            mcp_env=item.get("mcp_env") or {},
                            mcp_tool_list_skills=item.get("mcp_tool_list_skills", "listSkills"),
                            mcp_tool_load_skill_md=item.get("mcp_tool_load_skill_md", "loadSkillMd"),
                            mcp_tool_get_skill_files=item.get("mcp_tool_get_skill_files", "getSkillFiles"),
                            mcp_tool_load_skill_reference=item.get("mcp_tool_load_skill_reference", "loadSkillReference"),
                            mcp_tool_exec_skill_script=item.get("mcp_tool_exec_skill_script", "execSkillScript"),
                        ))
                logger.debug(
                    "SkillRegistry: loaded %d workspace remote source(s) from '%s'",
                    len(configs), sources_file,
                )
            except Exception as e:
                logger.warning("SkillRegistry: failed to parse '%s': %s", sources_file, e)

        self._ws_source_config_cache[working_dir] = (now, configs)
        return configs

    def _get_ws_remote_providers(self, working_dir: str) -> list[RemoteMCPSkillProvider]:
        """返回 workspace remote provider 列表，按需创建并常驻。"""
        configs = self._load_ws_source_configs(working_dir)
        if not configs:
            return []
        ws_providers = self._ws_remote_providers.setdefault(working_dir, {})
        result = []
        for config in configs:
            provider = ws_providers.get(config.source_name)
            if provider is None:
                provider = RemoteMCPSkillProvider(config)
                ws_providers[config.source_name] = provider
            result.append(provider)
        return result

    # ── 同步通知 ──────────────────────────────────────────────────────────────

    def _sync_added(self, metadatas: list[SkillMetadata]) -> None:
        try:
            from app.skills.skill_sync import get_skill_sync_service
            get_skill_sync_service().on_skills_added(metadatas)
        except Exception:
            logger.warning("SkillRegistry: sync-added failed", exc_info=True)


# ── 全局单例 ──────────────────────────────────────────────────────────────────

@lru_cache
def get_skill_registry() -> SkillRegistry:
    from app.config.settings import get_settings
    registry = SkillRegistry()
    registry.load_from_dir(get_settings().skills_dir)
    return registry
