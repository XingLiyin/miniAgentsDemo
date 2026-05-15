"""Skill 执行工具：get_skill_files、load_skill_reference、exec_skill_script。"""

from __future__ import annotations

import glob as _glob
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Annotated

from app.common.errors import AppError
from app.config.settings import get_settings
from app.tools.types import CallContext, ToolDefinition, ToolResult
from app.tools.utils import BASH_BLACKLIST, _resolve_cwd, build_venv_env, extract_input_schema, make_tool_handler

logger = logging.getLogger(__name__)

_SKILL_EXECUTOR_TOOLS: list[ToolDefinition] = []


def _get_remote_conn(registry, skill_name: str, ctx: "CallContext | None"):
    """ws-remote 优先，fallback global remote；都没有返回 None。"""
    if ctx and ctx.working_dir:
        conn = registry.get_ws_conn_for_skill(skill_name, ctx.working_dir)
        if conn is not None:
            return conn
    return registry.get_conn_for_skill(skill_name)


def skill_executor_tool(fn):
    tool_def = ToolDefinition(
        name=fn.__name__,
        description=(fn.__doc__ or "").strip(),
        input_schema=extract_input_schema(fn, exclude={"ctx"}),
        handler=make_tool_handler(fn),
    )
    _SKILL_EXECUTOR_TOOLS.append(tool_def)
    return tool_def


# ── get_skill_files ───────────────────────────────────────────────────────────

@skill_executor_tool
def get_skill_files(
    pattern: Annotated[str, "Glob pattern to match files (default: '**/*', local skills only)"] = "**/*",
    limit: Annotated[int, "Maximum number of results (default: 200, local skills only)"] = 200,
    *,
    ctx: CallContext | None = None,
) -> ToolResult:
    """List files available in the current task's skill directory. For local skills, supports glob patterns (e.g. 'scripts/*.py', 'references/**'). Returns a newline-separated list of file paths (local) or file names (remote). For remote skills, also returns file IDs usable with load_skill_reference and exec_skill_script."""
    skill_name = ctx.task.settings.get("skill_name") if (ctx and ctx.task and ctx.task.settings) else None
    if not skill_name:
        raise AppError("MISSING_SKILL_NAME", "Task has no skill assigned; cannot list skill files")

    from app.skills.registry import get_skill_registry
    registry = get_skill_registry()
    metadata = registry.get_metadata(skill_name, ctx)

    if metadata is None or metadata.source == "remote":
        conn = _get_remote_conn(registry, skill_name, ctx)
        if conn is None:
            raise AppError("SKILL_NOT_FOUND", f"Skill '{skill_name}' not found in registry")
        try:
            content = conn.get_skill_files(skill_name, pattern, limit, ctx)
        except Exception as exc:
            raise AppError("REMOTE_SKILL_ERROR", str(exc))
        return ToolResult(content=content, metadata={"skill": skill_name, "source": "remote"})

    skill_dir = metadata.skill_dir
    if not skill_dir.exists():
        raise AppError("SKILL_DIR_NOT_FOUND", f"Skill directory '{skill_dir}' does not exist")

    matches = _glob.glob(pattern, root_dir=str(skill_dir), recursive=True)
    files = [
        m for m in matches
        if (skill_dir / m).is_file()
        and not any(part.startswith(".") for part in Path(m).parts)
    ]
    files.sort()
    truncated = False
    if len(files) > limit:
        files = files[:limit]
        truncated = True
    output = "\n".join(files) if files else "(no files)"
    if truncated:
        output += f"\n[truncated at {limit} results]"
    return ToolResult(
        content=output,
        metadata={"skill": skill_name, "source": "local", "count": len(files), "truncated": truncated},
    )


# ── load_skill_reference ──────────────────────────────────────────────────────

