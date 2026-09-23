"""内置工具实现：bash_exec、read、write、edit、glob、read_image、save_image_code、render_svg。

新增内置工具步骤：
1. 用 @builtin_tool 装饰函数，参数用 Annotated[type, "描述"] 声明
2. ctx 参数放在 * 之后（keyword-only），不计入 schema
3. 函数直接返回 ToolResult；装饰器自动生成 handler 并注册到 _BUILTIN_TOOLS
"""

from __future__ import annotations

import glob as _glob
import logging
import platform
import re
import subprocess
from pathlib import Path
from typing import Annotated, Literal

from app.common.errors import AppError
from app.config.settings import get_settings
from app.tools.types import CallContext, ToolDefinition, ToolResult
from app.tools.utils import BASH_BLACKLIST, _resolve_cwd, _resolve_path, build_venv_env, extract_input_schema, make_tool_handler

logger = logging.getLogger(__name__)


# ── @builtin_tool 装饰器 ──────────────────────────────────────────────────────

_BUILTIN_TOOLS: list[ToolDefinition] = []


def builtin_tool(fn):
    """将具名参数函数注册为内置工具。

    自动从 Annotated 注解提取 schema，跳过 ctx 参数。
    """
    tool_def = ToolDefinition(
        name=fn.__name__,
        description=(fn.__doc__ or "").strip(),
        input_schema=extract_input_schema(fn, exclude={"ctx"}),
        handler=make_tool_handler(fn),
    )
    _BUILTIN_TOOLS.append(tool_def)
    return tool_def


# ── bash_exec ─────────────────────────────────────────────────────────────────

def _bash_exec_description() -> str:
    """根据当前操作系统生成 bash_exec 的工具描述。

    命令通过 ``subprocess.run(..., shell=True)`` 执行：Windows 走 cmd.exe，
    其它平台走 /bin/sh。描述会说明当前系统、对应的 shell 语法及可用命令示例，
    并列出被安全黑名单拦截的命令。
    """
    system = platform.system()
    if system == "Windows":
        env_desc = (
            f"The current system is Windows ({platform.platform()}); commands run "
            "through cmd.exe. Use Windows shell syntax and built-ins such as `dir`, "
            "`type`, `copy`, `move`, `del`, `cd`, `echo`, `where`, `findstr`. "
            "Unix-only commands (ls, cat, grep, rm) are unavailable unless an "
            "equivalent tool (e.g. git-bash, busybox) is installed and on PATH."
        )
    else:
        os_name = "macOS" if system == "Darwin" else system
        env_desc = (
            f"The current system is {os_name} ({platform.platform()}); commands run "
            "through /bin/sh. Use POSIX shell syntax and common commands such as "
            "`ls`, `cat`, `grep`, `find`, `cp`, `mv`, `rm`, `cd`, `echo`, `which`."
        )
    return (
        "Execute a shell command in a restricted environment. "
        f"{env_desc} "
        "Returns stdout+stderr; a non-zero exit code sets is_error=true. "
        "Blocked for safety: rm -rf, mkfs, dd of=/dev/*, shutdown, reboot, sudo, "
        "su, chmod 777, curl|bash, wget|bash."
    )


@builtin_tool
def bash_exec(
    command: Annotated[str, "Shell command to execute"],
    *,
    ctx: CallContext | None = None,
) -> ToolResult:
    """Execute a shell command in a restricted environment. Returns stdout+stderr. Non-zero exit code sets is_error=true."""
    for pattern in BASH_BLACKLIST:
        if re.search(pattern, command):
            raise AppError("TOOL_COMMAND_BLOCKED", f"Command blocked by blacklist: {pattern}")

    settings = get_settings()
    timeout_sec = settings.bash_exec_timeout_ms / 1000
    cwd = _resolve_cwd(ctx)
    env = build_venv_env(cwd)

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
    except FileNotFoundError as e:
        raise AppError("BASH_CWD_NOT_FOUND", f"Working directory not found: {cwd}") from e


