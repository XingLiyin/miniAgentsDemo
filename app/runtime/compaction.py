"""上下文压缩策略（CompactionStrategy）。

使用 agent-framework 官方压缩策略作为底层实现：
- TruncationStrategy    →  agent_framework._compaction.TruncationStrategy
- SummarizationStrategy →  agent_framework._compaction.SummarizationStrategy
- TokenBudgetComposedStrategy（新增） → agent_framework._compaction.TokenBudgetComposedStrategy

对外接口保持不变：compact(messages, token_budget) -> (messages, summary_str)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from agent_framework import (
    CharacterEstimatorTokenizer,
    ChatResponse,
    Message,
    SummarizationStrategy as _AF_Summarization,
    TokenBudgetComposedStrategy as _AF_TokenBudget,
    TruncationStrategy as _AF_Truncation,
    apply_compaction,
)

from app.common.async_utils import run_awaitable_sync

if TYPE_CHECKING:
    from app.llm.base import BaseChatClient

logger = logging.getLogger(__name__)


# ── 对外接口（不变） ────────────────────────────────────────────────────────

class CompactionStrategy(ABC):
    """上下文压缩策略抽象基类（接口不变，内部使用 AF 实现）。"""

    @abstractmethod
    def compact(
        self,
        messages: list[dict[str, Any]],
        token_budget: int = 200_000,
    ) -> tuple[list[dict[str, Any]], str]:
        """压缩消息列表。返回 (保留的消息列表, 摘要文本)。"""
        raise NotImplementedError


# ── 消息格式转换 ────────────────────────────────────────────────────────────

def _to_af(messages: list[dict[str, Any]]) -> list[Message]:
    return [Message(role=m.get("role", "user"), text=m.get("content", "")) for m in messages]


def _from_af(af_messages: list[Message]) -> list[dict[str, Any]]:
    return [{"role": m.role, "content": m.text or ""} for m in af_messages]


# ── TruncationStrategy ──────────────────────────────────────────────────────

class TruncationStrategy(CompactionStrategy):
    """截断策略：使用 AF TruncationStrategy，保留最近 keep_last 条消息。"""

    def __init__(self, keep_last: int = 20) -> None:
        # max_n: 触发阈值；compact_to: 保留目标条数（与 keep_last 一致）
        self._af = _AF_Truncation(max_n=keep_last + 1, compact_to=keep_last)
        self._keep_last = keep_last

    def compact(
        self,
        messages: list[dict[str, Any]],
        token_budget: int = 200_000,
    ) -> tuple[list[dict[str, Any]], str]:
        if len(messages) <= self._keep_last:
            return messages, ""
        af_msgs = _to_af(messages)
        result = run_awaitable_sync(apply_compaction(af_msgs, strategy=self._af))
        kept = _from_af(result)
        dropped = len(messages) - len(kept)
        summary = f"[Truncated {dropped} older messages, kept {len(kept)}]"
        logger.debug("TruncationStrategy: dropped %d, kept %d", dropped, len(kept))
        return kept, summary


# ── SummarizationStrategy ───────────────────────────────────────────────────

class _BaseChatClientAdapter:
    """将 miniAgents BaseChatClient 包装为 AF SupportsChatGetResponse。

    SummarizationStrategy 需要 async get_response()，
    此适配器将我们的同步 send_message() 桥接为 async 接口。
    """

    def __init__(self, client: "BaseChatClient") -> None:
        self._client = client

    async def get_response(
        self,
        messages: list[Message],
        *,
        options=None,
        **kwargs: Any,
    ) -> ChatResponse:
        from app.llm.types import LLMMessage

        system_msgs = [m for m in messages if m.role == "system"]
        non_system = [m for m in messages if m.role != "system"]
        system_prompt = system_msgs[0].text if system_msgs else None
        llm_msgs = [LLMMessage(role=m.role, content=m.text or "") for m in non_system]

        resp = self._client.send_message(llm_msgs, system_prompt=system_prompt)
        af_msg = Message(role="assistant", text=resp.text)
        return ChatResponse(messages=[af_msg])


class SummarizationStrategy(CompactionStrategy):
    """摘要策略：使用 AF SummarizationStrategy + 我们的 LLM 客户端桥接。"""

    def __init__(self, llm_client: "BaseChatClient", keep_last: int = 10) -> None:
        adapter = _BaseChatClientAdapter(llm_client)
        self._af = _AF_Summarization(client=adapter, target_count=keep_last, threshold=2)
        self._keep_last = keep_last

    def compact(
        self,
        messages: list[dict[str, Any]],
        token_budget: int = 200_000,
    ) -> tuple[list[dict[str, Any]], str]:
        if len(messages) <= self._keep_last:
            return messages, ""
        af_msgs = _to_af(messages)
        try:
            result = run_awaitable_sync(apply_compaction(af_msgs, strategy=self._af))
            kept = _from_af(result)
            summary = f"[Summarized to {len(kept)} messages]"
            logger.debug("SummarizationStrategy: compacted %d → %d", len(messages), len(kept))
        except Exception:
            logger.exception("SummarizationStrategy: AF summarization failed, falling back to truncation")
            kept = messages[-self._keep_last:]
            summary = f"[Summarized {len(messages) - len(kept)} older messages]"
        return kept, summary


# ── TokenBudgetComposedStrategy（新增） ────────────────────────────────────

class TokenBudgetComposedStrategy(CompactionStrategy):
    """Token 预算组合策略：超出 token_budget 时依次应用子策略，直到满足预算。

    使用 AF CharacterEstimatorTokenizer（字符数估算 token 数）。
    """

    def __init__(
        self,
        token_budget: int,
        strategies: list[CompactionStrategy],
        early_stop: bool = True,
    ) -> None:
        tokenizer = CharacterEstimatorTokenizer()
        # 将我们的 CompactionStrategy 包装为 AF CompactionStrategy callable
        af_strategies = [_MiniAgentsStrategyAdapter(s) for s in strategies]
        self._af = _AF_TokenBudget(
            token_budget=token_budget,
            tokenizer=tokenizer,
            strategies=af_strategies,
            early_stop=early_stop,
        )
        self._token_budget = token_budget

    def compact(
        self,
        messages: list[dict[str, Any]],
        token_budget: int = 200_000,
    ) -> tuple[list[dict[str, Any]], str]:
        af_msgs = _to_af(messages)
        result = run_awaitable_sync(apply_compaction(af_msgs, strategy=self._af))
        kept = _from_af(result)
        dropped = len(messages) - len(kept)
        summary = f"[TokenBudget compaction: dropped {dropped}, kept {len(kept)}]"
        return kept, summary


class _MiniAgentsStrategyAdapter:
    """将 miniAgents CompactionStrategy 包装为 AF CompactionStrategy protocol。

    AF 的 CompactionStrategy 要求 async def __call__(messages) -> bool，
    其中 True 表示本次调用修改了消息列表。
    此处将同步的 compact() 结果写回 messages 并返回是否发生变化。
    """

    def __init__(self, strategy: CompactionStrategy) -> None:
        self._strategy = strategy

    async def __call__(self, messages: list[Message]) -> bool:
        dicts = _from_af(messages)
        result, _ = self._strategy.compact(dicts)
        if len(result) == len(dicts):
            return False
        # 原地替换列表内容，使调用方可见变化
        messages[:] = _to_af(result)
        return True