@skill_executor_tool
def load_skill_reference(
    reference_path: Annotated[str, "Relative path to the reference file within the skill directory"],
    *,
    ctx: CallContext | None = None,
) -> ToolResult:
    """Read a reference file from the current task's skill directory. Use the relative path as shown in the skill instructions (e.g. 'references/background.md', 'checks/self_check.md')."""
    if not reference_path:
        raise AppError("INVALID_ARGUMENT", "reference_path is required")

    skill_name = ctx.task.settings.get("skill_name") if (ctx and ctx.task and ctx.task.settings) else None
    if not skill_name:
        raise AppError("MISSING_SKILL_NAME", "Task has no skill assigned; cannot load skill reference")

    from app.skills.registry import get_skill_registry
    registry = get_skill_registry()
    metadata = registry.get_metadata(skill_name, ctx)

    if metadata is None or metadata.source == "remote":
        conn = _get_remote_conn(registry, skill_name, ctx)
        if conn is None:
            raise AppError("SKILL_NOT_FOUND", f"Skill '{skill_name}' not found in registry")
        try:
            content = conn.load_skill_reference(skill_name, reference_path, ctx)
        except Exception as exc:
            raise AppError("REMOTE_REFERENCE_ERROR", str(exc))
        return ToolResult(content=content, metadata={"skill": skill_name, "path": reference_path})

    from app.skills.loader import SkillLoader
    try:
        content = SkillLoader().load_resource(metadata.skill_dir, reference_path)
    except ValueError as exc:
        raise AppError("INVALID_ARGUMENT", str(exc))
    except FileNotFoundError:
        raise AppError("FILE_NOT_FOUND", f"Reference '{reference_path}' not found in skill '{skill_name}'")

    return ToolResult(
        content=content,
        metadata={"skill": skill_name, "path": reference_path},
    )


# ── exec_skill_script ─────────────────────────────────────────────────────────

@skill_executor_tool
def exec_skill_script(
    script_path: Annotated[str, "Relative path to the script within the skill directory (e.g. 'scripts/extract.py')"],
    args: Annotated[str, "Command-line argument string appended after the script path"] = "",
    *,
    ctx: CallContext | None = None,
) -> ToolResult:
    """Execute a script from the current task's skill directory. The working directory is set to the skill root. Construct args as described in the skill instructions."""
    if not script_path:
        raise AppError("INVALID_ARGUMENT", "script_path is required")

    skill_name = ctx.task.settings.get("skill_name") if (ctx and ctx.task and ctx.task.settings) else None
    if not skill_name:
        raise AppError("MISSING_SKILL_NAME", "Task has no skill assigned; cannot execute skill script")

    from app.skills.registry import get_skill_registry
    registry = get_skill_registry()
    metadata = registry.get_metadata(skill_name, ctx)

    if metadata is None or metadata.source == "remote":
        conn = _get_remote_conn(registry, skill_name, ctx)
        if conn is None:
            raise AppError("SKILL_NOT_FOUND", f"Skill '{skill_name}' not found in registry")
        try:
            return conn.exec_skill_script(skill_name, script_path, args, ctx)
        except Exception as exc:
            raise AppError("REMOTE_SCRIPT_ERROR", str(exc))

    resolved = (metadata.skill_dir / script_path).resolve()

    try:
        resolved.relative_to(metadata.skill_dir.resolve())
    except ValueError:
        raise AppError("INVALID_ARGUMENT", "script_path escapes skill directory")

    if not resolved.is_file():
        raise AppError("SCRIPT_NOT_FOUND", f"Script '{script_path}' not found in skill '{skill_name}'")

    abs_skill_dir = metadata.skill_dir.resolve()
    if resolved.suffix == ".py":
        command = f'python "{resolved}" {args}'.strip()
    else:
        command = f'"{resolved}" {args}'.strip()

    for pattern in BASH_BLACKLIST:
        if re.search(pattern, command):
            raise AppError("TOOL_COMMAND_BLOCKED", f"Command blocked by blacklist: {pattern}")

    settings = get_settings()
    timeout_sec = settings.bash_exec_timeout_ms / 1000
    cwd = _resolve_cwd(ctx) or str(Path.cwd())

    env = build_venv_env(cwd) or os.environ.copy()
    env["SKILL_DIR"] = str(abs_skill_dir)

    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            cwd=cwd,
            env=env,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        limit = settings.bash_exec_output_limit_bytes
        if len(output.encode("utf-8")) > limit:
            output = output.encode("utf-8")[:limit].decode("utf-8", errors="replace")
            output += f"\n[output truncated at {limit} bytes]"
        is_error = proc.returncode != 0
        return ToolResult(
            content=output,
            is_error=is_error,
            error_code="SCRIPT_NONZERO_EXIT" if is_error else None,
            metadata={"exit_code": proc.returncode, "skill": skill_name, "script": script_path},
        )
    except subprocess.TimeoutExpired:
        raise AppError("TOOL_TIMEOUT", f"exec_skill_script timed out after {timeout_sec}s")
    except Exception as exc:
        return ToolResult(
            content=f"Failed to launch script: {exc}",
            is_error=True,
            error_code="SCRIPT_LAUNCH_ERROR",
            metadata={"skill": skill_name, "script": script_path},
        )


# ── Provider 入口 ─────────────────────────────────────────────────────────────

def get_skill_executor_tools() -> list[ToolDefinition]:
    """返回所有 skill 执行工具定义。"""
    return list(_SKILL_EXECUTOR_TOOLS)