# @builtin_tool 在装饰时已从 docstring 取了静态描述；这里按当前系统覆盖为动态描述。
bash_exec.description = _bash_exec_description()


# ── read ──────────────────────────────────────────────────────────────────────

@builtin_tool
def read(
    path: Annotated[str, "Absolute or relative path to the file to read"],
    encoding: Annotated[str, "File encoding (default: utf-8)"] = "utf-8",
    start_line: Annotated[int | None, "First line to read, 1-based inclusive (default: 1)"] = None,
    end_line: Annotated[int | None, "Last line to read, 1-based inclusive (default: last line)"] = None,
    *,
    ctx: CallContext | None = None,
) -> ToolResult:
    """Read a file from the filesystem and return its content as text. Optionally specify start_line and/or end_line (1-based, inclusive) to read a slice; the returned lines are prefixed with line numbers (e.g. '42: content')."""
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
    except UnicodeDecodeError as exc:
        raise AppError("DECODE_ERROR", f"Failed to decode file with encoding '{encoding}': {exc}")

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


# ── write ─────────────────────────────────────────────────────────────────────

@builtin_tool
def write(
    path: Annotated[str, "Absolute or relative path to the file to write"],
    content: Annotated[str, "Text content to write to the file"],
    encoding: Annotated[str, "File encoding (default: utf-8)"] = "utf-8",
    overwrite: Annotated[bool, "Allow overwriting an existing file (default: true)"] = True,
    *,
    ctx: CallContext | None = None,
) -> ToolResult:
    """Write text content to a file. Creates parent directories if they don't exist."""
    if not path or not Path(path).name:
        raise AppError("INVALID_ARGUMENT", "write: path must include a filename, not just a directory")

    file_path = _resolve_path(path, ctx)

    if file_path.exists() and not overwrite:
        raise AppError("FILE_EXISTS", f"File already exists and overwrite=false: {path}")

    file_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        file_path.write_text(content, encoding=encoding)
    except OSError as exc:
        raise AppError("WRITE_ERROR", f"Failed to write file: {exc}")

    return ToolResult(
        content=f"Write successfully: Written {len(content.encode(encoding))} bytes to {file_path.resolve()}",
        metadata={"path": str(file_path.resolve()), "bytes": len(content.encode(encoding))},
    )


# ── edit ──────────────────────────────────────────────────────────────────────

@builtin_tool
def edit(
    path: Annotated[str, "Absolute or relative path to the file to edit"],
    old_str: Annotated[str, "Exact text to find and replace"],
    new_str: Annotated[str, "Replacement text (empty string to delete old_str)"],
    replace_all: Annotated[bool, "Replace all occurrences instead of just the first (default: false)"] = False,
    encoding: Annotated[str, "File encoding (default: utf-8)"] = "utf-8",
    *,
    ctx: CallContext | None = None,
) -> ToolResult:
    """Replace an exact string in a file with new content. old_str must match exactly (including whitespace/indentation). Fails if old_str is not found, or if it matches multiple locations and replace_all is false. Set new_str to an empty string to delete old_str."""
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
    except UnicodeDecodeError as exc:
        raise AppError("DECODE_ERROR", f"Failed to decode file with encoding '{encoding}': {exc}")

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
    except OSError as exc:
        raise AppError("WRITE_ERROR", f"Failed to write file: {exc}")

    replaced = count if replace_all else 1
    return ToolResult(
        content=f"Replaced {replaced} occurrence(s) in {file_path.resolve()}",
        metadata={"path": str(file_path.resolve()), "replaced": replaced},
    )


# ── glob ──────────────────────────────────────────────────────────────────────

@builtin_tool
def glob(
    pattern: Annotated[str, "Glob pattern to match files, e.g. 'src/**/*.py'"],
    root: Annotated[str, "Root directory to search from (default: current working directory)"] = ".",
    limit: Annotated[int, "Maximum number of results to return (default: 200)"] = 200,
    *,
    ctx: CallContext | None = None,
) -> ToolResult:
    """Find files matching a glob pattern. Returns a newline-separated list of matching paths."""
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


