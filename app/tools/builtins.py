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


@tool_result
def bash_exec(
    command: Annotated[str, "Shell command to execute"],
) -> ToolResult:
    """Execute a shell command in a restricted environment. Returns stdout+stderr. Non-zero exit code sets is_error=true."""
    for pattern in _BASH_BLACKLIST:
        if re.search(pattern, command):
            raise AppError("TOOL_COMMAND_BLOCKED", f"Command blocked by blacklist: {pattern}")

    settings = get_settings()
    timeout_sec = settings.bash_exec_timeout_ms / 1000

    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout_sec
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
            metadata={"exit_code": proc.returncode},
        )
    except subprocess.TimeoutExpired:
        raise AppError("TOOL_TIMEOUT", f"bash_exec timed out after {timeout_sec}s")


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

@tool_result
def read(
    path: Annotated[str, "Absolute or relative path to the file to read"],
    encoding: Annotated[str, "File encoding (default: utf-8)"] = "utf-8",
) -> ToolResult:
    """Read a file from the filesystem and return its content as text."""
    settings = get_settings()
    limit = settings.http_response_limit_bytes  # reuse response limit as file size cap

    file_path = Path(path)
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


# ── write ─────────────────────────────────────────────────────────────────

@tool_result
def write(
    path: Annotated[str, "Absolute or relative path to the file to write"],
    content: Annotated[str, "Text content to write to the file"],
    encoding: Annotated[str, "File encoding (default: utf-8)"] = "utf-8",
    overwrite: Annotated[bool, "Allow overwriting an existing file (default: true)"] = True,
) -> ToolResult:
    """Write text content to a file. Creates parent directories if they don't exist."""
    file_path = Path(path)

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


# ── glob ──────────────────────────────────────────────────────────────────

@tool_result
def glob(
    pattern: Annotated[str, "Glob pattern to match files, e.g. 'src/**/*.py'"],
    root: Annotated[str, "Root directory to search from (default: current working directory)"] = ".",
    limit: Annotated[int, "Maximum number of results to return (default: 200)"] = 200,
) -> ToolResult:
    """Find files matching a glob pattern. Returns a newline-separated list of matching paths."""
    root_path = Path(root).resolve()
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

        if script_path.suffix == ".py":
            command = f"python {script_path} {args}".strip()
        else:
            command = f"{script_path} {args}".strip()

        for pattern in _BASH_BLACKLIST:
            if re.search(pattern, command):
                raise AppError("TOOL_COMMAND_BLOCKED", f"Command blocked by blacklist: {pattern}")

        settings = get_settings()
        timeout_sec = settings.bash_exec_timeout_ms / 1000

        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                cwd=str(metadata.skill_dir),
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
                error_code="SCRIPT_NONZERO_EXIT" if is_error else None,
                metadata={"exit_code": proc.returncode, "skill": skill_name, "script": script_name},
            )
        except subprocess.TimeoutExpired:
            raise AppError("TOOL_TIMEOUT", f"exec_skill_script timed out after {timeout_sec}s")

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
        bash_exec, http_request, search_tools, read, write, glob,
        _make_load_skill_reference(),
        _make_exec_skill_script(),
    ])
