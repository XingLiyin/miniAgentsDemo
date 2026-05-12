"""Agent 定义文件数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentCapabilityConfig:
    """Agent Capability 配置。"""
    required_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    required_mcp_servers: list[str] = field(default_factory=list)
    forbidden_mcp_servers: list[str] = field(default_factory=list)
    subagents: list[str] = field(default_factory=list)

    def effective_tools(self) -> list[str]:
        excluded = set(self.forbidden_tools)
        return [t for t in self.required_tools if t not in excluded]


@dataclass
class AgentDefDetails:
    """Agent 定义的完整细节。"""

    name: str
    version: str
    description: str

    source_dir: str = ""      # 文件所在目录（由 loader 填入，供 service 持久化用）
    actor_soul: str = ""      # SOUL.md 正文
    observer_role: str = ""   # ROLE.md 正文
    actor_capability: AgentCapabilityConfig = field(default_factory=AgentCapabilityConfig)
    observer_capability: AgentCapabilityConfig = field(default_factory=AgentCapabilityConfig)
