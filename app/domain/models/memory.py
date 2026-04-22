"""Memory 领域模型（Phase 1）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryItem:
    """单条消息记录（存入 messages.jsonl）。"""
    id: str
    session_id: str
    agent_id: str
    role: str                          # user | assistant | tool
    content: str
    task_id: str | None = None
    created_at: str = ""
    tool_call_id: str | None = None    # role="tool" 时填充，用于 provider 协议配对
    tool_calls: list | None = None     # role="assistant" 含工具调用时填充

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "role": self.role,
            "content": self.content,
            "task_id": self.task_id,
            "created_at": self.created_at,
        }
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_calls is not None:
            d["tool_calls"] = self.tool_calls
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MemoryItem":
        return cls(
            id=d["id"],
            session_id=d["session_id"],
            agent_id=d["agent_id"],
            role=d["role"],
            content=d["content"],
            task_id=d.get("task_id"),
            created_at=d.get("created_at", ""),
            tool_call_id=d.get("tool_call_id"),
            tool_calls=d.get("tool_calls"),
        )


@dataclass
class MemorySummary:
    """消息摘要（存入 summaries.json）。"""
    session_id: str
    agent_id: str
    summary_text: str
    covered_up_to: int                 # 已摘要的消息序号（inclusive）
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "summary_text": self.summary_text,
            "covered_up_to": self.covered_up_to,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MemorySummary":
        return cls(
            session_id=d["session_id"],
            agent_id=d["agent_id"],
            summary_text=d["summary_text"],
            covered_up_to=d.get("covered_up_to", 0),
            created_at=d.get("created_at", ""),
        )
