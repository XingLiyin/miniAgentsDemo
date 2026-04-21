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
from app.tools.tool_decorator import tool_result

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

        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=timeout_sec, cwd=cwd,
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


# ── http_request ──────────────────────────────────────────────────────────

_SSRF_BLOCKED = re.compile(
    r"^(localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|"
    r"192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)$",
    re.IGNORECASE,
)


@tool_result
def http_request(
    url: Annotated[str, "Target URL (must be a public address)"],
    method: Annotated[str, "HTTP method: GET, POST, PUT, DELETE, PATCH"] = "GET",
    headers: Annotated[dict | None, "Optional HTTP headers as key-value pairs"] = None,
    body: Annotated[str | None, "Optional request body (string)"] = None,
) -> ToolResult:
    """Make an HTTP request to an external URL. Private/localhost addresses are blocked."""
    try:
        host = url.split("/")[2].split(":")[0]
    except IndexError:
        raise AppError("INVALID_ARGUMENT", "http_request: invalid URL format")

    if _SSRF_BLOCKED.match(host):
        raise AppError("SSRF_BLOCKED", f"Access to host '{host}' is not allowed")

    settings = get_settings()
    timeout_sec = settings.http_request_timeout_ms / 1000

    try:
        with httpx.Client(timeout=timeout_sec) as client:
            resp = client.request(
                method=method.upper(),
                url=url,
                headers=headers or {},
                content=body.encode() if body else None,
            )
        content = resp.text
        limit = settings.http_response_limit_bytes
        if len(content.encode("utf-8")) > limit:
            content = content.encode("utf-8")[:limit].decode("utf-8", errors="replace")
            content += f"\n[response truncated at {limit} bytes]"
        is_error = resp.status_code >= 400
        return ToolResult(
            content=content,
            is_error=is_error,
            error_code=f"HTTP_{resp.status_code}" if is_error else None,
            metadata={"status_code": resp.status_code},
        )
    except httpx.TimeoutException:
        raise AppError("TOOL_TIMEOUT", f"http_request timed out after {timeout_sec}s")
    except httpx.RequestError as e:
        raise AppError("HTTP_REQUEST_ERROR", str(e))


# ── search_tools ──────────────────────────────────────────────────────────

@tool_result
def search_tools(
    query: Annotated[str, "Natural language description of what you want to accomplish"],
    top_k: Annotated[int, "Maximum number of tools to return (default 5)"] = 5,
) -> ToolResult:
    """Search registered tools by semantic relevance using the external tool store.
    Returns a JSON list of matching tools with name, description, and relevance score.
    Requires MINIAGENTS_TOOL_STORE_BASE_URL to be configured."""
    from app.tools.tool_store_client import get_tool_store_client

    client = get_tool_store_client()
    if not client.enabled:
        return ToolResult(
            content="Tool store is not configured. Set MINIAGENTS_TOOL_STORE_BASE_URL to enable semantic search.",
            is_error=True,
            error_code="TOOL_STORE_NOT_CONFIGURED",
        )

    results = client.search(query, top_k=top_k)
    if not results:
        return ToolResult(content="[]")

    import json
    output = json.dumps(
        [{"name": r.name, "description": r.description, "score": round(r.score, 4)} for r in results],
        ensure_ascii=False,
        indent=2,
    )
    return ToolResult(content=output)




# ── read ──────────────────────────────────────────────────────────────────

