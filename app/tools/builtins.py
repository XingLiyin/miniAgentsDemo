"""内置工具实现：bash_exec、http_request、search_tools、read、write、glob、
load_skill_reference、exec_skill_script。

新增内置工具步骤：
1. 用 @tool_result 装饰函数，参数用 Annotated[type, "描述"] 声明
2. 函数返回 ToolResult（或 str，自动包装）
3. 在 get_builtin_provider() 的列表中追加
"""

from __future__ import annotations

import glob as _glob
import logging
import os

if os.name == "nt":
    _gtk_bin = r"C:\Program Files\GTK3-Runtime Win64\bin"
    if _gtk_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _gtk_bin + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(_gtk_bin)
import re
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any

import httpx

from app.common.errors import AppError
from app.config.settings import get_settings
from app.llm.types import InputSchema
from app.tools.definition import CallContext, ToolDefinition, ToolResult
from app.tools.provider import BuiltinToolProvider

logger = logging.getLogger(__name__)


def _resolve_cwd(ctx: CallContext | None) -> str | None:
    """从 CallContext 中提取工作目录，空或无则返回 None（沿用进程 cwd）。"""
    wd = ctx.working_dir if ctx else ""
    return wd or None


def _resolve_path(path: str, ctx: CallContext | None) -> Path:
    """将相对路径解析到 working_dir；绝对路径直接返回。"""
    p = Path(path)
    if p.is_absolute():
        return p
    cwd = _resolve_cwd(ctx)
    return (Path(cwd) / p) if cwd else p


# ── bash_exec ─────────────────────────────────────────────────────────────

def _build_venv_env(cwd: str | None) -> dict[str, str] | None:
    """Return a copy of os.environ with .venv activated, creating it first if absent."""
    if not cwd:
        return None
    venv_dir = Path(cwd) / ".venv"
    if sys.platform == "win32":
        python_exe = venv_dir / "Scripts" / "python.exe"
        scripts_dir = str(venv_dir / "Scripts")
    else:
        python_exe = venv_dir / "bin" / "python"
        scripts_dir = str(venv_dir / "bin")

    if not python_exe.exists():
        logger.info("bash_exec: creating venv at '%s'", venv_dir)
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        if result.returncode != 0:
            raise AppError(
                "VENV_CREATE_FAILED",
                f"Failed to create venv: {((result.stdout or '') + (result.stderr or '')).strip()}",
            )

    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(venv_dir)
    env["PATH"] = scripts_dir + os.pathsep + env.get("PATH", "")
    env.pop("PYTHONHOME", None)
    return env


_BASH_BLACKLIST = [
    r"\brm\s+-rf\b",
    r"\bmkfs\b",
    r"\bdd\b.*\bof=/dev/",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bsudo\b",
    r"\bsu\b\s",
    r"\bchmod\s+777\b",
    r"\bcurl\b.*\|\s*bash",
    r"\bwget\b.*\|\s*bash",
]


def _make_bash_exec() -> ToolDefinition:
    def handler(arguments: dict[str, Any], ctx: CallContext | None = None) -> ToolResult:
        command: str = arguments.get("command", "")
        for pattern in _BASH_BLACKLIST:
            if re.search(pattern, command):
                raise AppError("TOOL_COMMAND_BLOCKED", f"Command blocked by blacklist: {pattern}")

        settings = get_settings()
        timeout_sec = settings.bash_exec_timeout_ms / 1000
        cwd = _resolve_cwd(ctx)
        env = _build_venv_env(cwd)

        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=timeout_sec, cwd=cwd, env=env,
            )
            output = proc.stdout + proc.stderr
            limit = settings.bash_exec_output_limit_bytes
            if len(output.encode("utf-8")) > limit:
                output = output.encode("utf-8")[:limit].decode("utf-8", errors="replace")
                output += f"\n[output truncated at {limit} bytes]"
            is_error = proc.returncode != 0
            return ToolResult(
                content=output,
                is_error=is_error,
                error_code="BASH_NONZERO_EXIT" if is_error else None,
                metadata={"exit_code": proc.returncode, "cwd": cwd or ""},
            )
        except subprocess.TimeoutExpired:
            raise AppError("TOOL_TIMEOUT", f"bash_exec timed out after {timeout_sec}s")

    return ToolDefinition(
        name="bash_exec",
        description="Execute a shell command in a restricted environment. Returns stdout+stderr. Non-zero exit code sets is_error=true.",
        input_schema=InputSchema(
            properties={"command": {"type": "string", "description": "Shell command to execute"}},
            require=["command"],
        ),
        handler=handler,
    )




