"""SkillMCPConn：远端 skill 专属私有 MCP 连接，不进 ToolRegistry，LLM 不可见。

MCP tool 协议（参数名 camelCase，与 MCP server spec 对齐）：
    listSkills()                                         → [{name, description}] JSON
    loadSkillMd(skillName)                               → SKILL.md 文本
    getSkillFiles(skillName, pattern?, limit?)            → newline 分隔的文件路径列表
    loadSkillReference(skillName, referencePath)         → 文件内容
    execSkillScript(skillName, scriptPath, args?)        → 脚本 stdout+stderr
"""

from __future__ import annotations

import json
import logging

from app.tools.types import CallContext, ToolResult
from app.tools.mcp_base import _MCPProviderBase

logger = logging.getLogger(__name__)


class SkillMCPConn:
    """封装对远端 skill MCP server 的私有调用。"""

    def __init__(
        self,
        provider: _MCPProviderBase,
        mcp_tool_list_skills: str = "listSkills",
        mcp_tool_load_skill_md: str = "loadSkillMd",
        mcp_tool_get_skill_files: str = "getSkillFiles",
        mcp_tool_load_skill_reference: str = "loadSkillReference",
        mcp_tool_exec_skill_script: str = "execSkillScript",
    ) -> None:
        self._provider = provider
        self._mcp_tool_list_skills = mcp_tool_list_skills
        self._mcp_tool_load_skill_md = mcp_tool_load_skill_md
        self._mcp_tool_get_skill_files = mcp_tool_get_skill_files
        self._mcp_tool_load_skill_reference = mcp_tool_load_skill_reference
        self._mcp_tool_exec_skill_script = mcp_tool_exec_skill_script

    def list_skills(self, ctx: CallContext | None = None) -> list[dict]:
        """调用 listSkills，返回解析后的 [{name, description}] 列表。"""
        result = self._provider.call(self._mcp_tool_list_skills, {}, ctx)
        return json.loads(result.content)

    def load_skill_md(self, skill_name: str, ctx: CallContext | None = None) -> str:
        """调用 loadSkillMd，返回 SKILL.md 主体文本。"""
        return self._provider.call(
            self._mcp_tool_load_skill_md,
            {"skillName": skill_name},
            ctx,
        ).content

    def get_skill_files(
        self,
        skill_name: str,
        pattern: str = "**/*",
        limit: int = 200,
        ctx: CallContext | None = None,
    ) -> str:
        """调用 getSkillFiles，返回 newline 分隔的文件路径列表。"""
        return self._provider.call(
            self._mcp_tool_get_skill_files,
            {"skillName": skill_name, "pattern": pattern, "limit": limit},
            ctx,
        ).content

    def load_skill_reference(
        self,
        skill_name: str,
        reference_path: str,
        ctx: CallContext | None = None,
    ) -> str:
        """调用 loadSkillReference，返回参考文件内容。"""
        return self._provider.call(
            self._mcp_tool_load_skill_reference,
            {"skillName": skill_name, "referencePath": reference_path},
            ctx,
        ).content

    def exec_skill_script(
        self,
        skill_name: str,
        script_path: str,
        args: str = "",
        ctx: CallContext | None = None,
    ) -> ToolResult:
        """调用 execSkillScript，返回脚本执行结果。"""
        return self._provider.call(
            self._mcp_tool_exec_skill_script,
            {"skillName": skill_name, "scriptPath": script_path, "args": args},
            ctx,
        )

    def stop(self) -> None:
        try:
            self._provider.stop()
        except Exception:
            logger.warning("SkillMCPConn.stop: error while stopping provider", exc_info=True)
