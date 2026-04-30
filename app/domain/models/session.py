"""Session 领域模型（Phase 1）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _coerce_str(v: Any) -> str:
    """将可能因历史数据污染而成为 list 的字段强制转为纯文本。"""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return '\n'.join(p.get('text', '') for p in v if isinstance(p, dict) and p.get('type') == 'text')
    return str(v) if v is not None else ''


@dataclass
class LoopGuard:
    """Agent Loop Guard 参数。"""
    turns_used: int = 0
    max_turns: int = 20


@dataclass
class Session:
    """会话领域对象。

    状态流转：QUEUED → RUNNING → SUCCEEDED / FAILED / CANCELED
    """
    id: str
    user_prompt: str                   # 用户输入的文本信息，创建 session 的依据，供 Agent 处理，不可为空
    goal: str                          # session 目标，供 Agent 处理，初始值同 user_prompt，等待 llm 补全
    status: str                        # QUEUED | RUNNING | SUCCEEDED | FAILED | CANCELED
    template_id: str | None
    root_agent_id: str | None

    # Guard 字段
    token_budget: int = 200_000        # 硬上限，超出立即终止
    token_used: int = 0                # 已消耗 token 总数
    root_max_turns: int = 20           # 软上限，超出给出警告
    failure_counter: int = 0           # 连续失败计数
    failure_threshold: int = 3         # 达到阈值时暂停（Phase 2 HITL）

    llm_name: str = ""
    llm_model: str = ""
    working_dir: str = ""   # workspace path bound to this session

    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_prompt": self.user_prompt,
            "goal": self.goal,
            "status": self.status,
            "template_id": self.template_id,
            "root_agent_id": self.root_agent_id,
            "token_budget": self.token_budget,
            "token_used": self.token_used,
            "root_max_turns": self.root_max_turns,
            "failure_counter": self.failure_counter,
            "failure_threshold": self.failure_threshold,
            "llm_name": self.llm_name,
            "llm_model": self.llm_model,
            "working_dir": self.working_dir,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Session":
        return cls(
            id=d["id"],
            user_prompt=_coerce_str(d["user_prompt"]),
            goal=d.get("goal") or _coerce_str(d.get("user_prompt", "")),
            status=d["status"],
            template_id=d.get("template_id"),
            root_agent_id=d.get("root_agent_id"),
            token_budget=d.get("token_budget", 200_000),
            token_used=d.get("token_used", 0),
            root_max_turns=d.get("root_max_turns", 20),
            failure_counter=d.get("failure_counter", 0),
            failure_threshold=d.get("failure_threshold", 3),
            llm_name=d.get("llm_name", ""),
            llm_model=d.get("llm_model", ""),
            working_dir=d.get("working_dir", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            metadata=d.get("metadata", {}),
        )
