"""Agent 定义文件数据模型。

两层加载模型：
  Level 1  AgentDefMetadata — SOUL.md + ROLE.md frontmatter，常驻内存
  Level 2  AgentDefContent  — 四个文件正文内容，按需加载，用于拼装 system prompt
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ToolSpec:
    """单阶段工具声明：必须有的（required）和不允许有的（forbidden）。"""

    required: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)

    def effective(self) -> list[str]:
        """解析为最终可用工具列表：required 去掉 forbidden。"""
        excluded = set(self.forbidden)
        return [t for t in self.required if t not in excluded]


@dataclass
class AgentDefMetadata:
    """Level 1 — 常驻内存的元数据（从 SOUL.md / ROLE.md frontmatter 解析）。"""

    name: str
    version: str
    description: str
    act_tool_spec: ToolSpec    # 来自 SOUL.md tools frontmatter（Actor 阶段）
    observe_tool_spec: ToolSpec  # 来自 ROLE.md tools frontmatter（Observer 阶段）
    agent_dir: Path            # 四个文件所在目录


@dataclass
class AgentDefContent:
    """Level 2 — 四个文件的正文内容（按需加载，用于拼装 system prompt）。"""

    metadata: AgentDefMetadata
    soul_md: str
    role_md: str
    tools_md: str
    style_md: str
