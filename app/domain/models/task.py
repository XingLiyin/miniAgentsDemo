"""Task 领域模型（Phase 1）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Task:
    """任务领域对象。

    状态流转：PENDING → ACTIVE → FINISHED / FAILED / CANCELED
    """
    id: str
    session_id: str
    agent_id: str
    type: str                          # reasoning | tool-call
    title: str
    status: str                        # PENDING | ACTIVE | FINISHED | FAILED | CANCELED

    description: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    result: str | None = None          # reasoning 结果文本
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "type": self.type,
            "title": self.title,
            "status": self.status,
            "description": self.description,
            "inputs": self.inputs,
            "result": self.result,
            "outputs": self.outputs,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Task":
        return cls(
            id=d["id"],
            session_id=d["session_id"],
            agent_id=d["agent_id"],
            type=d["type"],
            title=d["title"],
            status=d["status"],
            description=d.get("description", ""),
            inputs=d.get("inputs", {}),
            result=d.get("result"),
            outputs=d.get("outputs", {}),
            error=d.get("error"),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )
