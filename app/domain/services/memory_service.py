"""Memory 领域服务（Phase 1）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.common.utils import new_memory_id, now_iso
from app.config.settings import get_settings
from app.domain.models.memory import MemoryItem, MemorySummary
from app.storage.file.memory_store import MemoryStore


@dataclass
class PromptContext:
    """拼装好的 Prompt 上下文，供 AgentLoop.plan() 使用。"""
    system_prompt: str
    goal: str
    task_description: str
    blackboard_snippets: list[str] = field(default_factory=list)
    recent_messages: list[dict[str, Any]] = field(default_factory=list)
    summary_text: str = ""
    token_estimate: int = 0


class MemoryService:
    """消息追加、上下文拼装、触发摘要（以 agent_id 为存储 key）。"""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def append_message(
        self,
        agent_id: str,
        role: str,
        content: str | list,
        session_id: str = "",
        task_id: str | None = None,
        tool_call_id: str | None = None,
        tool_calls: list | None = None,
    ) -> MemoryItem:
        """追加一条消息到该 agent 的 messages.jsonl。"""
        item = MemoryItem(
            id=new_memory_id(),
            session_id=session_id,
            agent_id=agent_id,
            role=role,
            content=content,
            task_id=task_id,
            created_at=now_iso(),
            tool_call_id=tool_call_id,
            tool_calls=tool_calls,
        )
        self._store.append_message(agent_id, item.to_dict())
        return item

    def get_all_messages(self, agent_id: str) -> list[dict[str, Any]]:
        """读取该 agent 的全量消息记录。"""
        return self._store.read_messages(agent_id)

    def get_window(self, agent_id: str, n: int | None = None) -> list[dict[str, Any]]:
        """读取该 agent 最近 n 条消息（默认使用配置值）。"""
        if n is None:
            n = get_settings().default_short_window_size
        return self._store.read_window(agent_id, n)

    def get_summary(self, agent_id: str) -> MemorySummary | None:
        data = self._store.get_summary(agent_id)
        if data is None:
            return None
        return MemorySummary.from_dict(data)

    def save_summary(self, agent_id: str, summary: MemorySummary) -> None:
        self._store.save_summary(agent_id, summary.to_dict())

    def should_summarize(
        self,
        agent_id: str,
        threshold: int | None = None,
        context_tokens: int = 0,
        context_limit: int = 0,
    ) -> bool:
        """判断该 agent 是否达到摘要阈值。

        两个维度任一满足即触发：
        - 消息数超过 threshold（原有逻辑）
        - context_tokens 超过 context_limit 的 80%（上下文窗口将满）
        """
        if context_limit and context_tokens >= int(context_limit * 0.8):
            return True
        if threshold is None:
            threshold = get_settings().default_summary_threshold
        count = self._store.count_messages(agent_id)
        summary = self.get_summary(agent_id)
        covered = summary.covered_up_to if summary else 0
        return (count - covered) >= threshold

    def count_messages(self, agent_id: str) -> int:
        return self._store.count_messages(agent_id)

    def rewrite_messages(self, agent_id: str, messages: list[dict[str, Any]]) -> None:
        """用 compact 后的消息替换活跃窗口（原内容归档到 .bak）。"""
        self._store.rewrite_messages(agent_id, messages)

    def delete_agent(self, agent_id: str) -> None:
        """删除该 agent 的全部记忆文件。"""
        self._store.delete_agent(agent_id)