# ── read ──────────────────────────────────────────────────────────────────

def _make_read() -> ToolDefinition:
    def handler(arguments: dict[str, Any], ctx: CallContext | None = None) -> ToolResult:
        path: str = arguments.get("path", "")
        encoding: str = arguments.get("encoding", "utf-8")
        start_line: int | None = arguments.get("start_line")
        end_line: int | None = arguments.get("end_line")

        settings = get_settings()
        limit = settings.http_response_limit_bytes

        file_path = _resolve_path(path, ctx)
        if not file_path.exists():
            raise AppError("FILE_NOT_FOUND", f"File not found: {path}")
        if not file_path.is_file():
            raise AppError("NOT_A_FILE", f"Path is not a file: {path}")

        size = file_path.stat().st_size
        if size > limit:
            raise AppError("FILE_TOO_LARGE", f"File size {size} bytes exceeds limit {limit} bytes")

        try:
            content = file_path.read_text(encoding=encoding)
        except UnicodeDecodeError as e:
            raise AppError("DECODE_ERROR", f"Failed to decode file with encoding '{encoding}': {e}")

        if start_line is not None or end_line is not None:
            lines = content.splitlines(keepends=True)
            total = len(lines)
            s = max(1, start_line or 1)
            e = min(total, end_line or total)
            if s > total:
                raise AppError("INVALID_ARGUMENT", f"start_line {s} exceeds file length {total}")
            selected = lines[s - 1:e]
            content = "".join(f"{s + i}: {line}" for i, line in enumerate(selected))
            return ToolResult(
                content=content,
                metadata={"path": str(file_path.resolve()), "start_line": s, "end_line": s + len(selected) - 1, "total_lines": total},
            )

        return ToolResult(content=content, metadata={"path": str(file_path.resolve()), "size": size})

    return ToolDefinition(
        name="read",
        description=(
            "Read a file from the filesystem and return its content as text. "
            "Optionally specify start_line and/or end_line (1-based, inclusive) to read a slice; "
            "the returned lines are prefixed with line numbers (e.g. '42: content')."
        ),
        input_schema=InputSchema(
            properties={
                "path": {"type": "string", "description": "Absolute or relative path to the file to read"},
                "encoding": {"type": "string", "description": "File encoding (default: utf-8)"},
                "start_line": {"type": "integer", "description": "First line to read, 1-based inclusive (default: 1)"},
                "end_line": {"type": "integer", "description": "Last line to read, 1-based inclusive (default: last line)"},
            },
            require=["path"],
        ),
        handler=handler,
    )


# ── write ─────────────────────────────────────────────────────────────────

def _make_write() -> ToolDefinition:
    def handler(arguments: dict[str, Any], ctx: CallContext | None = None) -> ToolResult:
        path: str = arguments.get("path", "")
        content: str = arguments.get("content", "")
        encoding: str = arguments.get("encoding", "utf-8")
        overwrite: bool = arguments.get("overwrite", True)

        if not path or not Path(path).name:
            raise AppError("INVALID_ARGUMENT", "write: path must include a filename, not just a directory")

        file_path = _resolve_path(path, ctx)

        if file_path.exists() and not overwrite:
            raise AppError("FILE_EXISTS", f"File already exists and overwrite=false: {path}")

        file_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            file_path.write_text(content, encoding=encoding)
        except OSError as e:
            raise AppError("WRITE_ERROR", f"Failed to write file: {e}")

        return ToolResult(
            content=f"Write successfully: Written {len(content.encode(encoding))} bytes to {file_path.resolve()}",
            metadata={"path": str(file_path.resolve()), "bytes": len(content.encode(encoding))},
        )

    return ToolDefinition(
        name="write",
        description="Write text content to a file. Creates parent directories if they don't exist.",
        input_schema=InputSchema(
            properties={
                "path": {"type": "string", "description": "Absolute or relative path to the file to write"},
                "content": {"type": "string", "description": "Text content to write to the file"},
                "encoding": {"type": "string", "description": "File encoding (default: utf-8)"},
                "overwrite": {"type": "boolean", "description": "Allow overwriting an existing file (default: true)"},
            },
            require=["path", "content"],
        ),
        handler=handler,
    )


