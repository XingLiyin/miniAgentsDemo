"""远端 Skill 来源管理 API Schema。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class _SourceBaseRequest(BaseModel):
    source_name: str = Field(..., description="来源唯一标识名")
    mcp_tool_list_skills: str = Field(default="listSkills", description="列出 skill 列表的 MCP tool 名")
    mcp_tool_load_skill_md: str = Field(default="loadSkillMd", description="读取 SKILL.md 的 MCP tool 名")
    mcp_tool_get_skill_files: str = Field(default="getSkillFiles", description="列出 skill 文件的 MCP tool 名")
    mcp_tool_load_skill_reference: str = Field(default="loadSkillReference", description="读取参考文件的 MCP tool 名")
    mcp_tool_exec_skill_script: str = Field(default="execSkillScript", description="执行脚本的 MCP tool 名")


class RegisterHttpSourceRequest(_SourceBaseRequest):
    mcp_url: str = Field(..., description="远端 skill MCP server 的 HTTP 端点")
    mcp_timeout: int = Field(default=30, ge=1, description="请求超时秒数")


class RegisterStdioSourceRequest(_SourceBaseRequest):
    mcp_command: str = Field(..., description="启动 MCP server 的可执行文件，如 'python'")
    mcp_args: list[str] = Field(default_factory=list, description="命令参数列表")
    mcp_env: dict[str, str] = Field(default_factory=dict, description="额外环境变量")


class RemoteSkillSourceResponse(BaseModel):
    source_name: str
    mcp_type: str
    mcp_tool_list_skills: str
    mcp_tool_load_skill_md: str
    mcp_tool_get_skill_files: str
    mcp_tool_load_skill_reference: str
    mcp_tool_exec_skill_script: str
    mcp_url: Optional[str] = None
    mcp_timeout: Optional[int] = None
    mcp_command: Optional[str] = None
    mcp_args: Optional[list[str]] = None
    mcp_env: Optional[dict[str, str]] = None
