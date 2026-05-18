"""SkillRegistry：管理四类 skill 来源。

来源优先级从高到低：
  1. workspace 文件 skill：working_dir/.skills/（子目录，每个目录一个 skill）
  2. workspace remote skill：working_dir/.skills/sources.json 声明的 MCP source
  3. local skill：settings.skills_dir，持久化在 _skills
  4. global remote skill：API 注册的 MCP source，持久化在 _source_configs

workspace 两类来源均按 working_dir 隔离，不同 session 互不干扰，不写入持久化 store。
文件扫描和 sources.json 解析结果均在 TTL 内缓存（_workspace_cache、_ws_source_config_cache）。
workspace remote 连接建立后常驻（_ws_mcp_conns），复用全局连接的建立/校验逻辑。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from functools import lru_cache
from pathlib import Path

from app.common.errors import AppError
from app.skills.definition import (
    RemoteSkillSourceConfig,
    SkillDefinition,
    SkillMetadata,
)
from app.skills.loader import SkillLoader
from app.skills.skill_mcp_conn import SkillMCPConn
from app.tools.types import CallContext

logger = logging.getLogger(__name__)

_WORKSPACE_CACHE_TTL = 5.0  # 秒，workspace 扫描 / sources.json 解析结果缓存时间


class SkillRegistry:
    """本地 skill 内存索引 + 全局/workspace 远端 skill source 连接管理。"""

    _CONNECT_COOLDOWN = 30.0
    _MAX_CONNECT_FAILURES = 5  # 连续失败达到此次数后停止自动重试

    def __init__(self) -> None:
        # ── local ──────────────────────────────────────────────────────────────
        self._skills: dict[str, SkillMetadata] = {}
        self._loader = SkillLoader()

        # ── global remote ──────────────────────────────────────────────────────
        self._source_configs: dict[str, RemoteSkillSourceConfig] = {}
        self._mcp_conns: dict[str, SkillMCPConn] = {}
        self._last_connect_attempt: dict[str, float] = {}
        self._connect_locks: dict[str, threading.Lock] = {}
        self._connect_failures: dict[str, int] = {}             # source_name → 连续失败次数
        self._remote_index: dict[str, str] = {}                 # skill_name → source_name（ephemeral）

        # ── workspace file (.skills/ 子目录) ───────────────────────────────────
        self._workspace_cache: dict[str, tuple[float, list[SkillMetadata]]] = {}

        # ── workspace remote (.skills/sources.json) ────────────────────────────
        # working_dir → (timestamp, list[config])
        self._ws_source_config_cache: dict[str, tuple[float, list[RemoteSkillSourceConfig]]] = {}
        # working_dir → {source_name → SkillMCPConn}
        self._ws_mcp_conns: dict[str, dict[str, SkillMCPConn]] = {}
        # working_dir → {source_name → last_attempt_ts}
        self._ws_last_connect: dict[str, dict[str, float]] = {}
        # working_dir → {source_name → Lock}
        self._ws_connect_locks: dict[str, dict[str, threading.Lock]] = {}
        # working_dir → {skill_name → source_name}（ephemeral，每次 list_all 刷新）
        self._ws_remote_index: dict[str, dict[str, str]] = {}

    # ── 本地 skill (skills_dir) ───────────────────────────────────────────────

    def register(self, metadata: SkillMetadata) -> None:
        self._skills[metadata.name] = metadata
        logger.debug("SkillRegistry: registered local skill '%s'", metadata.name)
        self._sync_added([metadata])

    def unregister(self, name: str) -> None:
        if name in self._skills:
            del self._skills[name]
            self._sync_removed([name])
            logger.debug("SkillRegistry: unregistered local skill '%s'", name)

    def load_from_dir(self, skills_dir: Path) -> None:
        newly_registered: list[SkillMetadata] = []
        for metadata in self._loader.scan(skills_dir.resolve()):
            self._skills[metadata.name] = metadata
            newly_registered.append(metadata)
        if newly_registered:
            self._sync_added(newly_registered)
        logger.info(
            "SkillRegistry: loaded %d local skill(s) from '%s'",
            len(self._skills), skills_dir,
        )

    # ── 全局 remote skill source ──────────────────────────────────────────────

    def register_remote_source(self, config: RemoteSkillSourceConfig) -> None:
        if config.source_name in self._source_configs:
            raise AppError(
                "SKILL_SOURCE_ALREADY_EXISTS",
                f"Remote skill source '{config.source_name}' already registered",
            )
        self._source_configs[config.source_name] = config
        self._connect_locks[config.source_name] = threading.Lock()
        logger.info("SkillRegistry: registered remote source '%s'", config.source_name)
        # 注册时立即在后台发起连接，保证 MCP 可用时首次 list_all 就能用上
        self._try_connect_source(
            config.source_name,
            config,
            self._mcp_conns,
            self._last_connect_attempt,
            self._connect_locks[config.source_name],
        )

    def unregister_remote_source(self, source_name: str) -> None:
        self._source_configs.pop(source_name, None)
        self._last_connect_attempt.pop(source_name, None)
        self._connect_locks.pop(source_name, None)
        stale = [n for n, s in self._remote_index.items() if s == source_name]
        for n in stale:
            del self._remote_index[n]
        conn = self._mcp_conns.pop(source_name, None)
        if conn:
            conn.stop()
        logger.info("SkillRegistry: unregistered remote source '%s'", source_name)

    def get_conn(self, source_name: str) -> SkillMCPConn | None:
        return self._mcp_conns.get(source_name)

    def get_conn_for_skill(self, skill_name: str) -> SkillMCPConn | None:
        """通过全局 ephemeral 索引定位 skill 所属的 MCP 连接。"""
        source_name = self._remote_index.get(skill_name)
        if not source_name:
            return None
        return self._mcp_conns.get(source_name)

    def get_ws_conn_for_skill(self, skill_name: str, working_dir: str) -> SkillMCPConn | None:
        """通过 workspace ephemeral 索引定位 skill 所属的 workspace MCP 连接。"""
        source_name = self._ws_remote_index.get(working_dir, {}).get(skill_name)
        if not source_name:
            return None
        return self._ws_mcp_conns.get(working_dir, {}).get(source_name)

    # ── 查询 ─────────────────────────────────────────────────────────────────

    def list_all(self, ctx: CallContext | None = None) -> list[SkillMetadata]:
        """四源合并，按优先级去重：ws文件 > ws remote > local > global remote。"""
        seen: set[str] = set()
        result: list[SkillMetadata] = []

        if ctx and ctx.working_dir:
            # workspace 文件 (.skills/ 子目录)
            for meta in self._fetch_workspace_live(ctx.working_dir):
                if meta.name not in seen:
                    seen.add(meta.name)
                    result.append(meta)
            # workspace remote (.skills/sources.json)
            for meta in self._fetch_ws_remote_live(ctx.working_dir, ctx):
                if meta.name not in seen:
                    seen.add(meta.name)
                    result.append(meta)

        # local
        for meta in self._skills.values():
            if meta.name not in seen:
                seen.add(meta.name)
                result.append(meta)

        # global remote
        for meta in self._fetch_remote_live(ctx):
            if meta.name not in seen:
                seen.add(meta.name)
                result.append(meta)

        return result

    def get_metadata(self, name: str, ctx: CallContext | None = None) -> SkillMetadata | None:
        """按优先级查找：ws文件 > ws remote > local。"""
        if ctx and ctx.working_dir:
            for meta in self._fetch_workspace_live(ctx.working_dir):
                if meta.name == name:
                    return meta
            for meta in self._fetch_ws_remote_live(ctx.working_dir, ctx):
                if meta.name == name:
                    return meta
        return self._skills.get(name)

    def get_metadata_block(self) -> str:
        all_skills = self.list_all()
        if not all_skills:
            return ""
        lines = ["## Available Skills (assign to tasks where appropriate)"]
        for m in all_skills:
            lines.append(f"- {m.name}: {m.description}")
        return "\n".join(lines)

    # ── Level 2 按需加载 ──────────────────────────────────────────────────────

    def load_definition(self, name: str, ctx: CallContext | None = None) -> SkillDefinition | None:
        """加载 SKILL.md 主体。优先级：ws文件 > ws remote > local > global remote。"""
        if ctx and ctx.working_dir:
            # workspace 文件
            for meta in self._fetch_workspace_live(ctx.working_dir):
                if meta.name == name:
                    try:
                        instructions = self._loader.load_instructions(meta.skill_dir)
                        return SkillDefinition(metadata=meta, instructions=instructions)
                    except Exception as e:
                        logger.warning("SkillRegistry: failed to load workspace skill '%s': %s", name, e)
                    break

            # workspace remote
            conn = self.get_ws_conn_for_skill(name, ctx.working_dir)
            if conn is not None:
                source_name = self._ws_remote_index[ctx.working_dir][name]
                try:
                    instructions = conn.load_skill_md(name, ctx)
                    meta = SkillMetadata(
                        name=name, description="", triggers=[], version="",
                        skill_dir=Path(""), source="remote",
                        remote_source_name=source_name,
                    )
                    return SkillDefinition(metadata=meta, instructions=instructions)
                except Exception as e:
                    logger.warning("SkillRegistry: failed to load ws-remote skill '%s': %s", name, e)

        # local
        meta = self._skills.get(name)
        if meta is not None:
            try:
                instructions = self._loader.load_instructions(meta.skill_dir)
                return SkillDefinition(metadata=meta, instructions=instructions)
            except Exception as e:
                logger.warning("SkillRegistry: failed to load local skill '%s': %s", name, e)
                return None

        # global remote
        conn = self.get_conn_for_skill(name)
        if conn is None:
            logger.warning(
                "SkillRegistry: skill '%s' not found (call list_all() first to populate remote index)",
                name,
            )
            return None
        source_name = self._remote_index[name]
        try:
            instructions = conn.load_skill_md(name, ctx)
            meta = SkillMetadata(
                name=name, description="", triggers=[], version="",
                skill_dir=Path(""), source="remote",
                remote_source_name=source_name,
            )
            return SkillDefinition(metadata=meta, instructions=instructions)
        except Exception as e:
            logger.warning("SkillRegistry: failed to load remote skill '%s': %s", name, e)
            return None

    # ── 内部工具：workspace 文件 ──────────────────────────────────────────────

    def _fetch_workspace_live(self, working_dir: str) -> list[SkillMetadata]:
        """扫描 working_dir/.skills/ 子目录（排除 sources.json），TTL 内缓存。"""
        now = time.monotonic()
        cached = self._workspace_cache.get(working_dir)
        if cached and now - cached[0] < _WORKSPACE_CACHE_TTL:
            return cached[1]

        ws_skills_dir = Path(working_dir) / ".skills"
        items: list[SkillMetadata] = []
        for metadata in self._loader.scan(ws_skills_dir):
            metadata.source = "workspace"
            items.append(metadata)

        self._workspace_cache[working_dir] = (now, items)
        if items:
            logger.debug(
                "SkillRegistry: scanned %d workspace skill(s) from '%s'",
                len(items), ws_skills_dir,
            )
        return items

    # ── 内部工具：workspace remote ────────────────────────────────────────────

    def _load_ws_source_configs(self, working_dir: str) -> list[RemoteSkillSourceConfig]:
        """读取 working_dir/.skills/sources.json，TTL 内缓存，文件不存在返回空列表。"""
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

    def _fetch_ws_remote_live(self, working_dir: str, ctx: CallContext | None) -> list[SkillMetadata]:
        """从 workspace remote source 拉取 skill 列表，刷新 _ws_remote_index[working_dir]。"""
        configs = self._load_ws_source_configs(working_dir)
        if not configs:
            return []

        ws_conns  = self._ws_mcp_conns.setdefault(working_dir, {})
        ws_last   = self._ws_last_connect.setdefault(working_dir, {})
        ws_locks  = self._ws_connect_locks.setdefault(working_dir, {})
        ws_index: dict[str, str] = {}
        result: list[SkillMetadata] = []

        for config in configs:
            source_name = config.source_name
            conn = ws_conns.get(source_name)
            if conn is None or not conn.is_connected:
                lock = ws_locks.setdefault(source_name, threading.Lock())
                if not self._try_connect_source(
                    source_name, config, ws_conns, ws_last, lock,
                    reset_failures=True,
                ):
                    continue
                conn = ws_conns.get(source_name)
            if conn is None:
                continue
            try:
                items = conn.list_skills(ctx)
                for item in items:
                    name = item.get("name", "")
                    if not name:
                        continue
                    ws_index[name] = source_name
                    result.append(SkillMetadata(
                        name=name,
                        description=item.get("description", ""),
                        triggers=[], version="",
                        skill_dir=Path(""),
                        source="remote",
                        remote_source_name=source_name,
                    ))
                logger.debug(
                    "SkillRegistry: ws-remote fetched %d skill(s) from '%s'",
                    len(items), source_name,
                )
            except Exception as e:
                logger.warning(
                    "SkillRegistry: ws-remote live-fetch from '%s' failed: %s",
                    source_name, e,
                )

        self._ws_remote_index[working_dir] = ws_index
        return result

    # ── 内部工具：全局 remote ─────────────────────────────────────────────────

    def _fetch_remote_live(self, ctx: CallContext | None = None) -> list[SkillMetadata]:
        """实时从全局 remote source 拉取 skill 列表，刷新 _remote_index。"""
        self._remote_index.clear()
        result: list[SkillMetadata] = []
        for source_name, config in self._source_configs.items():
            conn = self._mcp_conns.get(source_name)
            if conn is None or not conn.is_connected:
                lock = self._connect_locks.get(source_name)
                if lock is None:
                    continue
                if not self._try_connect_source(
                    source_name, config,
                    self._mcp_conns, self._last_connect_attempt, lock,
                    reset_failures=True,
                ):
                    continue
                conn = self._mcp_conns.get(source_name)
            if conn is None:
                continue
            try:
                items = conn.list_skills(ctx)
                for item in items:
                    name = item.get("name", "")
                    if not name:
                        continue
                    self._remote_index[name] = source_name
                    result.append(SkillMetadata(
                        name=name,
                        description=item.get("description", ""),
                        triggers=[], version="",
                        skill_dir=Path(""),
                        source="remote",
                        remote_source_name=source_name,
                    ))
                logger.debug(
                    "SkillRegistry: live-fetched %d skill(s) from '%s'",
                    len(items), source_name,
                )
            except Exception as e:
                logger.warning("SkillRegistry: live-fetch from '%s' failed: %s", source_name, e)
        return result

    def _try_connect_source(
        self,
        source_name: str,
        config: RemoteSkillSourceConfig,
        conn_store: dict[str, SkillMCPConn],
        last_attempt: dict[str, float],
        lock: threading.Lock,
        *,
        reset_failures: bool = False,
    ) -> bool:
        """返回 source 是否已连接；未连接时在后台发起连接并立即返回 False。

        reset_failures=True 时清零失败计数（agent 主动需要时使用）。
        失败次数达到上限后停止自动重试，直到 reset_failures=True 再次触发。
        """
        with lock:
            existing = conn_store.get(source_name)
            if existing is not None and existing.is_connected:
                return True
            if existing is not None:
                conn_store.pop(source_name, None)
                last_attempt.pop(source_name, None)  # 断连重连，跳过冷却
                try:
                    existing.stop()
                except Exception:
                    pass
            if reset_failures:
                self._connect_failures[source_name] = 0
            if self._connect_failures.get(source_name, 0) >= self._MAX_CONNECT_FAILURES:
                return False  # 达到上限，等待下次主动调用重置
            now = time.monotonic()
            if now - last_attempt.get(source_name, 0) < self._CONNECT_COOLDOWN:
                return False
            last_attempt[source_name] = now

        def _bg() -> None:
            try:
                conn = self._build_conn(config)
                self._validate_connection(conn, config)
                with lock:
                    conn_store[source_name] = conn
                    last_attempt.pop(source_name, None)
                    self._connect_failures[source_name] = 0
                logger.info("SkillRegistry: connected remote source '%s'", source_name)
            except Exception as e:
                with lock:
                    count = self._connect_failures.get(source_name, 0) + 1
                    self._connect_failures[source_name] = count
                if count >= self._MAX_CONNECT_FAILURES:
                    logger.warning(
                        "SkillRegistry: '%s' failed %d/%d times, stopping auto-retry until next demand",
                        source_name, count, self._MAX_CONNECT_FAILURES,
                    )
                else:
                    logger.warning("SkillRegistry: failed to connect '%s' (%d/%d): %s",
                                   source_name, count, self._MAX_CONNECT_FAILURES, e)

        threading.Thread(target=_bg, name=f"skill-mcp-{source_name}", daemon=True).start()
        return False

    def _validate_connection(self, conn: SkillMCPConn, config: RemoteSkillSourceConfig) -> None:
        try:
            tool_definitions = conn._provider.list_definitions()
        except Exception as e:
            conn.stop()
            raise AppError("SKILL_SOURCE_VALIDATION_FAILED", f"Failed to list MCP tools: {e}")

        tool_map = {td.name: td for td in tool_definitions}
        required = {
            config.mcp_tool_list_skills,
            config.mcp_tool_load_skill_md,
            config.mcp_tool_get_skill_files,
            config.mcp_tool_load_skill_reference,
            config.mcp_tool_exec_skill_script,
        }
        missing = required - tool_map.keys()
        if missing:
            conn.stop()
            raise AppError(
                "SKILL_SOURCE_MISSING_TOOLS",
                f"MCP server missing required tools: {sorted(missing)}",
            )

        _SCHEMA_REQUIREMENTS: dict[str, set[str]] = {
            config.mcp_tool_load_skill_md:          {"skillName"},
            config.mcp_tool_get_skill_files:        {"skillName"},
            config.mcp_tool_load_skill_reference:   {"skillName", "referencePath"},
            config.mcp_tool_exec_skill_script:      {"skillName", "scriptPath"},
        }
        for tool_name, required_params in _SCHEMA_REQUIREMENTS.items():
            td = tool_map.get(tool_name)
            if td is None:
                continue
            available = set(td.input_schema.properties.keys()) if td.input_schema else set()
            missing_params = required_params - available
            if missing_params:
                conn.stop()
                raise AppError(
                    "SKILL_SOURCE_VALIDATION_FAILED",
                    f"Tool '{tool_name}' missing params: {sorted(missing_params)}",
                )

    def _build_conn(self, config: RemoteSkillSourceConfig) -> SkillMCPConn:
        if config.mcp_type == "http":
            from app.tools.mcp_http_provider import MCPStreamableHTTPProvider
            provider = MCPStreamableHTTPProvider(
                name=config.source_name,
                url=config.mcp_url,
                timeout=config.mcp_timeout,
            )
        elif config.mcp_type == "stdio":
            from app.tools.mcp_provider import MCPStdioProvider
            provider = MCPStdioProvider(
                name=config.source_name,
                command=config.mcp_command,
                args=config.mcp_args or [],
                env=config.mcp_env or None,
            )
        else:
            raise AppError("INVALID_MCP_TYPE", f"Unsupported mcp_type: '{config.mcp_type}'")
        provider.start()
        return SkillMCPConn(
            provider=provider,
            mcp_tool_list_skills=config.mcp_tool_list_skills,
            mcp_tool_load_skill_md=config.mcp_tool_load_skill_md,
            mcp_tool_get_skill_files=config.mcp_tool_get_skill_files,
            mcp_tool_load_skill_reference=config.mcp_tool_load_skill_reference,
            mcp_tool_exec_skill_script=config.mcp_tool_exec_skill_script,
        )

    def _sync_added(self, metadatas: list[SkillMetadata]) -> None:
        try:
            from app.skills.skill_sync import get_skill_sync_service
            get_skill_sync_service().on_skills_added(metadatas)
        except Exception:
            logger.warning("SkillRegistry: sync-added failed", exc_info=True)

    def _sync_removed(self, names: list[str]) -> None:
        try:
            from app.skills.skill_sync import get_skill_sync_service
            get_skill_sync_service().on_skills_removed(names)
        except Exception:
            logger.warning("SkillRegistry: sync-removed failed for %s", names, exc_info=True)


# ── 全局单例 ──────────────────────────────────────────────────────────────────

@lru_cache
def get_skill_registry() -> SkillRegistry:
    from app.config.settings import get_settings
    registry = SkillRegistry()
    registry.load_from_dir(get_settings().skills_dir)
    return registry
