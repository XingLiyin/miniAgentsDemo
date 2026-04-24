"""SkillRegistry：管理本地 skill 和远端 skill source。

职责划分：
  - 本地 skill：持久化存储在 _skills，通过 register / load_from_dir 管理
  - 远端 skill source：连接持久化在 _mcp_conns，skill 列表每次实时从 MCP 拉取
  - _remote_index：ephemeral 索引（skill_name → source_name），每次 list_all() 时刷新，
    供 load_definition / get_conn_for_skill 路由使用
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.common.errors import AppError
from app.skills.definition import (
    RemoteSkillSourceConfig,
    SkillDefinition,
    SkillMetadata,
)
from app.skills.loader import SkillLoader
from app.skills.skill_mcp_conn import SkillMCPConn
from app.tools.definition import CallContext

logger = logging.getLogger(__name__)


class SkillRegistry:
    """本地 skill 内存索引 + 远端 skill source 连接管理。"""

    def __init__(self) -> None:
        self._skills: dict[str, SkillMetadata] = {}       # local only
        self._loader = SkillLoader()
        self._mcp_conns: dict[str, SkillMCPConn] = {}     # source_name → 连接
        self._remote_index: dict[str, str] = {}            # skill_name → source_name（ephemeral）

    # ── 本地 skill ────────────────────────────────────────────────────────────

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
        for metadata in self._loader.scan(skills_dir):
            self._skills[metadata.name] = metadata
            newly_registered.append(metadata)
        if newly_registered:
            self._sync_added(newly_registered)
        logger.info(
            "SkillRegistry: loaded %d local skill(s) from '%s'",
            len(self._skills), skills_dir,
        )

    # ── 远端 skill source ─────────────────────────────────────────────────────

    def register_remote_source(self, config: RemoteSkillSourceConfig) -> None:
        """注册远端 skill source：建连接 + 校验，不写 _skills。"""
        if config.source_name in self._mcp_conns:
            raise AppError(
                "SKILL_SOURCE_ALREADY_EXISTS",
                f"Remote skill source '{config.source_name}' already registered",
            )
        conn = self._build_conn(config)
        self._validate_connection(conn, config)
        self._mcp_conns[config.source_name] = conn
        logger.info("SkillRegistry: registered remote source '%s'", config.source_name)

    def unregister_remote_source(self, source_name: str) -> None:
        """注销远端 source：断连接，清理 _remote_index 中该 source 的条目。"""
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
        """通过 ephemeral 索引定位 skill 所属的 MCP 连接。"""
        source_name = self._remote_index.get(skill_name)
        if not source_name:
            return None
        return self._mcp_conns.get(source_name)

    # ── 查询 ─────────────────────────────────────────────────────────────────

    def list_all(self, ctx: CallContext | None = None) -> list[SkillMetadata]:
        """本地 skill（缓存）+ 远端 skill（每次实时拉取）。"""
        return list(self._skills.values()) + self._fetch_remote_live(ctx)

    def get_metadata(self, name: str) -> SkillMetadata | None:
        """仅查本地 skill。"""
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
        """加载 SKILL.md 主体内容。本地读磁盘，远端走 MCP loadSkillMd。"""
        # 本地
        meta = self._skills.get(name)
        if meta is not None:
            try:
                instructions = self._loader.load_instructions(meta.skill_dir)
                return SkillDefinition(metadata=meta, instructions=instructions)
            except Exception as e:
                logger.warning("SkillRegistry: failed to load local skill '%s': %s", name, e)
                return None

        # 远端：通过 ephemeral 索引路由
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
                name=name,
                description="",
                triggers=[],
                version="",
                skill_dir=Path(""),
                source="remote",
                remote_source_name=source_name,
            )
            return SkillDefinition(metadata=meta, instructions=instructions)
        except Exception as e:
            logger.warning("SkillRegistry: failed to load remote skill '%s': %s", name, e)
            return None

    # ── 内部工具 ──────────────────────────────────────────────────────────────

    def _fetch_remote_live(self, ctx: CallContext | None = None) -> list[SkillMetadata]:
        """实时从每个远端 source 拉取 skill 列表，刷新 _remote_index。"""
        self._remote_index.clear()
        result: list[SkillMetadata] = []
        for source_name, conn in self._mcp_conns.items():
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
                        triggers=[],
                        version="",
                        skill_dir=Path(""),
                        source="remote",
                        remote_source_name=source_name,
                    ))
                logger.debug(
                    "SkillRegistry: live-fetched %d skill(s) from '%s'",
                    len(result), source_name,
                )
            except Exception as e:
                logger.warning(
                    "SkillRegistry: live-fetch from '%s' failed: %s", source_name, e
                )
        return result

    def _validate_connection(self, conn: SkillMCPConn, config: RemoteSkillSourceConfig) -> None:
        """校验 MCP 连接：tool 存在性 + schema + listSkills 可用。失败时 stop conn 并抛出。"""
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

_registry: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
        from app.config.settings import get_settings
        _registry.load_from_dir(get_settings().skills_dir)
    return _registry