# ── read_image ────────────────────────────────────────────────────────────────

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".gif": "image/gif",
    ".webp": "image/webp", ".bmp": "image/bmp",
}
_IMAGE_MAX_BYTES = 5 * 1024 * 1024  # 5 MB


@builtin_tool
def read_image(
    path: Annotated[str, "Absolute or relative path to the image file"],
    *,
    ctx: CallContext | None = None,
) -> ToolResult:
    """Read an image file from the workspace and return it as multimodal content. Supported formats: jpg, jpeg, png, gif, webp, bmp. Max size: 5 MB."""
    import base64

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


# ── save_image_code ───────────────────────────────────────────────────────────

_IMAGE_CODE_FORMATS: dict[str, str] = {
    "svg": ".svg",
}


@builtin_tool
def save_image_code(
    code: Annotated[str, "Complete source code of the image (e.g. full SVG markup)"],
    format: Annotated[Literal["svg"], "Image code format: 'svg'. Future: 'html', 'react'"],
    filename: Annotated[str, "Output filename without extension. Defaults to 'image_<timestamp>'"] = "",
    output_dir: Annotated[str, "Output directory relative to working dir (default: 'images')"] = "images",
    *,
    ctx: CallContext | None = None,
) -> ToolResult:
    """Save generated image source code to a file. Supported formats: svg. Extensible to html and react in future. Output goes to 'images/' under the working directory by default."""
    import datetime

    if not code.strip():
        raise AppError("INVALID_ARGUMENT", "save_image_code: code must not be empty")

    ext = _IMAGE_CODE_FORMATS.get(format)
    if ext is None:
        supported = ", ".join(sorted(_IMAGE_CODE_FORMATS))
        raise AppError("UNSUPPORTED_FORMAT", f"Unsupported format '{format}'. Supported: {supported}")

    if format == "svg" and "<svg" not in code:
        raise AppError("INVALID_ARGUMENT", "save_image_code: SVG code must contain an <svg> element")

    safe_name = re.sub(r"[^\w\-]", "_", filename) if filename else (
        "image_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    )

    out_path = _resolve_path(output_dir, ctx) / f"{safe_name}{ext}"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        out_path.write_text(code, encoding="utf-8")
    except OSError as exc:
        raise AppError("WRITE_ERROR", f"Failed to write image code: {exc}")

    size = out_path.stat().st_size
    return ToolResult(
        content=f"Saved {format.upper()} → {out_path.resolve()} ({size:,} bytes)",
        metadata={
            "path": str(out_path.resolve()),
            "format": format,
            "size": size,
            "filename": f"{safe_name}{ext}",
        },
    )


# ── render_svg ────────────────────────────────────────────────────────────────

@builtin_tool
def render_svg(
    filename: Annotated[str, "Path to the saved .svg file (relative to working dir)"] = "",
    code: Annotated[str, "Inline SVG source code to render directly"] = "",
    scale: Annotated[float, "Render scale factor (default: 2.0 for 2x resolution)"] = 2.0,
    *,
    ctx: CallContext | None = None,
) -> ToolResult:
    """Render an SVG to PNG and return the image inline for visual inspection. Provide either 'filename' (path to a saved .svg file) or 'code' (inline SVG string). Requires cairosvg: pip install cairosvg."""
    import base64

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
    except Exception as exc:
        raise AppError("RENDER_ERROR", f"Failed to render SVG: {exc}")

    data = base64.b64encode(png_bytes).decode()
    content = [
        {"type": "image", "data": data, "media_type": "image/png", "source_type": "base64"},
        {"type": "text", "text": f"[Rendered SVG → PNG: {label}, scale={scale}x, {len(png_bytes):,} bytes]"},
    ]
    return ToolResult(
        content=content,
        metadata={"source": label, "scale": scale, "png_size": len(png_bytes)},
    )


# ── Provider 入口 ─────────────────────────────────────────────────────────────

def get_builtin_tools() -> list[ToolDefinition]:
    """返回所有内置工具定义。"""
    return list(_BUILTIN_TOOLS)