def _make_read() -> ToolDefinition:
    def handler(arguments: dict[str, Any], ctx: CallContext | None = None) -> ToolResult:
        path: str = arguments.get("path", "")
        encoding: str = arguments.get("encoding", "utf-8")

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

        return ToolResult(content=content, metadata={"path": str(file_path.resolve()), "size": size})

    return ToolDefinition(
        name="read",
        description="Read a file from the filesystem and return its content as text.",
        input_schema=InputSchema(
            properties={
                "path": {"type": "string", "description": "Absolute or relative path to the file to read"},
                "encoding": {"type": "string", "description": "File encoding (default: utf-8)"},
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
        from app.skills.loader import SkillLoader

        metadata = get_skill_registry().get_metadata(skill_name)
        if metadata is None:
            raise AppError("SKILL_NOT_FOUND", f"Skill '{skill_name}' not found in registry")

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

def _venv_python(skill_dir: Path) -> str:
    """Return the Python executable to use for a skill script.

    If skill_dir/requirements.txt exists, ensure a .venv is created and
    dependencies are installed, then return its interpreter path.
    Falls back to sys.executable when no requirements.txt is present.
    """
    reqs = skill_dir / "requirements.txt"
    if not reqs.exists():
        return sys.executable

    venv_dir = skill_dir / ".venv"
    # Determine platform-specific interpreter path inside the venv
    if sys.platform == "win32":
        python_bin = venv_dir / "Scripts" / "python.exe"
        pip_bin    = venv_dir / "Scripts" / "pip.exe"
    else:
        python_bin = venv_dir / "bin" / "python"
        pip_bin    = venv_dir / "bin" / "pip"

    if not python_bin.exists():
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

    # Always sync requirements so new packages are picked up
    logger.info("exec_skill_script: installing requirements from '%s'", reqs)
    result = subprocess.run(
        [str(pip_bin), "install", "-r", str(reqs), "--disable-pip-version-check"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    if result.returncode != 0:
        raise AppError(
            "VENV_INSTALL_FAILED",
            f"pip install failed: {((result.stdout or '') + (result.stderr or '')).strip()}",
        )

    return str(python_bin)


# ── exec_skill_script ─────────────────────────────────────────────────────

def _make_exec_skill_script() -> ToolDefinition:
    def handler(arguments: dict[str, Any], ctx: CallContext | None = None) -> ToolResult:
        script_name: str = arguments.get("script_name", "")
        args: str = arguments.get("args", "")

        if not script_name:
            raise AppError("INVALID_ARGUMENT", "script_name is required")

        skill_name = ctx.task.settings.get("skill_name") if (ctx and ctx.task and ctx.task.settings) else None
        if not skill_name:
            raise AppError("MISSING_SKILL_NAME", "Task has no skill assigned; cannot execute skill script")

        from app.skills.registry import get_skill_registry

        metadata = get_skill_registry().get_metadata(skill_name)
        if metadata is None:
            raise AppError("SKILL_NOT_FOUND", f"Skill '{skill_name}' not found in registry")

        scripts_dir = metadata.skill_dir / "scripts"
        script_path: Path | None = None
        for ext in (".py", ".sh", ""):
            candidate = scripts_dir / f"{script_name}{ext}"
            if candidate.is_file():
                script_path = candidate
                break

        if script_path is None:
            raise AppError("SCRIPT_NOT_FOUND", f"Script '{script_name}' not found in skill '{skill_name}'")

        # 路径安全检查：禁止逃出 skill_dir
        try:
            script_path.resolve().relative_to(metadata.skill_dir.resolve())
        except ValueError:
            raise AppError("INVALID_ARGUMENT", f"Script path escapes skill directory")

        abs_script_path = script_path.resolve()
        abs_skill_dir = metadata.skill_dir.resolve()
        if abs_script_path.suffix == ".py":
            python_exe = _venv_python(abs_skill_dir)
            command = f'"{python_exe}" "{abs_script_path}" {args}'.strip()
        else:
            command = f'"{abs_script_path}" {args}'.strip()

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
                metadata={"exit_code": proc.returncode, "skill": skill_name, "script": script_name},
            )
        except subprocess.TimeoutExpired:
            raise AppError("TOOL_TIMEOUT", f"exec_skill_script timed out after {timeout_sec}s")
        except Exception as e:
            return ToolResult(
                content=f"Failed to launch script: {e}",
                is_error=True,
                error_code="SCRIPT_LAUNCH_ERROR",
                metadata={"skill": skill_name, "script": script_name},
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
                "script_name": {
                    "type": "string",
                    "description": "Script filename without extension (e.g. 'extract_olt_config')",
                },
                "args": {
                    "type": "string",
                    "description": "Command-line argument string appended after the script path",
                },
            },
            require=["script_name"],
        ),
        handler=handler,
    )


# ── Provider 入口 ─────────────────────────────────────────────────────────

def get_builtin_provider() -> BuiltinToolProvider:
    """返回包含所有内置工具的 Provider 实例。"""
    return BuiltinToolProvider([
        _make_bash_exec(),
        http_request,
        search_tools,
        _make_read(),
        _make_write(),
        _make_glob(),
        _make_load_skill_reference(),
        _make_exec_skill_script(),
    ])
