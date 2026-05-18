"""Skill 执行工具：get_skill_files、load_skill_reference、exec_skill_script。"""

from __future__ import annotations

from typing import Annotated

from app.common.errors import AppError
from app.tools.types import CallContext, ToolDefinition, ToolResult
from app.tools.utils import extract_input_schema, make_tool_handler

_SKILL_EXECUTOR_TOOLS: list[ToolDefinition] = []


def skill_executor_tool(fn):
    tool_def = ToolDefinition(
        name=fn.__name__,
        description=(fn.__doc__ or "").strip(),
        input_schema=extract_input_schema(fn, exclude={"ctx"}),
        handler=make_tool_handler(fn),
    )
    _SKILL_EXECUTOR_TOOLS.append(tool_def)
    return tool_def


def _get_skill(skill_name: str, ctx: CallContext | None):
    from app.skills.registry import get_skill_registry
    skill = get_skill_registry().fetch_skill(skill_name, ctx)
    if skill is None:
        raise AppError("SKILL_NOT_FOUND", f"Skill '{skill_name}' not found in registry")
    return skill


def _skill_name_from_ctx(ctx: CallContext | None) -> str:
    name = ctx.task.settings.get("skill_name") if (ctx and ctx.task and ctx.task.settings) else None
    if not name:
        raise AppError("MISSING_SKILL_NAME", "Task has no skill assigned")
    return name


# ── get_skill_files ───────────────────────────────────────────────────────────

@skill_executor_tool
def get_skill_files(
    pattern: Annotated[str, "Glob pattern to match files (default: '**/*', local skills only)"] = "**/*",
    limit: Annotated[int, "Maximum number of results (default: 200, local skills only)"] = 200,
    *,
    ctx: CallContext | None = None,
) -> ToolResult:
    """List files available in the current task's skill directory. For local skills, supports glob patterns (e.g. 'scripts/*.py', 'references/**'). Returns a newline-separated list of file paths (local) or file names (remote). For remote skills, also returns file IDs usable with load_skill_reference and exec_skill_script."""
    skill = _get_skill(_skill_name_from_ctx(ctx), ctx)
    content = skill.get_files(pattern, limit, ctx)
    return ToolResult(content=content, metadata={"skill": skill.name, "source": skill.source.label})


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
    skill = _get_skill(_skill_name_from_ctx(ctx), ctx)
    content = skill.load_reference(reference_path, ctx)
    return ToolResult(content=content, metadata={"skill": skill.name, "path": reference_path})


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
    skill = _get_skill(_skill_name_from_ctx(ctx), ctx)
    return skill.exec_script(script_path, args, ctx)


# ── Provider 入口 ─────────────────────────────────────────────────────────────

def get_skill_executor_tools() -> list[ToolDefinition]:
    """返回所有 skill 执行工具定义。"""
    return list(_SKILL_EXECUTOR_TOOLS)
