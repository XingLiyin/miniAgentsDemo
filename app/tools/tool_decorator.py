"""@tool 装饰器：使用 agent-framework FunctionTool 自动生成 ToolDefinition。

用法：
    from app.tools.tool_decorator import tool_result

    @tool_result
    def bash_exec(command: Annotated[str, "Shell command to execute"]) -> ToolResult:
        \"\"\"Execute a shell command.\"\"\"
        ...
        return ToolResult(content=output, is_error=False)

    # bash_exec 已经是 ToolDefinition，直接注册
    registry.register(bash_exec)

也可以先用 @tool 装饰再用 @tool_result：

    @tool_result
    @tool
    def read_file(path: Annotated[str, "File path"]) -> str:
        \"\"\"Read file content.\"\"\"
        return open(path).read()

    # 此时 tool_result 接收 FunctionTool，走 invoke() 路径（async → sync 桥接）
    # 函数返回 str，自动包装为 ToolResult(content=str)
"""

from __future__ import annotations

from typing import Any, Callable

from agent_framework._tools import FunctionTool
from agent_framework._tools import tool as _af_tool

from app.common.async_utils import run_awaitable_sync
from app.common.errors import AppError
from app.llm.types import InputSchema
from app.tools.definition import ToolDefinition, ToolResult

# 重新导出 AF @tool 装饰器
tool = _af_tool


def _extract_input_schema(ft: FunctionTool) -> InputSchema:
    """从 FunctionTool 提取 miniAgents InputSchema。"""
    spec = ft.to_json_schema_spec().get("function", {})
    params = spec.get("parameters", {})
    return InputSchema(
        type=params.get("type", "object"),
        properties=params.get("properties", {}),
        require=params.get("required", []),
    )


def tool_result(fn: FunctionTool | Callable) -> ToolDefinition:
    """装饰器：将函数或 FunctionTool 转换为 ToolDefinition，执行结果包装为 ToolResult。

    两种用法：

    **用法一：直接装饰普通函数（推荐用于 built-in tool）**

        @tool_result
        def bash_exec(command: Annotated[str, "Shell command"]) -> ToolResult:
            ...
            return ToolResult(content=output, is_error=False, metadata={"exit_code": 0})

    - 内部自动调用 AF ``@tool`` 提取参数 Schema
    - 直接调用原函数执行（同步，无 async 桥接开销）
    - 函数可返回完整 ToolResult（含 is_error、error_code、metadata）
    - AppError 自动转换为 ToolResult(is_error=True)

    **用法二：装饰 @tool 产生的 FunctionTool**

        @tool_result
        @tool
        def read_file(path: Annotated[str, "File path"]) -> str:
            return open(path).read()

    - 走 AF FunctionTool.invoke() 路径（Pydantic 参数校验 + async → sync 桥接）
    - 函数返回 str，自动包装为 ToolResult(content=str)
    - AppError 自动转换为 ToolResult(is_error=True)
    """
    if isinstance(fn, FunctionTool):
        return _from_function_tool(fn)
    return _from_callable(fn)


def _from_callable(fn: Callable) -> ToolDefinition:
    """从普通函数构建 ToolDefinition：AF @tool 提取 schema，直接调函数执行。"""
    ft = _af_tool(fn)

    def handler(arguments: dict) -> ToolResult:
        try:
            result = fn(**arguments)
            if isinstance(result, ToolResult):
                return result
            return ToolResult(content=str(result) if result is not None else "")
        except AppError as e:
            return ToolResult(content=e.message, is_error=True, error_code=e.code)

    return ToolDefinition(
        name=ft.name,
        description=ft.description or "",
        input_schema=_extract_input_schema(ft),
        handler=handler,
    )


def _from_function_tool(ft: FunctionTool) -> ToolDefinition:
    """从 FunctionTool 构建 ToolDefinition：走 invoke() 路径（含 Pydantic 校验）。"""

    def handler(arguments: dict) -> ToolResult:
        try:
            contents = run_awaitable_sync(ft.invoke(arguments=arguments))
            text = "\n".join(c.text for c in contents if c.text)
            return ToolResult(content=text)
        except AppError as e:
            return ToolResult(content=e.message, is_error=True, error_code=e.code)

    return ToolDefinition(
        name=ft.name,
        description=ft.description or "",
        input_schema=_extract_input_schema(ft),
        handler=handler,
    )


# 向后兼容
def get_tool_definition(fn: Any) -> ToolDefinition:
    """从 @tool 装饰的 FunctionTool 提取 ToolDefinition（向后兼容接口）。"""
    if not isinstance(fn, FunctionTool):
        raise ValueError(f"{fn!r} 未被 @tool 装饰（期望 FunctionTool，得到 {type(fn).__name__}）")
    return _from_function_tool(fn)
