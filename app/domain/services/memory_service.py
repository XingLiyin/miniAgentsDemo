"""Memory 领域服务（Phase 1）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.common.utils import new_memory_id, now_iso, estimate_tokens
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
    """消息追加、上下文拼装、触发摘要。"""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def append_message(
        self,
        session_id: str,
        agent_id: str,
        role: str,
        content: str,
        task_id: str | None = None,
    ) -> MemoryItem:
        """追加一条消息到 messages.jsonl。"""
        item = MemoryItem(
            id=new_memory_id(),
            session_id=session_id,
            agent_id=agent_id,
            role=role,
            content=content,
            task_id=task_id,
            created_at=now_iso(),
        )
        self._store.append_message(session_id, item.to_dict())
        return item

    def get_window(self, session_id: str, n: int | None = None) -> list[dict[str, Any]]:
        """读取最近 n 条消息（默认使用配置值）。"""
        if n is None:
            n = get_settings().default_short_window_size
        return self._store.read_window(session_id, n)

    def get_summary(self, session_id: str) -> MemorySummary | None:
        data = self._store.get_summary(session_id)
        if data is None:
            return None
        return MemorySummary.from_dict(data)

    def save_summary(self, session_id: str, summary: MemorySummary) -> None:
        self._store.save_summary(session_id, summary.to_dict())

    def should_summarize(self, session_id: str, threshold: int | None = None) -> bool:
        """判断是否达到摘要阈值。"""
        if threshold is None:
            threshold = get_settings().default_summary_threshold
        count = self._store.count_messages(session_id)
        summary = self.get_summary(session_id)
        covered = summary.covered_up_to if summary else 0
        return (count - covered) >= threshold

    def build_prompt_context(
        self,
        session_id: str,
        agent_id: str,
        system_prompt: str,
        goal: str,
        task_description: str,
        blackboard_snippets: list[str] | None = None,
        token_budget: int = 200_000,
    ) -> PromptContext:
        """拼装完整 Prompt 上下文，按优先级截断。"""
        settings = get_settings()
        messages = self.get_window(session_id, settings.default_short_window_size)
        summary = self.get_summary(session_id)
        summary_text = summary.summary_text if summary else ""

        ctx = PromptContext(
            system_prompt=system_prompt,
            goal=goal,
            task_description=task_description,
            blackboard_snippets=blackboard_snippets or [],
            recent_messages=messages,
            summary_text=summary_text,
        )

        # 粗略 token 估算
        total_text = system_prompt + goal + task_description + summary_text
        total_text += " ".join(m.get("content", "") for m in messages)
        total_text += " ".join(blackboard_snippets or [])
        ctx.token_estimate = estimate_tokens(total_text)

        # 超过 60% token_budget 时截断低优先级内容（blackboard + 旧消息）
        limit = int(token_budget * 0.6)
        if ctx.token_estimate > limit:
            ctx.blackboard_snippets = []
            ctx.recent_messages = messages[-5:]  # 只保留最近 5 条

        return ctx
