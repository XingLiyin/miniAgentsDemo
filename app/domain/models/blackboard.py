"""Blackboard 领域模型（Phase 1）。

Blackboard 是 Session 内 topic 级的发布/订阅消息板。
系统保留 topic：_root（主目标摘要）、_digest（多轮汇总）、_lifecycle（生命周期事件）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BlackboardEntry:
    """黑板条目（追加写，不可变）。"""
    id: str
    session_id: str
    topic: str                         # 如 _root, _digest, 或自定义 topic
    publisher_id: str                  # 发布者 agent_id
    content: str
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "topic": self.topic,
            "publisher_id": self.publisher_id,
            "content": self.content,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BlackboardEntry":
        return cls(
            id=d["id"],
            session_id=d["session_id"],
            topic=d["topic"],
            publisher_id=d["publisher_id"],
            content=d["content"],
            created_at=d.get("created_at", ""),
        )


@dataclass
class TopicCursor:
    """记录某 Agent 在某 topic 上的已读位置（条目数）。"""
    agent_id: str
    topic: str
    last_read: int = 0                 # 已读条目数，下次 pull 从此处开始
