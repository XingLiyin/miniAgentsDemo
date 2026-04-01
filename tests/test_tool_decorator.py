"""Tests for @tool_result decorator (app/tools/tool_decorator.py)."""

from __future__ import annotations

from typing import Annotated
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.common.errors import AppError
from app.tools.definition import ToolDefinition, ToolResult
from app.tools.tool_decorator import _extract_input_schema, tool_result


# ── Fixtures ──────────────────────────────────────────────────────────────────

@tool_result
def _add(
    a: Annotated[int, "First number"],
    b: Annotated[int, "Second number"],
) -> int:
    """Add two numbers."""
    return a + b


@tool_result
def _greet(name: Annotated[str, "Person's name"]) -> str:
    """Return a greeting."""
    return f"hello {name}"


@tool_result
def _fail(msg: Annotated[str, "Error message"]) -> str:
    """Always raises AppError."""
    raise AppError("TEST_ERR", msg)
    return ""


@tool_result
def _returns_tool_result(x: Annotated[str, "Input"]) -> ToolResult:
    """Returns a full ToolResult directly."""
    return ToolResult(content=x, is_error=False, metadata={"key": "val"})


@tool_result
def _returns_none(x: Annotated[str, "Input"]) -> None:
    """Returns None (no explicit return)."""
    pass


# ── ToolDefinition shape ──────────────────────────────────────────────────────

class TestToolDefinitionShape:
    def test_returns_tool_definition(self):
        assert isinstance(_add, ToolDefinition)

    def test_name_matches_function_name(self):
        assert _add.name == "_add"

    def test_description_from_docstring(self):
        assert _add.description == "Add two numbers."

    def test_handler_is_callable(self):
        assert callable(_add.handler)

    def test_input_schema_has_properties(self):
        assert "a" in _add.input_schema.properties
        assert "b" in _add.input_schema.properties

    def test_input_schema_required_fields(self):
        assert "a" in _add.input_schema.require
        assert "b" in _add.input_schema.require


# ── _from_callable execution path ─────────────────────────────────────────────

class TestFromCallable:
    def test_int_result_wrapped_as_string(self):
        result = _add.handler({"a": 3, "b": 4})
        assert isinstance(result, ToolResult)
        assert result.content == "7"
        assert not result.is_error

    def test_str_result_wrapped(self):
        result = _greet.handler({"name": "Alice"})
        assert result.content == "hello Alice"
        assert not result.is_error

    def test_app_error_becomes_tool_result_error(self):
        result = _fail.handler({"msg": "boom"})
        assert result.is_error
        assert result.error_code == "TEST_ERR"
        assert result.content == "boom"

    def test_full_tool_result_passed_through(self):
        result = _returns_tool_result.handler({"x": "data"})
        assert result.content == "data"
        assert not result.is_error
        assert result.metadata == {"key": "val"}

    def test_none_return_becomes_empty_string(self):
        result = _returns_none.handler({"x": "anything"})
        assert result.content == ""
        assert not result.is_error


# ── _extract_input_schema ──────────────────────────────────────────────────────

class TestExtractInputSchema:
    def test_schema_type_is_object(self):
        assert _add.input_schema.type == "object"

    def test_optional_param_not_in_required(self):
        @tool_result
        def _opt(
            required: Annotated[str, "required"],
            optional: Annotated[str, "optional"] = "default",
        ) -> str:
            """Optional param test."""
            return required

        assert "required" in _opt.input_schema.require
        assert "optional" not in _opt.input_schema.require

    def test_properties_contain_type_info(self):
        props = _add.input_schema.properties
        assert props["a"].get("type") == "integer"
        assert props["b"].get("type") == "integer"


# ── _from_function_tool path ───────────────────────────────────────────────────

class TestFromFunctionTool:
    def _make_mock_ft(self, return_text: str) -> MagicMock:
        """Build a minimal FunctionTool mock."""
        content = MagicMock()
        content.text = return_text

        ft = MagicMock()
        ft.name = "mock_tool"
        ft.description = "A mock tool."
        ft.to_json_schema_spec.return_value = {
            "function": {
                "parameters": {
                    "type": "object",
                    "properties": {"x": {"type": "string"}},
                    "required": ["x"],
                }
            }
        }
        ft.invoke = AsyncMock(return_value=[content])
        return ft

    def test_function_tool_path_returns_text(self):
        from agent_framework._tools import FunctionTool
        from app.tools.tool_decorator import _from_function_tool

        ft = self._make_mock_ft("result text")
        # Pretend it's a FunctionTool instance for the isinstance check
        ft.__class__ = FunctionTool

        td = tool_result(ft)
        assert isinstance(td, ToolDefinition)
        result = td.handler({"x": "hello"})
        assert result.content == "result text"
        assert not result.is_error

    def test_function_tool_multiple_content_blocks_joined(self):
        from agent_framework._tools import FunctionTool
        from app.tools.tool_decorator import _from_function_tool

        c1, c2 = MagicMock(), MagicMock()
        c1.text = "line one"
        c2.text = "line two"

        ft = MagicMock()
        ft.name = "multi"
        ft.description = "Multi-block tool."
        ft.to_json_schema_spec.return_value = {
            "function": {"parameters": {"type": "object", "properties": {}, "required": []}}
        }
        ft.invoke = AsyncMock(return_value=[c1, c2])
        ft.__class__ = FunctionTool

        td = tool_result(ft)
        result = td.handler({})
        assert result.content == "line one\nline two"
