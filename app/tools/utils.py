"""工具公共工具函数。"""

from __future__ import annotations

import inspect
import logging
import os
import subprocess
import sys
import types as _types
import typing
from pathlib import Path
from typing import TYPE_CHECKING

from app.common.errors import AppError
from app.llm.types import InputSchema

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.tools.types import CallContext


def _resolve_cwd(ctx: "CallContext | None") -> str | None:
    wd = ctx.working_dir if ctx else ""
    return wd or None


def _resolve_path(path: str, ctx: "CallContext | None") -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    cwd = _resolve_cwd(ctx)
    return (Path(cwd) / p) if cwd else p


BASH_BLACKLIST = [
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


def build_venv_env(cwd: str | None) -> dict[str, str] | None:
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
        logger.info("build_venv_env: creating venv at '%s'", venv_dir)
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


def _py_type_to_json_schema(py_type) -> dict:
    if hasattr(py_type, "__metadata__"):
        args = typing.get_args(py_type)
        schema = _py_type_to_json_schema(args[0])
        if len(args) > 1 and isinstance(args[1], str):
            schema["description"] = args[1]
        return schema

    origin = typing.get_origin(py_type)

    # Union[X, None] or X | None
    _is_union = origin is typing.Union or (
        hasattr(_types, "UnionType") and isinstance(py_type, _types.UnionType)
    )
    if _is_union:
        non_none = [a for a in typing.get_args(py_type) if a is not type(None)]
        if len(non_none) == 1:
            return _py_type_to_json_schema(non_none[0])
        return {"type": "string"}

    # Literal["a", "b"] or Literal[1, 2]
    if origin is typing.Literal:
        args = typing.get_args(py_type)
        if args and isinstance(args[0], str):
            return {"type": "string", "enum": list(args)}
        if args and isinstance(args[0], int) and not isinstance(args[0], bool):
            return {"type": "integer", "enum": list(args)}
        return {"type": "string", "enum": [str(a) for a in args]}

    if py_type is bool:
        return {"type": "boolean"}
    if py_type is int:
        return {"type": "integer"}
    if py_type is float:
        return {"type": "number"}
    if py_type is str:
        return {"type": "string"}
    if py_type is list or origin is list:
        list_args = typing.get_args(py_type)
        if list_args:
            return {"type": "array", "items": _py_type_to_json_schema(list_args[0])}
        return {"type": "array"}
    if py_type is dict or origin is dict:
        return {"type": "object"}
    if hasattr(py_type, "model_json_schema"):
        return py_type.model_json_schema()
    if (isinstance(py_type, type) and issubclass(py_type, dict)
            and hasattr(py_type, "__required_keys__") and hasattr(py_type, "__optional_keys__")):
        try:
            field_hints = typing.get_type_hints(py_type, include_extras=True)
        except Exception:
            field_hints = getattr(py_type, "__annotations__", {})
        required = list(py_type.__required_keys__)
        props = {k: _py_type_to_json_schema(v) for k, v in field_hints.items()}
        schema: dict = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        return schema
    return {"type": "string"}


def _resolve_hints(fn) -> dict:
    """Resolve annotations individually so TYPE_CHECKING-only names don't block everything."""
    try:
        return typing.get_type_hints(fn, include_extras=True)
    except Exception:
        pass
    globs = getattr(fn, "__globals__", {})
    hints: dict = {}
    for name, raw in getattr(fn, "__annotations__", {}).items():
        ann = raw if not isinstance(raw, str) else None
        if isinstance(raw, str):
            try:
                ann = eval(raw, globs)  # noqa: S307
            except Exception:
                pass
        if ann is not None:
            hints[name] = ann
    return hints


def extract_input_schema(fn, *, exclude: set[str] | None = None) -> InputSchema:
    """从函数签名的 Annotated 类型注解中提取 InputSchema。"""
    hints = _resolve_hints(fn)

    properties: dict = {}
    required: list[str] = []

    for name, param in inspect.signature(fn).parameters.items():
        if exclude and name in exclude:
            continue
        ann = hints.get(name, inspect.Parameter.empty)
        description = ""
        py_type = ann if ann is not inspect.Parameter.empty else str

        if hasattr(ann, "__metadata__"):  # Annotated types have __metadata__; get_origin behavior differs across Python versions
            args = typing.get_args(ann)
            py_type = args[0]
            if len(args) > 1 and isinstance(args[1], str):
                description = args[1]

        prop = _py_type_to_json_schema(py_type)
        if description:
            prop["description"] = description
        properties[name] = prop

        if param.default is inspect.Parameter.empty:
            required.append(name)

    return InputSchema(type="object", properties=properties, require=required)


def make_tool_handler(fn):
    """将具名参数函数包装为 (arguments: dict, ctx) -> ToolResult handler。

    跳过 ctx 参数，将 arguments 字典中的值按名字注入，缺省值取函数签名默认值。
    """
    sig = inspect.signature(fn)

    def handler(arguments: dict, ctx=None):
        kwargs: dict = {}
        for name, param in sig.parameters.items():
            if name == "ctx":
                continue
            if name in arguments:
                kwargs[name] = arguments[name]
            elif param.default is not inspect.Parameter.empty:
                kwargs[name] = param.default
        return fn(**kwargs, ctx=ctx)

    return handler
