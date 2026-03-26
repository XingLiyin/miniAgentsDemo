"""内置工具实现：bash_exec、http_request。

新增内置工具步骤：
1. 实现 handler 函数（接收 dict，返回 ToolResult）
2. 在 _make_*_definition() 中声明 name / description / input_schema
3. 在 get_builtin_provider() 的列表中追加新定义
"""

from __future__ import annotations

import logging
import re
import subprocess
from typing import Any

import httpx

from app.common.errors import AppError
from app.config.settings import get_settings
from app.llm.llm_base import InputSchema
from app.tools.definition import ToolDefinition, ToolResult
from app.tools.provider import BuiltinToolProvider

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


def _bash_exec_handler(arguments: dict[str, Any]) -> ToolResult:
    command: str = arguments.get("command", "")
    if not command:
        raise AppError("INVALID_ARGUMENT", "bash_exec: 'command' is required")

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


def _make_bash_exec_definition() -> ToolDefinition:
    return ToolDefinition(
        name="bash_exec",
        description=(
            "Execute a shell command in a restricted environment. "
            "Returns stdout+stderr. Non-zero exit code sets is_error=true."
        ),
        input_schema=InputSchema(
            type="object",
            properties={
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": "Execution timeout in milliseconds (default 30000)",
                },
            },
            require=["command"],
        ),
        handler=_bash_exec_handler,
    )


# ── http_request ──────────────────────────────────────────────────────────

_SSRF_BLOCKED = re.compile(
    r"^(localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|"
    r"192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)$",
    re.IGNORECASE,
)


def _http_request_handler(arguments: dict[str, Any]) -> ToolResult:
    url: str = arguments.get("url", "")
    method: str = arguments.get("method", "GET").upper()
    headers: dict = arguments.get("headers", {})
    body: str | None = arguments.get("body")

    if not url:
        raise AppError("INVALID_ARGUMENT", "http_request: 'url' is required")

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
                method=method, url=url, headers=headers,
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


def _make_http_request_definition() -> ToolDefinition:
    return ToolDefinition(
        name="http_request",
        description=(
            "Make an HTTP request to an external URL. "
            "Private/localhost addresses are blocked."
        ),
        input_schema=InputSchema(
            type="object",
            properties={
                "url": {
                    "type": "string",
                    "description": "Target URL (must be a public address)",
                },
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                    "description": "HTTP method (default GET)",
                },
                "headers": {
                    "type": "object",
                    "description": "Optional HTTP headers as key-value pairs",
                },
                "body": {
                    "type": "string",
                    "description": "Optional request body (string)",
                },
            },
            require=["url"],
        ),
        handler=_http_request_handler,
    )


# ── Provider 入口 ─────────────────────────────────────────────────────────

def get_builtin_provider() -> BuiltinToolProvider:
    """返回包含所有内置工具的 Provider 实例。"""
    return BuiltinToolProvider([
        _make_bash_exec_definition(),
        _make_http_request_definition(),
    ])
