"""Agent 定义文件数据模型。

两层加载模型：
  Level 1  AgentDefMetadata — SOUL.md + TOOLS.md frontmatter，常驻内存
  Level 2  AgentDefContent  — 四个文件正文内容，按需加载，用于拼装 system prompt
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentDefMetadata:
    """Level 1 — 常驻内存的元数据（从 SOUL.md frontmatter 解析）。"""

    name: str
    version: str
    description: str
    tool_list: list[str]      # 来自 TOOLS.md frontmatter.tools（可能为空）
    tool_list_ready: bool     # True = tool_list 已从 frontmatter 提取
    agent_dir: Path           # 四个文件所在目录


@dataclass
class AgentDefContent:
    """Level 2 — 四个文件的正文内容（按需加载，用于拼装 system prompt）。"""

    metadata: AgentDefMetadata
    soul_md: str
    role_md: str
    tools_md: str
    style_md: str
