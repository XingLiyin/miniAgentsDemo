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


@dataclass
class SkillDefinition:
    """Level 2 — 按需加载的完整 Skill 定义。"""

    metadata: SkillMetadata
    instructions: str  # SKILL.md 主体文本（frontmatter 之后的部分）