# ── edit ──────────────────────────────────────────────────────────────────

def _make_edit() -> ToolDefinition:
    def handler(arguments: dict[str, Any], ctx: CallContext | None = None) -> ToolResult:
        path: str = arguments.get("path", "")
        old_str: str = arguments.get("old_str", "")
        new_str: str = arguments.get("new_str", "")
        replace_all: bool = arguments.get("replace_all", False)
        encoding: str = arguments.get("encoding", "utf-8")

        if not path:
            raise AppError("INVALID_ARGUMENT", "edit: path is required")
        if not old_str:
            raise AppError("INVALID_ARGUMENT", "edit: old_str is required")

        file_path = _resolve_path(path, ctx)
        if not file_path.exists():
            raise AppError("FILE_NOT_FOUND", f"File not found: {path}")
        if not file_path.is_file():
            raise AppError("NOT_A_FILE", f"Path is not a file: {path}")

        try:
            content = file_path.read_text(encoding=encoding)
        except UnicodeDecodeError as e:
            raise AppError("DECODE_ERROR", f"Failed to decode file with encoding '{encoding}': {e}")

        count = content.count(old_str)
        if count == 0:
            raise AppError("STRING_NOT_FOUND", "edit: old_str not found in file")
        if count > 1 and not replace_all:
            raise AppError(
                "AMBIGUOUS_MATCH",
                f"edit: old_str matches {count} locations; set replace_all=true to replace all, or provide more context to make it unique",
            )

        new_content = content.replace(old_str, new_str) if replace_all else content.replace(old_str, new_str, 1)

        try:
            file_path.write_text(new_content, encoding=encoding)
        except OSError as e:
            raise AppError("WRITE_ERROR", f"Failed to write file: {e}")

        replaced = count if replace_all else 1
        return ToolResult(
            content=f"Replaced {replaced} occurrence(s) in {file_path.resolve()}",
            metadata={"path": str(file_path.resolve()), "replaced": replaced},
        )

    return ToolDefinition(
        name="edit",
        description=(
            "Replace an exact string in a file with new content. "
            "old_str must match exactly (including whitespace/indentation). "
            "Fails if old_str is not found, or if it matches multiple locations and replace_all is false. "
            "Set new_str to an empty string to delete old_str."
        ),
        input_schema=InputSchema(
            properties={
                "path": {"type": "string", "description": "Absolute or relative path to the file to edit"},
                "old_str": {"type": "string", "description": "Exact text to find and replace"},
                "new_str": {"type": "string", "description": "Replacement text (empty string to delete old_str)"},
                "replace_all": {"type": "boolean", "description": "Replace all occurrences instead of just the first (default: false)"},
                "encoding": {"type": "string", "description": "File encoding (default: utf-8)"},
            },
            require=["path", "old_str", "new_str"],
        ),
        handler=handler,
    )


# ── glob ──────────────────────────────────────────────────────────────────

def _make_glob() -> ToolDefinition:
    def handler(arguments: dict[str, Any], ctx: CallContext | None = None) -> ToolResult:
        pattern: str = arguments.get("pattern", "")
        root: str = arguments.get("root", ".")
        limit: int = arguments.get("limit", 200)

        root_path = _resolve_path(root, ctx).resolve()
        if not root_path.is_dir():
            raise AppError("NOT_A_DIRECTORY", f"Root path is not a directory: {root}")

        matches = _glob.glob(pattern, root_dir=str(root_path), recursive=True)
        matches.sort()

        truncated = False
        if len(matches) > limit:
            matches = matches[:limit]
            truncated = True

        output = "\n".join(matches) if matches else "(no matches)"
        if truncated:
            output += f"\n[truncated at {limit} results]"

        return ToolResult(
            content=output,
            metadata={"count": len(matches), "truncated": truncated, "root": str(root_path)},
        )

    return ToolDefinition(
        name="glob",
        description="Find files matching a glob pattern. Returns a newline-separated list of matching paths.",
        input_schema=InputSchema(
            properties={
                "pattern": {"type": "string", "description": "Glob pattern to match files, e.g. 'src/**/*.py'"},
                "root": {"type": "string", "description": "Root directory to search from (default: current working directory)"},
                "limit": {"type": "integer", "description": "Maximum number of results to return (default: 200)"},
            },
            require=["pattern"],
        ),
        handler=handler,
    )


