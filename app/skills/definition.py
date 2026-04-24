"""Skill 数据模型。

三层加载模型：
  Level 1  SkillMetadata   — YAML frontmatter，常驻内存，注入 plan prompt
  Level 2  SkillDefinition — SKILL.md 主体（Instructions），按需加载，注入 task system prompt
  Level 3  资源文件         — skill 目录下的其他文件，执行阶段按需读取
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SkillMetadata:
    """Level 1 — 常驻内存的元数据（从 SKILL.md frontmatter 解析）。"""

    name: str
    description: str
    triggers: list[str]
    version: str
    skill_dir: Path

    # 来源字段（注册时由调用方注入，不来自 SKILL.md frontmatter）
    source: str = "local"                  # "local" | "remote"
    remote_source_name: str | None = None  # 对应 SkillRegistry._mcp_conns 的 key
    extra: dict = field(default_factory=dict)


@dataclass
class SkillDefinition:
    """Level 2 — 按需加载的完整 Skill 定义。"""

    metadata: SkillMetadata
    instructions: str  # SKILL.md 主体文本（frontmatter 之后的部分）


# ── 远端 skill 来源注册用数据类 ────────────────────────────────────────────────

@dataclass
class RemoteSkillSourceConfig:
    """一个远端 skill 来源的完整配置（注册调用时传入，可持久化）。"""

    source_name: str                               # 本地唯一标识，作为 _mcp_conns 的 key
    mcp_type: str                                  # "http" | "stdio"
    # http 连接参数
    mcp_url: str | None = None
    mcp_timeout: int = 30
    # stdio 连接参数
    mcp_command: str | None = None
    mcp_args: list[str] = field(default_factory=list)
    mcp_env: dict[str, str] = field(default_factory=dict)
    # MCP tool 名称（与远端 MCP server spec 对齐）
    mcp_tool_list_skills: str = "listSkills"
    mcp_tool_load_skill_md: str = "loadSkillMd"
    mcp_tool_get_skill_files: str = "getSkillFiles"
    mcp_tool_load_skill_reference: str = "loadSkillReference"
    mcp_tool_exec_skill_script: str = "execSkillScript"
