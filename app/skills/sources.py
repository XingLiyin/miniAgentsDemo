"""Skill source adapters — fetch content from a specific backend.

Each adapter knows how to load instructions, files, references, and run scripts
for a skill that lives in its backend. Registry attaches one adapter per Skill.
"""

from __future__ import annotations

import glob as _glob
import logging
import os
import re
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.common.errors import AppError
from app.config.settings import get_settings
from app.skills.definition import RemoteSkillSourceConfig, SkillMetadata
from app.skills.loader import SkillLoader
from app.skills.skill_mcp_conn import SkillMCPConn
from app.tools.types import ToolResult
from app.tools.utils import BASH_BLACKLIST, _resolve_cwd, build_venv_env

if TYPE_CHECKING:
    from app.tools.types import CallContext

logger = logging.getLogger(__name__)

_CONNECT_COOLDOWN = 30.0
_MAX_CONNECT_FAILURES = 5


class SkillSource(ABC):
    """Abstract backend for fetching skill content."""

    @abstractmethod
    def load_instructions(self, skill_name: str, ctx: "CallContext | None") -> str: ...

    @abstractmethod
    def get_files(self, skill_name: str, pattern: str, limit: int,
                  ctx: "CallContext | None") -> str: ...

    @abstractmethod
    def load_reference(self, skill_name: str, ref_path: str,
                       ctx: "CallContext | None") -> str: ...

    @abstractmethod
    def exec_script(self, skill_name: str, script_path: str, args: str,
                    ctx: "CallContext | None") -> ToolResult: ...


@dataclass
class LocalFileSkillSource(SkillSource):
    """Fetches skill content from a local filesystem directory."""

    skill_dir: Path
    loader: SkillLoader
    label: str = "local"  # "local" | "workspace"

    def load_instructions(self, skill_name: str, ctx: "CallContext | None" = None) -> str:
        return self.loader.load_instructions(self.skill_dir)

    def get_files(self, skill_name: str, pattern: str = "**/*", limit: int = 200,
                  ctx: "CallContext | None" = None) -> str:
        if not self.skill_dir.exists():
            raise AppError("SKILL_DIR_NOT_FOUND",
                           f"Skill directory '{self.skill_dir}' does not exist")
        matches = _glob.glob(pattern, root_dir=str(self.skill_dir), recursive=True)
        files = [
            m for m in matches
            if (self.skill_dir / m).is_file()
            and not any(part.startswith(".") for part in Path(m).parts)
        ]
        files.sort()
        truncated = len(files) > limit
        if truncated:
            files = files[:limit]
        output = "\n".join(files) if files else "(no files)"
        if truncated:
            output += f"\n[truncated at {limit} results]"
        return output

    def load_reference(self, skill_name: str, ref_path: str,
                       ctx: "CallContext | None" = None) -> str:
        try:
            return self.loader.load_resource(self.skill_dir, ref_path)
        except ValueError as exc:
            raise AppError("INVALID_ARGUMENT", str(exc))
        except FileNotFoundError:
            raise AppError("FILE_NOT_FOUND",
                           f"Reference '{ref_path}' not found in skill '{skill_name}'")

    def exec_script(self, skill_name: str, script_path: str, args: str = "",
                    ctx: "CallContext | None" = None) -> ToolResult:
        resolved = (self.skill_dir / script_path).resolve()
        try:
            resolved.relative_to(self.skill_dir.resolve())
        except ValueError:
            raise AppError("INVALID_ARGUMENT", "script_path escapes skill directory")
        if not resolved.is_file():
            raise AppError("SCRIPT_NOT_FOUND",
                           f"Script '{script_path}' not found in skill '{skill_name}'")

        abs_skill_dir = self.skill_dir.resolve()
        command = (f'python "{resolved}" {args}' if resolved.suffix == ".py"
                   else f'"{resolved}" {args}').strip()

        for blocked in BASH_BLACKLIST:
            if re.search(blocked, command):
                raise AppError("TOOL_COMMAND_BLOCKED",
                               f"Command blocked by blacklist: {blocked}")

        settings = get_settings()
        timeout_sec = settings.bash_exec_timeout_ms / 1000
        cwd = _resolve_cwd(ctx) or str(Path.cwd())
        env = build_venv_env(cwd) or os.environ.copy()
        env["SKILL_DIR"] = str(abs_skill_dir)

        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=timeout_sec, cwd=cwd, env=env,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            limit_bytes = settings.bash_exec_output_limit_bytes
            if len(output.encode("utf-8")) > limit_bytes:
                output = (output.encode("utf-8")[:limit_bytes]
                          .decode("utf-8", errors="replace")
                          + f"\n[output truncated at {limit_bytes} bytes]")
            is_error = proc.returncode != 0
            return ToolResult(
                content=output, is_error=is_error,
                error_code="SCRIPT_NONZERO_EXIT" if is_error else None,
                metadata={"exit_code": proc.returncode, "skill": skill_name,
                          "script": script_path},
            )
        except subprocess.TimeoutExpired:
            raise AppError("TOOL_TIMEOUT",
                           f"exec_skill_script timed out after {timeout_sec}s")
        except Exception as exc:
            return ToolResult(
                content=f"Failed to launch script: {exc}", is_error=True,
                error_code="SCRIPT_LAUNCH_ERROR",
                metadata={"skill": skill_name, "script": script_path},
            )