# ── get_skill_files ───────────────────────────────────────────────────────

def _make_get_skill_files() -> ToolDefinition:
    def handler(arguments: dict[str, Any], ctx: CallContext | None = None) -> ToolResult:
        pattern: str = arguments.get("pattern", "**/*")
        limit: int = arguments.get("limit", 200)

        skill_name = ctx.task.settings.get("skill_name") if (ctx and ctx.task and ctx.task.settings) else None
        if not skill_name:
            raise AppError("MISSING_SKILL_NAME", "Task has no skill assigned; cannot list skill files")

        from app.skills.registry import get_skill_registry
        registry = get_skill_registry()
        metadata = registry.get_metadata(skill_name, ctx)

        if metadata is None:
            # 远端：通过 _remote_index 路由到对应 MCP 连接
            conn = registry.get_conn_for_skill(skill_name)
            if conn is None:
                raise AppError("SKILL_NOT_FOUND", f"Skill '{skill_name}' not found in registry")
            try:
                content = conn.get_skill_files(skill_name, pattern, limit, ctx)
            except Exception as e:
                raise AppError("REMOTE_SKILL_ERROR", str(e))
            return ToolResult(content=content, metadata={"skill": skill_name, "source": "remote"})

        # 本地：glob 模式匹配，与 glob 工具行为一致
        skill_dir = metadata.skill_dir
        if not skill_dir.exists():
            raise AppError("SKILL_DIR_NOT_FOUND", f"Skill directory '{skill_dir}' does not exist")

        matches = _glob.glob(pattern, root_dir=str(skill_dir), recursive=True)
        # 仅保留文件（排除目录、隐藏路径、.venv）
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

    return ToolDefinition(
        name="get_skill_files",
        description=(
            "List files available in the current task's skill directory. "
            "For local skills, supports glob patterns (e.g. 'scripts/*.py', 'references/**'). "
            "Returns a newline-separated list of file paths (local) or file names (remote). "
            "For remote skills, also returns file IDs usable with load_skill_reference and exec_skill_script."
        ),
        input_schema=InputSchema(
            properties={
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match files (default: '**/*', local skills only)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (default: 200, local skills only)",
                },
            },
            require=[],
        ),
        handler=handler,
    )


# ── load_skill_reference ──────────────────────────────────────────────────

def _make_load_skill_reference() -> ToolDefinition:
    def handler(arguments: dict[str, Any], ctx: CallContext | None = None) -> ToolResult:
        reference_path: str = arguments.get("reference_path", "")
        if not reference_path:
            raise AppError("INVALID_ARGUMENT", "reference_path is required")

        skill_name = ctx.task.settings.get("skill_name") if (ctx and ctx.task and ctx.task.settings) else None
        if not skill_name:
            raise AppError("MISSING_SKILL_NAME", "Task has no skill assigned; cannot load skill reference")

        from app.skills.registry import get_skill_registry
        registry = get_skill_registry()
        metadata = registry.get_metadata(skill_name, ctx)

        if metadata is None:
            # 远端：通过 _remote_index 路由到对应 MCP 连接
            conn = registry.get_conn_for_skill(skill_name)
            if conn is None:
                raise AppError("SKILL_NOT_FOUND", f"Skill '{skill_name}' not found in registry")
            try:
                content = conn.load_skill_reference(skill_name, reference_path, ctx)
            except Exception as e:
                raise AppError("REMOTE_REFERENCE_ERROR", str(e))
            return ToolResult(content=content, metadata={"skill": skill_name, "path": reference_path})

        # 本地 skill：读取本地文件
        from app.skills.loader import SkillLoader
        try:
            content = SkillLoader().load_resource(metadata.skill_dir, reference_path)
        except ValueError as e:
            raise AppError("INVALID_ARGUMENT", str(e))
        except FileNotFoundError:
            raise AppError("FILE_NOT_FOUND", f"Reference '{reference_path}' not found in skill '{skill_name}'")

        return ToolResult(
            content=content,
            metadata={"skill": skill_name, "path": reference_path},
        )

    return ToolDefinition(
        name="load_skill_reference",
        description=(
            "Read a reference file from the current task's skill directory. "
            "Use the relative path as shown in the skill instructions "
            "(e.g. 'references/background.md', 'checks/self_check.md')."
        ),
        input_schema=InputSchema(
            properties={
                "reference_path": {
                    "type": "string",
                    "description": "Relative path to the reference file within the skill directory",
                }
            },
            require=["reference_path"],
        ),
        handler=handler,
    )


