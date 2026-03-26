"""Blackboard 领域服务（Phase 1）。

提供 topic 级 publish / subscribe / pull（带 cursor）。
"""

from __future__ import annotations

from app.common.utils import new_blackboard_entry_id, now_iso
from app.domain.models.blackboard import BlackboardEntry, TopicCursor
from app.storage.file.blackboard_store import BlackboardStore


class BlackboardService:
    """发布/订阅黑板条目。"""

    def __init__(self, store: BlackboardStore) -> None:
        self._store = store
        # 内存 cursor 表：{(agent_id, session_id, topic): last_read}
        self._cursors: dict[tuple[str, str, str], int] = {}

    def publish(
        self,
        session_id: str,
        topic: str,
        publisher_id: str,
        content: str,
    ) -> BlackboardEntry:
        """发布一条黑板条目（追加写）。"""
        entry = BlackboardEntry(
            id=new_blackboard_entry_id(),
            session_id=session_id,
            topic=topic,
            publisher_id=publisher_id,
            content=content,
            created_at=now_iso(),
        )
        self._store.append(session_id, topic, entry.to_dict())
        return entry

    def pull(self, session_id: str, topic: str, agent_id: str) -> list[BlackboardEntry]:
        """拉取自上次读取以来的增量条目，更新 cursor。"""
        key = (agent_id, session_id, topic)
        cursor = self._cursors.get(key, 0)
        raw_entries = self._store.read_since(session_id, topic, cursor)
        entries = [BlackboardEntry.from_dict(d) for d in raw_entries]
        self._cursors[key] = cursor + len(entries)
        return entries

    def subscribe(self, session_id: str, topic: str) -> list[BlackboardEntry]:
        """读取 topic 全部条目（无 cursor）。"""
        return [BlackboardEntry.from_dict(d) for d in self._store.read_all(session_id, topic)]

    def list_topics(self, session_id: str) -> list[str]:
        return self._store.list_topics(session_id)