@dataclass
class RemoteSkillSource(SkillSource):
    """Fetches skill content via an MCP connection."""

    source_name: str
    conn: SkillMCPConn
    label: str = "remote"

    def load_instructions(self, skill_name: str, ctx: "CallContext | None" = None) -> str:
        return self.conn.load_skill_md(skill_name, ctx)

    def get_files(self, skill_name: str, pattern: str = "**/*", limit: int = 200,
                  ctx: "CallContext | None" = None) -> str:
        try:
            return self.conn.get_skill_files(skill_name, pattern, limit, ctx)
        except Exception as exc:
            raise AppError("REMOTE_SKILL_ERROR", str(exc))

    def load_reference(self, skill_name: str, ref_path: str,
                       ctx: "CallContext | None" = None) -> str:
        try:
            return self.conn.load_skill_reference(skill_name, ref_path, ctx)
        except Exception as exc:
            raise AppError("REMOTE_REFERENCE_ERROR", str(exc))

    def exec_script(self, skill_name: str, script_path: str, args: str = "",
                    ctx: "CallContext | None" = None) -> ToolResult:
        try:
            return self.conn.exec_skill_script(skill_name, script_path, args, ctx)
        except Exception as exc:
            raise AppError("REMOTE_SCRIPT_ERROR", str(exc))


# ── Registry-level providers（按来源枚举 Skill 列表） ─────────────────────────


class LocalDirSkillProvider:
    """从本地目录枚举 skill，每次调用 list_skills 时按需扫描。"""

    def __init__(self, skills_dir: Path, loader: SkillLoader, label: str = "local") -> None:
        self.skills_dir = skills_dir
        self._loader = loader
        self.label = label

    def list_skills(self, ctx: "CallContext | None" = None) -> list:
        from app.skills.skill import Skill
        result = []
        for metadata, skill_dir in self._loader.scan(self.skills_dir):
            result.append(Skill(
                metadata=metadata,
                source=LocalFileSkillSource(skill_dir=skill_dir, loader=self._loader, label=self.label),
            ))
        if result:
            logger.debug(
                "LocalDirSkillProvider: scanned %d skill(s) from '%s'",
                len(result), self.skills_dir,
            )
        return result