# ── skill venv helpers ────────────────────────────────────────────────────

def _venv_python(working_dir: Path, skill_dir: Path) -> str:
    """Return the Python executable for running a skill script.

    venv lives in working_dir, not skill_dir. Priority:
    1. working_dir/.venv exists → use it as-is (user manages deps)
    2. working_dir/.venv absent + skill_dir/requirements.txt exists → create venv
       at working_dir/.venv and install skill deps
    3. No requirements.txt → sys.executable
    """
    if sys.platform == "win32":
        _python = lambda d: d / "Scripts" / "python.exe"
        _pip    = lambda d: d / "Scripts" / "pip.exe"
    else:
        _python = lambda d: d / "bin" / "python"
        _pip    = lambda d: d / "bin" / "pip"

    venv_dir = working_dir / ".venv"

    # Case 1: venv already exists — use it without touching deps
    if _python(venv_dir).exists():
        return str(_python(venv_dir))

    # Case 2: no venv yet — only create if skill has requirements
    reqs = skill_dir / "requirements.txt"
    if not reqs.exists():
        return sys.executable

    logger.info("exec_skill_script: creating venv at '%s'", venv_dir)
    result = subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if result.returncode != 0:
        raise AppError(
            "VENV_CREATE_FAILED",
            f"Failed to create venv: {((result.stdout or '') + (result.stderr or '')).strip()}",
        )

    logger.info("exec_skill_script: installing requirements from '%s'", reqs)
    result = subprocess.run(
        [str(_pip(venv_dir)), "install", "-r", str(reqs), "--disable-pip-version-check"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    if result.returncode != 0:
        raise AppError(
            "VENV_INSTALL_FAILED",
            f"pip install failed: {((result.stdout or '') + (result.stderr or '')).strip()}",
        )

    return str(_python(venv_dir))


# ── exec_skill_script ─────────────────────────────────────────────────────

def _make_exec_skill_script() -> ToolDefinition:
    def handler(arguments: dict[str, Any], ctx: CallContext | None = None) -> ToolResult:
        script_path: str = arguments.get("script_path", "")
        args: str = arguments.get("args", "")

        if not script_path:
            raise AppError("INVALID_ARGUMENT", "script_path is required")

        skill_name = ctx.task.settings.get("skill_name") if (ctx and ctx.task and ctx.task.settings) else None
        if not skill_name:
            raise AppError("MISSING_SKILL_NAME", "Task has no skill assigned; cannot execute skill script")

        from app.skills.registry import get_skill_registry
        registry = get_skill_registry()
        metadata = registry.get_metadata(skill_name, ctx)

        if metadata is None:
            # 远端：通过 _remote_index 路由到对应 MCP 连接
            conn = registry.get_conn_for_skill(skill_name)
            if conn is None:
                raise AppError("SKILL_NOT_FOUND", f"Skill '{skill_name}' not found in registry")
            try:
                return conn.exec_skill_script(skill_name, script_path, args, ctx)
            except Exception as e:
                raise AppError("REMOTE_SCRIPT_ERROR", str(e))

        # 本地：script_path 为相对于 skill_dir 的路径，如 scripts/extract.py
        resolved = (metadata.skill_dir / script_path).resolve()

        # 路径安全检查：禁止逃出 skill_dir
        try:
            resolved.relative_to(metadata.skill_dir.resolve())
        except ValueError:
            raise AppError("INVALID_ARGUMENT", "script_path escapes skill directory")

        if not resolved.is_file():
            raise AppError("SCRIPT_NOT_FOUND", f"Script '{script_path}' not found in skill '{skill_name}'")

        abs_skill_dir = metadata.skill_dir.resolve()
        if resolved.suffix == ".py":
            wd_str = ctx.working_dir if ctx else ""
            if wd_str:
                python_exe = _venv_python(Path(wd_str), abs_skill_dir)
            else:
                python_exe = sys.executable
            command = f'"{python_exe}" "{resolved}" {args}'.strip()
        else:
            command = f'"{resolved}" {args}'.strip()

        for pattern in _BASH_BLACKLIST:
            if re.search(pattern, command):
                raise AppError("TOOL_COMMAND_BLOCKED", f"Command blocked by blacklist: {pattern}")

        settings = get_settings()
        timeout_sec = settings.bash_exec_timeout_ms / 1000

        cwd = _resolve_cwd(ctx) or str(Path.cwd())
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
        except Exception as e:
            return ToolResult(
                content=f"Failed to launch script: {e}",
                is_error=True,
                error_code="SCRIPT_LAUNCH_ERROR",
                metadata={"skill": skill_name, "script": script_path},
            )

    return ToolDefinition(
        name="exec_skill_script",
        description=(
            "Execute a script from the current task's skill directory. "
            "The working directory is set to the skill root. "
            "Construct args as described in the skill instructions."
        ),
        input_schema=InputSchema(
            properties={
                "script_path": {
                    "type": "string",
                    "description": "Relative path to the script within the skill directory (e.g. 'scripts/extract.py')",
                },
                "args": {
                    "type": "string",
                    "description": "Command-line argument string appended after the script path",
                },
            },
            require=["script_path"],
        ),
        handler=handler,
    )


# ── read_image ────────────────────────────────────────────────────────────

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".gif": "image/gif",
    ".webp": "image/webp", ".bmp": "image/bmp",
}
_IMAGE_MAX_BYTES = 5 * 1024 * 1024  # 5 MB


def _make_read_image() -> ToolDefinition:
    def handler(arguments: dict[str, Any], ctx: CallContext | None = None) -> ToolResult:
        import base64

        path: str = arguments.get("path", "")
        file_path = _resolve_path(path, ctx)

        if not file_path.exists():
            raise AppError("FILE_NOT_FOUND", f"File not found: {path}")
        if not file_path.is_file():
            raise AppError("NOT_A_FILE", f"Path is not a file: {path}")

        ext = file_path.suffix.lower()
        if ext not in _IMAGE_EXTENSIONS:
            raise AppError(
                "UNSUPPORTED_FORMAT",
                f"Unsupported image format '{ext}'. Supported: {', '.join(sorted(_IMAGE_EXTENSIONS))}",
            )

        size = file_path.stat().st_size
        if size > _IMAGE_MAX_BYTES:
            raise AppError(
                "FILE_TOO_LARGE",
                f"Image size {size:,} bytes exceeds limit {_IMAGE_MAX_BYTES:,} bytes (5 MB)",
            )

        media_type = _IMAGE_MEDIA_TYPES[ext]
        data = base64.b64encode(file_path.read_bytes()).decode()

        content = [
            {"type": "image", "data": data, "media_type": media_type, "source_type": "base64"},
            {"type": "text", "text": f"[Image read: {file_path.name}, {size:,} bytes, {media_type}]"},
        ]
        return ToolResult(
            content=content,
            metadata={"path": str(file_path.resolve()), "size": size, "media_type": media_type},
        )

    return ToolDefinition(
        name="read_image",
        description="Read an image file from the workspace and return it as multimodal content. "
                    "Supported formats: jpg, jpeg, png, gif, webp, bmp. Max size: 5 MB.",
        input_schema=InputSchema(
            properties={
                "path": {"type": "string", "description": "Absolute or relative path to the image file"},
            },
            require=["path"],
        ),
        handler=handler,
    )


# ── save_image_code ───────────────────────────────────────────────────────

# 当前支持的格式 → 文件扩展名映射；未来在此追加 "html": ".html", "react": ".tsx"
_IMAGE_CODE_FORMATS: dict[str, str] = {
    "svg": ".svg",
}


def _make_save_image_code() -> ToolDefinition:
    def handler(arguments: dict[str, Any], ctx: CallContext | None = None) -> ToolResult:
        import datetime

        code: str = arguments.get("code", "")
        fmt: str = arguments.get("format", "svg").lower()
        filename: str = arguments.get("filename", "")
        output_dir: str = arguments.get("output_dir", "images")

        if not code.strip():
            raise AppError("INVALID_ARGUMENT", "save_image_code: code must not be empty")

        ext = _IMAGE_CODE_FORMATS.get(fmt)
        if ext is None:
            supported = ", ".join(sorted(_IMAGE_CODE_FORMATS))
            raise AppError("UNSUPPORTED_FORMAT", f"Unsupported format '{fmt}'. Supported: {supported}")

        # 格式级校验（各格式扩展此处）
        if fmt == "svg" and "<svg" not in code:
            raise AppError("INVALID_ARGUMENT", "save_image_code: SVG code must contain an <svg> element")

        safe_name = re.sub(r"[^\w\-]", "_", filename) if filename else (
            "image_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        )

        out_path = _resolve_path(output_dir, ctx) / f"{safe_name}{ext}"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            out_path.write_text(code, encoding="utf-8")
        except OSError as e:
            raise AppError("WRITE_ERROR", f"Failed to write image code: {e}")

        size = out_path.stat().st_size
        return ToolResult(
            content=f"Saved {fmt.upper()} → {out_path.resolve()} ({size:,} bytes)",
            metadata={
                "path": str(out_path.resolve()),
                "format": fmt,
                "size": size,
                "filename": f"{safe_name}{ext}",
            },
        )

    return ToolDefinition(
        name="save_image_code",
        description=(
            "Save generated image source code to a file. "
            "Supported formats: svg. Extensible to html and react in future. "
            "Output goes to 'images/' under the working directory by default."
        ),
        input_schema=InputSchema(
            properties={
                "code": {
                    "type": "string",
                    "description": "Complete source code of the image (e.g. full SVG markup)",
                },
                "format": {
                    "type": "string",
                    "enum": ["svg"],
                    "description": "Image code format: 'svg'. Future: 'html', 'react'",
                },
                "filename": {
                    "type": "string",
                    "description": "Output filename without extension. Defaults to 'image_<timestamp>'",
                },
                "output_dir": {
                    "type": "string",
                    "description": "Output directory relative to working dir (default: 'images')",
                },
            },
            require=["code", "format"],
        ),
        handler=handler,
    )


# ── render_svg ────────────────────────────────────────────────────────────

def _make_render_svg() -> ToolDefinition:
    def handler(arguments: dict[str, Any], ctx: CallContext | None = None) -> ToolResult:
        import base64

        filename: str = arguments.get("filename", "")
        code: str = arguments.get("code", "")
        scale: float = float(arguments.get("scale", 2.0))

        if not filename and not code:
            raise AppError("INVALID_ARGUMENT", "render_svg: provide either 'filename' or 'code'")

        if filename:
            svg_path = _resolve_path(filename, ctx)
            if not svg_path.exists():
                raise AppError("FILE_NOT_FOUND", f"SVG file not found: {filename}")
            svg_bytes = svg_path.read_bytes()
            label = svg_path.name
        else:
            if "<svg" not in code:
                raise AppError("INVALID_ARGUMENT", "render_svg: 'code' must contain an <svg> element")
            svg_bytes = code.encode("utf-8")
            label = "inline"

        try:
            import cairosvg
        except ImportError:
            return ToolResult(
                content=(
                    "cairosvg is not installed. Run: pip install cairosvg\n"
                    "SVG file is saved and can be opened directly in a browser."
                ),
                is_error=True,
                error_code="DEPENDENCY_MISSING",
            )

        try:
            png_bytes: bytes = cairosvg.svg2png(bytestring=svg_bytes, scale=scale)
        except Exception as e:
            raise AppError("RENDER_ERROR", f"Failed to render SVG: {e}")

        data = base64.b64encode(png_bytes).decode()
        content = [
            {"type": "image", "data": data, "media_type": "image/png", "source_type": "base64"},
            {"type": "text", "text": f"[Rendered SVG → PNG: {label}, scale={scale}x, {len(png_bytes):,} bytes]"},
        ]
        return ToolResult(
            content=content,
            metadata={"source": label, "scale": scale, "png_size": len(png_bytes)},
        )

    return ToolDefinition(
        name="render_svg",
        description=(
            "Render an SVG to PNG and return the image inline for visual inspection. "
            "Provide either 'filename' (path to a saved .svg file) or 'code' (inline SVG string). "
            "Requires cairosvg: pip install cairosvg."
        ),
        input_schema=InputSchema(
            properties={
                "filename": {
                    "type": "string",
                    "description": "Path to the saved .svg file (relative to working dir)",
                },
                "code": {
                    "type": "string",
                    "description": "Inline SVG source code to render directly",
                },
                "scale": {
                    "type": "number",
                    "description": "Render scale factor (default: 2.0 for 2x resolution)",
                },
            },
            require=[],
        ),
        handler=handler,
    )


# # ── write_image ───────────────────────────────────────────────────────────

# def _make_write_image() -> ToolDefinition:
#     def handler(arguments: dict[str, Any], ctx: CallContext | None = None) -> ToolResult:
#         import base64

#         path: str = arguments.get("path", "")
#         data: str = arguments.get("data", "")
#         overwrite: bool = arguments.get("overwrite", True)

#         if not path:
#             raise AppError("INVALID_ARGUMENT", "write_image: path is required")
#         if not data:
#             raise AppError("INVALID_ARGUMENT", "write_image: data is required")

#         file_path = _resolve_path(path, ctx)

#         if file_path.suffix.lower() not in _IMAGE_EXTENSIONS:
#             raise AppError(
#                 "UNSUPPORTED_FORMAT",
#                 f"Unsupported image format '{file_path.suffix}'. Supported: {', '.join(sorted(_IMAGE_EXTENSIONS))}",
#             )
#         if not overwrite and file_path.exists():
#             raise AppError("FILE_EXISTS", f"File already exists: {path}. Set overwrite=true to replace it.")

#         try:
#             raw = base64.b64decode(data)
#         except Exception as e:
#             raise AppError("INVALID_ARGUMENT", f"write_image: invalid base64 data: {e}")

#         if len(raw) > _IMAGE_MAX_BYTES:
#             raise AppError(
#                 "FILE_TOO_LARGE",
#                 f"Image size {len(raw):,} bytes exceeds limit {_IMAGE_MAX_BYTES:,} bytes (5 MB)",
#             )

#         file_path.parent.mkdir(parents=True, exist_ok=True)
#         file_path.write_bytes(raw)

#         return ToolResult(
#             content=f"Image saved to {file_path.resolve()} ({len(raw):,} bytes)",
#             metadata={"path": str(file_path.resolve()), "size": len(raw)},
#         )

#     return ToolDefinition(
#         name="write_image",
#         description="Save a base64-encoded image to a file in the workspace. "
#                     "Supported formats: jpg, jpeg, png, gif, webp, bmp. Max size: 5 MB.",
#         input_schema=InputSchema(
#             properties={
#                 "path":      {"type": "string",  "description": "Absolute or relative path to save the image"},
#                 "data":      {"type": "string",  "description": "Base64-encoded image data"},
#                 "overwrite": {"type": "boolean", "description": "Overwrite if file exists (default: true)", "default": True},
#             },
#             require=["path", "data"],
#         ),
#         handler=handler,
#     )


# ── Provider 入口 ─────────────────────────────────────────────────────────

def get_builtin_provider() -> BuiltinToolProvider:
    """返回包含所有内置工具的 Provider 实例。"""
    return BuiltinToolProvider([
        _make_bash_exec(),
        _make_read(),
        _make_write(),
        _make_edit(),
        _make_glob(),
        _make_get_skill_files(),
        _make_load_skill_reference(),
        _make_exec_skill_script(),
        _make_read_image(),
        _make_save_image_code(),
        _make_render_svg(),
    ])
