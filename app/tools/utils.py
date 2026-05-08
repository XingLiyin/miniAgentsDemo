"""工具公共工具函数。"""

from __future__ import annotations

import inspect
import types as _types
import typing

from app.llm.types import InputSchema


def _py_type_to_json_schema(py_type) -> dict:
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
    return {"type": "string"}


def extract_input_schema(fn, *, exclude: set[str] | None = None) -> InputSchema:
    """从函数签名的 Annotated 类型注解中提取 InputSchema。"""
    try:
        hints = typing.get_type_hints(fn, include_extras=True)
    except Exception:
        hints = {}

    properties: dict = {}
    required: list[str] = []

    for name, param in inspect.signature(fn).parameters.items():
        if exclude and name in exclude:
            continue
        ann = hints.get(name, inspect.Parameter.empty)
        description = ""
        py_type = ann if ann is not inspect.Parameter.empty else str

        if typing.get_origin(ann) is typing.Annotated:
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