class RemoteMCPSkillProvider:
    """管理单个 MCP 连接，枚举远端 skill 列表，自带连接重试逻辑。"""

    def __init__(self, config: RemoteSkillSourceConfig) -> None:
        self.config = config
        self._conn: SkillMCPConn | None = None
        self._last_attempt: float = 0.0
        self._failures: int = 0
        self._lock = threading.Lock()

    @property
    def source_name(self) -> str:
        return self.config.source_name

    @property
    def label(self) -> str:
        return "remote"

    @property
    def is_connected(self) -> bool:
        return self._conn is not None and self._conn.is_connected

    def get_conn(self) -> SkillMCPConn | None:
        return self._conn

    def ensure_connected(self, *, reset_failures: bool = False) -> bool:
        """返回是否已连接；未连接时在后台发起连接并立即返回 False。"""
        with self._lock:
            if self._conn is not None and self._conn.is_connected:
                return True
            if self._conn is not None:
                try:
                    self._conn.stop()
                except Exception:
                    pass
                self._conn = None
                self._last_attempt = 0.0
            if reset_failures:
                self._failures = 0
            if self._failures >= _MAX_CONNECT_FAILURES:
                return False
            now = time.monotonic()
            if now - self._last_attempt < _CONNECT_COOLDOWN:
                return False
            self._last_attempt = now

        source_name = self.config.source_name

        def _bg() -> None:
            try:
                conn = self._build_conn()
                self._validate_conn(conn)
                with self._lock:
                    self._conn = conn
                    self._last_attempt = 0.0
                    self._failures = 0
                logger.info("RemoteMCPSkillProvider: connected '%s'", source_name)
            except Exception as e:
                with self._lock:
                    self._failures += 1
                    count = self._failures
                if count >= _MAX_CONNECT_FAILURES:
                    logger.warning(
                        "RemoteMCPSkillProvider: '%s' failed %d/%d times, stopping auto-retry",
                        source_name, count, _MAX_CONNECT_FAILURES,
                    )
                else:
                    logger.warning(
                        "RemoteMCPSkillProvider: failed to connect '%s' (%d/%d): %s",
                        source_name, count, _MAX_CONNECT_FAILURES, e,
                    )

        threading.Thread(target=_bg, name=f"skill-mcp-{source_name}", daemon=True).start()
        return False

    def stop(self) -> None:
        if self._conn:
            self._conn.stop()

    def list_skills(self, ctx: "CallContext | None" = None) -> list:
        if not self.is_connected or self._conn is None:
            return []
        from app.skills.skill import Skill
        try:
            items = self._conn.list_skills(ctx)
        except Exception as e:
            logger.warning(
                "RemoteMCPSkillProvider: list_skills from '%s' failed: %s",
                self.source_name, e,
            )
            return []
        result = []
        for item in items:
            name = item.get("name", "")
            if not name:
                continue
            result.append(Skill(
                metadata=SkillMetadata(
                    name=name,
                    description=item.get("description", ""),
                    triggers=[], version="",
                ),
                source=RemoteSkillSource(source_name=self.source_name, conn=self._conn),
            ))
        logger.debug(
            "RemoteMCPSkillProvider: listed %d skill(s) from '%s'",
            len(result), self.source_name,
        )
        return result

    def _build_conn(self) -> SkillMCPConn:
        config = self.config
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

    def _validate_conn(self, conn: SkillMCPConn) -> None:
        config = self.config
        try:
            tool_definitions = conn._provider.list_definitions()
        except Exception as e:
            conn.stop()
            raise AppError("SKILL_SOURCE_VALIDATION_FAILED", f"Failed to list MCP tools: {e}")

        tool_map = {td.name: td for td in tool_definitions}
        required = {
            config.mcp_tool_list_skills, config.mcp_tool_load_skill_md,
            config.mcp_tool_get_skill_files, config.mcp_tool_load_skill_reference,
            config.mcp_tool_exec_skill_script,
        }
        missing = required - tool_map.keys()
        if missing:
            conn.stop()
            raise AppError(
                "SKILL_SOURCE_MISSING_TOOLS",
                f"MCP server missing required tools: {sorted(missing)}",
            )
        _SCHEMA: dict[str, set[str]] = {
            config.mcp_tool_load_skill_md:        {"skillName"},
            config.mcp_tool_get_skill_files:      {"skillName"},
            config.mcp_tool_load_skill_reference: {"skillName", "referencePath"},
            config.mcp_tool_exec_skill_script:    {"skillName", "scriptPath"},
        }
        for tool_name, required_params in _SCHEMA.items():
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
