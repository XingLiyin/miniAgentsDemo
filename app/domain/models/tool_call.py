"""ToolCall 审计领域模型（Phase 1）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """工具调用审计记录（只追加写，不可变）。"""
    id: str
    session_id: str
    task_id: str
    agent_id: str
    tool_name: str
    status: str                        # RUNNING | SUCCEEDED | FAILED
    arguments: dict[str, Any] = field(default_factory=dict)
    result: str | None = None
    error: str | None = None
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "tool_name": self.tool_name,
            "status": self.status,
            "arguments": self.arguments,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolCall":
        return cls(
            id=d["id"],
            session_id=d["session_id"],
            task_id=d["task_id"],
            agent_id=d["agent_id"],
            tool_name=d["tool_name"],
            status=d["status"],
            arguments=d.get("arguments", {}),
            result=d.get("result"),
            error=d.get("error"),
            started_at=d.get("started_at", ""),
            finished_at=d.get("finished_at", ""),
        )
